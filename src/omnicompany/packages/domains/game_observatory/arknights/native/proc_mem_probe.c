/*
 * Read-only, request-driven process memory sampler for the Arknights observer.
 *
 * The helper never opens a writable target handle.  In request mode each byte
 * received on stdin triggers one process_vm_readv() call over the immutable
 * block list supplied on the command line.  In fixed-interval mode the same
 * operation repeats at the requested cadence.  The sampled blocks are emitted
 * in argument order with no framing; the caller knows the exact frame length.
 */

#define _GNU_SOURCE

#include <errno.h>
#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <poll.h>
#include <sys/uio.h>
#include <unistd.h>

static int parse_block(const char *text, uintptr_t *address, size_t *size) {
    char *separator = strchr(text, ':');
    char *address_end = NULL;
    char *size_end = NULL;
    unsigned long long parsed_address;
    unsigned long long parsed_size;

    if (separator == NULL || separator == text || separator[1] == '\0') {
        return -1;
    }
    errno = 0;
    parsed_address = strtoull(text, &address_end, 0);
    if (errno != 0 || address_end != separator) {
        return -1;
    }
    errno = 0;
    parsed_size = strtoull(separator + 1, &size_end, 0);
    if (errno != 0 || *size_end != '\0' || parsed_size == 0 ||
        parsed_size > SIZE_MAX) {
        return -1;
    }
    *address = (uintptr_t)parsed_address;
    *size = (size_t)parsed_size;
    return 0;
}

static int write_all(int fd, const void *buffer, size_t length) {
    const unsigned char *cursor = (const unsigned char *)buffer;
    while (length > 0) {
        ssize_t written = write(fd, cursor, length);
        if (written < 0 && errno == EINTR) {
            continue;
        }
        if (written <= 0) {
            return -1;
        }
        cursor += (size_t)written;
        length -= (size_t)written;
    }
    return 0;
}

static int sample_once(
    pid_t pid,
    struct iovec *local,
    struct iovec *remote,
    size_t block_count,
    unsigned char *frame,
    size_t total_size
) {
    ssize_t received;
    do {
        received = process_vm_readv(
            pid, local, block_count, remote, block_count, 0
        );
    } while (received < 0 && errno == EINTR);
    if (received < 0) {
        fprintf(stderr, "process_vm_readv failed: %s\n", strerror(errno));
        return -1;
    }
    if ((size_t)received != total_size) {
        fprintf(
            stderr,
            "short process_vm_readv: got %zd of %zu bytes\n",
            received,
            total_size
        );
        return -1;
    }
    if (write_all(STDOUT_FILENO, frame, total_size) != 0) {
        fprintf(stderr, "stdout write failed: %s\n", strerror(errno));
        return -1;
    }
    return 0;
}

static int wait_interval_or_stdin_eof(unsigned long interval_us) {
    struct pollfd input = {
        .fd = STDIN_FILENO,
        .events = POLLIN | POLLHUP,
        .revents = 0,
    };
    int timeout_ms = (int)((interval_us + 999UL) / 1000UL);
    int result;

    do {
        result = poll(&input, 1, timeout_ms);
    } while (result < 0 && errno == EINTR);
    if (result < 0) {
        fprintf(stderr, "stdin poll failed: %s\n", strerror(errno));
        return -1;
    }
    if (result == 0) {
        return 0;
    }
    if ((input.revents & (POLLHUP | POLLERR | POLLNVAL)) != 0) {
        return 1;
    }
    if ((input.revents & POLLIN) != 0) {
        unsigned char ignored;
        ssize_t received;
        do {
            received = read(STDIN_FILENO, &ignored, 1);
        } while (received < 0 && errno == EINTR);
        return received == 0 ? 1 : 0;
    }
    return 0;
}

int main(int argc, char **argv) {
    pid_t pid;
    char *pid_end = NULL;
    char *interval_end = NULL;
    unsigned long interval_us = 0;
    int first_block_arg = 2;
    int emit_pid = 0;
    size_t block_count;
    size_t total_size = 0;
    unsigned char *frame;
    struct iovec *local;
    struct iovec *remote;
    size_t index;
    unsigned char request;

    if (argc >= 2 && strncmp(argv[1], "--interval-us=", 14) == 0) {
        errno = 0;
        interval_us = strtoul(argv[1] + 14, &interval_end, 10);
        if (errno != 0 || *interval_end != '\0' || interval_us == 0) {
            fprintf(stderr, "invalid interval: %s\n", argv[1]);
            return 64;
        }
        first_block_arg = 3;
    }
    if (argc > first_block_arg - 1 &&
        strcmp(argv[first_block_arg - 1], "--emit-pid") == 0) {
        emit_pid = 1;
        first_block_arg += 1;
    }
    if (argc < first_block_arg + 1) {
        fprintf(
            stderr,
            "usage: %s [--interval-us=N] [--emit-pid] PID "
            "ADDRESS:SIZE [ADDRESS:SIZE ...]\n",
            argv[0]
        );
        return 64;
    }
    errno = 0;
    pid = (pid_t)strtol(argv[first_block_arg - 1], &pid_end, 10);
    if (errno != 0 || *pid_end != '\0' || pid <= 0) {
        fprintf(stderr, "invalid pid: %s\n", argv[first_block_arg - 1]);
        return 64;
    }

    block_count = (size_t)(argc - first_block_arg);
    local = calloc(block_count, sizeof(*local));
    remote = calloc(block_count, sizeof(*remote));
    if (local == NULL || remote == NULL) {
        fprintf(stderr, "cannot allocate iovec tables\n");
        return 70;
    }

    for (index = 0; index < block_count; ++index) {
        uintptr_t address;
        size_t size;
        if (parse_block(argv[index + first_block_arg], &address, &size) != 0 ||
            SIZE_MAX - total_size < size) {
            fprintf(stderr, "invalid block: %s\n", argv[index + first_block_arg]);
            return 64;
        }
        remote[index].iov_base = (void *)address;
        remote[index].iov_len = size;
        total_size += size;
    }

    frame = malloc(total_size);
    if (frame == NULL) {
        fprintf(stderr, "cannot allocate %zu-byte frame\n", total_size);
        return 70;
    }
    total_size = 0;
    for (index = 0; index < block_count; ++index) {
        local[index].iov_base = frame + total_size;
        local[index].iov_len = remote[index].iov_len;
        total_size += remote[index].iov_len;
    }

    if (emit_pid) {
        char header[64];
        int header_length = snprintf(
            header, sizeof(header), "OMNI_PROC_MEM_PROBE_PID %ld\n", (long)getpid()
        );
        if (header_length <= 0 || (size_t)header_length >= sizeof(header) ||
            write_all(STDOUT_FILENO, header, (size_t)header_length) != 0) {
            fprintf(stderr, "cannot emit helper pid\n");
            return 74;
        }
    }

    if (interval_us > 0) {
        for (;;) {
            int wait_result;
            if (sample_once(pid, local, remote, block_count, frame, total_size) != 0) {
                return 74;
            }
            wait_result = wait_interval_or_stdin_eof(interval_us);
            if (wait_result < 0) {
                return 74;
            }
            if (wait_result > 0) {
                return 0;
            }
        }
    }

    while (read(STDIN_FILENO, &request, 1) == 1) {
        if (sample_once(pid, local, remote, block_count, frame, total_size) != 0) {
            return 74;
        }
    }
    return 0;
}
