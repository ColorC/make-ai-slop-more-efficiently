/* Read-only, device-side locator for the live Arknights BattleController. */

#define _GNU_SOURCE
#include <errno.h>
#include <inttypes.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/uio.h>
#include <unistd.h>

#define MAX_REGIONS 8192
#define MAX_HITS 512
#define MAX_REGION_BYTES (300ULL * 1024ULL * 1024ULL)
#define CHUNK_BYTES (8ULL * 1024ULL * 1024ULL)

typedef struct {
    uintptr_t start, end;
    int readable, writable, metadata, managed;
    char path[256];
} region_t;

typedef struct {
    region_t rows[MAX_REGIONS];
    size_t count;
} maps_t;

static ssize_t read_target(pid_t pid, uintptr_t address, void *buffer, size_t size) {
    struct iovec local = {.iov_base = buffer, .iov_len = size};
    struct iovec remote = {.iov_base = (void *)address, .iov_len = size};
    ssize_t result;
    do {
        result = process_vm_readv(pid, &local, 1, &remote, 1, 0);
    } while (result < 0 && errno == EINTR);
    return result;
}

static int starts_with(const char *value, const char *prefix) {
    return strncmp(value, prefix, strlen(prefix)) == 0;
}

static int load_maps(pid_t pid, maps_t *maps) {
    char filename[64], line[1024];
    snprintf(filename, sizeof(filename), "/proc/%ld/maps", (long)pid);
    FILE *handle = fopen(filename, "r");
    if (!handle) return -1;
    maps->count = 0;
    while (fgets(line, sizeof(line), handle) && maps->count < MAX_REGIONS) {
        unsigned long long start, end;
        char perms[5] = {0}, path[256] = {0};
        int fields = sscanf(line, "%llx-%llx %4s %*s %*s %*s %255[^\n]", &start, &end, perms, path);
        if (fields < 3 || end <= start) continue;
        char *trimmed = path;
        while (*trimmed == ' ' || *trimmed == '\t') ++trimmed;
        region_t *row = &maps->rows[maps->count++];
        memset(row, 0, sizeof(*row));
        row->start = (uintptr_t)start;
        row->end = (uintptr_t)end;
        row->readable = perms[0] == 'r';
        row->writable = perms[1] == 'w';
        snprintf(row->path, sizeof(row->path), "%s", trimmed);
        uint64_t size = row->end - row->start;
        int eligible = row->readable && row->writable && size <= MAX_REGION_BYTES &&
            !starts_with(row->path, "[anon:dalvik") && !starts_with(row->path, "[stack");
        row->metadata = eligible && starts_with(row->path, "/dev/zero");
        row->managed = eligible && (row->path[0] == '\0' || starts_with(row->path, "[anon:scudo"));
    }
    fclose(handle);
    return maps->count == MAX_REGIONS ? -2 : 0;
}

static int mapped(const maps_t *maps, uintptr_t value) {
    for (size_t i = 0; i < maps->count; ++i) {
        if (maps->rows[i].readable && maps->rows[i].start <= value && value < maps->rows[i].end) return 1;
    }
    return 0;
}

static void add_unique(uintptr_t *hits, size_t *count, uintptr_t value) {
    for (size_t i = 0; i < *count; ++i) if (hits[i] == value) return;
    if (*count < MAX_HITS) hits[(*count)++] = value;
}

static int contains(const uintptr_t *values, size_t count, uintptr_t value) {
    for (size_t i = 0; i < count; ++i) if (values[i] == value) return 1;
    return 0;
}

static void scan_literal(
    pid_t pid, const maps_t *maps, int metadata_only,
    const unsigned char *needle, size_t needle_size,
    uintptr_t *hits, size_t *hit_count, uint64_t *scanned
) {
    unsigned char *buffer = malloc(CHUNK_BYTES + 32);
    if (!buffer) return;
    for (size_t r = 0; r < maps->count; ++r) {
        const region_t *region = &maps->rows[r];
        if (!(metadata_only ? region->metadata : region->managed)) continue;
        for (uintptr_t at = region->start; at < region->end;) {
            size_t wanted = (size_t)((region->end - at) < CHUNK_BYTES ? (region->end - at) : CHUNK_BYTES);
            ssize_t got = read_target(pid, at, buffer, wanted);
            if (got > 0) {
                *scanned += (uint64_t)got;
                for (size_t i = 0; i + needle_size <= (size_t)got; ++i) {
                    if (memcmp(buffer + i, needle, needle_size) == 0) add_unique(hits, hit_count, at + i);
                }
            }
            if (wanted <= needle_size) break;
            at += wanted - (needle_size - 1);
        }
    }
    free(buffer);
}

static void scan_classes(
    pid_t pid, const maps_t *maps,
    const uintptr_t *names, size_t name_count,
    const uintptr_t *spaces, size_t space_count,
    uintptr_t *classes, size_t *class_count, uint64_t *scanned
) {
    unsigned char *buffer = malloc(CHUNK_BYTES + 32);
    if (!buffer) return;
    for (size_t r = 0; r < maps->count; ++r) {
        const region_t *region = &maps->rows[r];
        if (!region->managed) continue;
        for (uintptr_t at = region->start; at < region->end;) {
            size_t wanted = (size_t)((region->end - at) < CHUNK_BYTES ? region->end - at : CHUNK_BYTES);
            ssize_t got = read_target(pid, at, buffer, wanted);
            if (got > 0) {
                *scanned += (uint64_t)got;
                uintptr_t first = (8 - (at & 7)) & 7;
                for (size_t i = first; i + 16 <= (size_t)got; i += 8) {
                    uintptr_t name, space;
                    memcpy(&name, buffer + i, 8);
                    memcpy(&space, buffer + i + 8, 8);
                    if (contains(names, name_count, name) && contains(spaces, space_count, space) && at + i >= 16)
                        add_unique(classes, class_count, at + i - 16);
                }
            }
            if (wanted <= 15) break;
            at += wanted - 15;
        }
    }
    free(buffer);
}

static int read_u64(pid_t pid, uintptr_t address, uintptr_t *value) {
    return read_target(pid, address, value, 8) == 8 ? 0 : -1;
}

static int follow(pid_t pid, uintptr_t node, const size_t *chain, size_t count, uintptr_t *result) {
    for (size_t i = 0; i < count; ++i) {
        if (read_u64(pid, node + chain[i], &node) != 0 || node == 0) return -1;
    }
    *result = node;
    return 0;
}

static int validate_controller(
    pid_t pid, const maps_t *maps, uintptr_t address, uintptr_t klass,
    uintptr_t *scheduler, uintptr_t *cost_manager, uintptr_t *cost_status,
    uintptr_t *cost_timer, uintptr_t *pause_list
) {
    unsigned char body[0x300];
    if (read_target(pid, address, body, sizeof(body)) != sizeof(body)) return 0;
    uintptr_t body_class, enable_state;
    int32_t state, result, speed;
    float origin;
    memcpy(&body_class, body, 8);
    memcpy(scheduler, body + 0x30, 8);
    memcpy(cost_manager, body + 0x108, 8);
    memcpy(&state, body + 0x220, 4);
    memcpy(&result, body + 0x224, 4);
    memcpy(&speed, body + 0x228, 4);
    memcpy(&enable_state, body + 0x278, 8);
    memcpy(&origin, body + 0x280, 4);
    if (body_class != klass || body[0x200] != 1 || state < 0 || state > 32 || result < -8 || result > 32 ||
        speed < 0 || speed > 3 || origin != 1.0f || !*scheduler || !*cost_manager ||
        !mapped(maps, *scheduler) || !mapped(maps, *cost_manager) || !mapped(maps, enable_state)) return 0;
    uintptr_t back;
    if (read_u64(pid, *scheduler + 0x130, &back) != 0 || back != address) return 0;
    if (read_u64(pid, *cost_manager + 0x10, &back) != 0 || back != address) return 0;
    const size_t status_chain[] = {0x20, 0x10, 0x28};
    const size_t timer_chain[] = {0x28, 0x10, 0x28};
    const size_t pause_chain[] = {0x10, 0x10};
    if (follow(pid, *cost_manager, status_chain, 3, cost_status) != 0 ||
        follow(pid, *cost_manager, timer_chain, 3, cost_timer) != 0 ||
        follow(pid, enable_state, pause_chain, 2, pause_list) != 0) return 0;
    return mapped(maps, *cost_status) && mapped(maps, *cost_timer) && mapped(maps, *pause_list);
}

int main(int argc, char **argv) {
    if (argc != 2) {
        fprintf(stderr, "usage: %s PID\n", argv[0]);
        return 64;
    }
    pid_t pid = (pid_t)strtol(argv[1], NULL, 10);
    if (pid <= 0) return 64;
    maps_t *maps = calloc(1, sizeof(*maps));
    if (!maps || load_maps(pid, maps) != 0) {
        fprintf(stderr, "cannot parse target maps\n");
        return 70;
    }
    uintptr_t names[MAX_HITS] = {0}, spaces[MAX_HITS] = {0}, classes[MAX_HITS] = {0};
    size_t name_count = 0, space_count = 0, class_count = 0;
    uint64_t scanned = 0;
    const unsigned char class_name[] = "BattleController\0";
    const unsigned char class_space[] = "Torappu.Battle\0";
    scan_literal(pid, maps, 1, class_name, sizeof(class_name) - 1, names, &name_count, &scanned);
    scan_literal(pid, maps, 1, class_space, sizeof(class_space) - 1, spaces, &space_count, &scanned);
    if (!name_count || !space_count) {
        scan_literal(pid, maps, 0, class_name, sizeof(class_name) - 1, names, &name_count, &scanned);
        scan_literal(pid, maps, 0, class_space, sizeof(class_space) - 1, spaces, &space_count, &scanned);
    }
    if (!name_count || !space_count) {
        fprintf(stderr, "metadata strings not found\n");
        return 2;
    }
    scan_classes(pid, maps, names, name_count, spaces, space_count, classes, &class_count, &scanned);
    if (class_count != 1) {
        fprintf(stderr, "expected one class, found %zu\n", class_count);
        return 3;
    }
    uintptr_t klass = classes[0], controller = 0, scheduler = 0, cost_manager = 0;
    uintptr_t cost_status = 0, cost_timer = 0, pause_list = 0, region_start = 0, region_end = 0;
    size_t controller_count = 0;
    unsigned char *buffer = malloc(CHUNK_BYTES + 32);
    if (!buffer) return 70;
    for (size_t r = 0; r < maps->count; ++r) {
        const region_t *region = &maps->rows[r];
        if (!region->managed) continue;
        for (uintptr_t at = region->start; at < region->end;) {
            size_t wanted = (size_t)((region->end - at) < CHUNK_BYTES ? region->end - at : CHUNK_BYTES);
            ssize_t got = read_target(pid, at, buffer, wanted);
            if (got > 0) {
                scanned += (uint64_t)got;
                uintptr_t first = (8 - (at & 7)) & 7;
                for (size_t i = first; i + 8 <= (size_t)got; i += 8) {
                    uintptr_t value;
                    memcpy(&value, buffer + i, 8);
                    if (value != klass) continue;
                    uintptr_t candidate = at + i, s, cm, cs, ct, pl;
                    if (validate_controller(pid, maps, candidate, klass, &s, &cm, &cs, &ct, &pl)) {
                        ++controller_count;
                        controller = candidate; scheduler = s; cost_manager = cm;
                        cost_status = cs; cost_timer = ct; pause_list = pl;
                        region_start = region->start; region_end = region->end;
                    }
                }
            }
            if (wanted <= 7) break;
            at += wanted - 7;
        }
    }
    free(buffer);
    if (controller_count != 1) {
        fprintf(stderr, "expected one loaded controller, found %zu\n", controller_count);
        return 4;
    }
    printf("{\"pid\":%ld,\"controller_class\":\"0x%" PRIxPTR "\",\"controller\":\"0x%" PRIxPTR
           "\",\"scheduler\":\"0x%" PRIxPTR "\",\"cost_manager\":\"0x%" PRIxPTR
           "\",\"cost_status\":\"0x%" PRIxPTR "\",\"cost_timer\":\"0x%" PRIxPTR
           "\",\"pause_list\":\"0x%" PRIxPTR "\",\"controller_region\":[\"0x%" PRIxPTR
           "\",\"0x%" PRIxPTR "\"],\"scanned_bytes\":%" PRIu64 "}\n",
           (long)pid, klass, controller, scheduler, cost_manager, cost_status, cost_timer,
           pause_list, region_start, region_end, scanned);
    free(maps);
    return 0;
}
