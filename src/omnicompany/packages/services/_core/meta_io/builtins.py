# [OMNI] origin=ai-ide domain=services/_core/meta_io ts=2026-05-02T06:00:00Z type=service status=active agent=ai-ide-current
# [OMNI] summary="内置元 IO - 文件系统 / 网络 / git 三组常用元 IO 真实例参考"
# [OMNI] why="给现有 SingleToolRouter 子类 (GlobRouter / GrepRouter / ReadFileRouter / WriteFileRouter / WebFetchRouter) 声明 consumed/produced 用. 也是元 IO 形态参考"
# [OMNI] tags=meta_io,builtins,reference,foundation
# [OMNI] material_id="material:core.meta_io.builtin_registrar.implementation.py"
"""内置元 IO 注册.

启动时由 __init__.py 调 `register_builtin_meta_io()` 填到 META_IO_REGISTRY.

涵盖三组常用 I/O:
  - 文件系统 (fs): read_file_text / read_file_bytes / list_directory / stat_file
                    create_file / overwrite_file / append_to_file / delete_file
  - 网络 (http):  get / post / put / delete
  - git:          read_log / read_diff / commit_local / push_remote

业务工具如有特殊 IO (例 lark.read_doc / xlsm.read_sheet) 走 业务包的 meta_io.py 自己注册.
"""
from __future__ import annotations

from omnicompany.packages.services._core.meta_io.definitions import (
    MetaIO, MetaIOKind, StateCheck,
)
from omnicompany.packages.services._core.meta_io.registry import register_meta_io


def _fs_meta_io() -> list[MetaIO]:
    return [
        MetaIO(
            id="meta_io.fs.read_file_text",
            kind=MetaIOKind.READ,
            target_type="file",
            description=(
                "读取一份本地文本文件的全部内容, 按指定编码解码, 返回 str. "
                "前提: 文件存在 + 进程有读权限 + 编码声明跟实际一致."
            ),
            side_effect_scope="local_filesystem.read_only",
            is_atomic_semantic=True,
            state_check=StateCheck(
                precondition="文件路径存在 + 大小 < limit + 编码可识别",
                postcondition="进程内存增加 N 字节 / 文件 mtime 不变",
                invariant="文件锁状态不变 / 文件大小不变",
            ),
            tags=("fs", "read"),
        ),
        MetaIO(
            id="meta_io.fs.read_file_bytes",
            kind=MetaIOKind.READ,
            target_type="file",
            description="读取本地二进制文件, 返回 bytes. 不解码. 前提同 read_file_text 但不要求编码.",
            side_effect_scope="local_filesystem.read_only",
            tags=("fs", "read", "binary"),
        ),
        MetaIO(
            id="meta_io.fs.list_directory",
            kind=MetaIOKind.READ,
            target_type="file",
            description="列目录返回名字 + 类型 (file/dir) 列表. 不读文件内容. 前提: 路径存在且是目录.",
            side_effect_scope="local_filesystem.read_only",
            tags=("fs", "read", "list"),
        ),
        MetaIO(
            id="meta_io.fs.stat_file",
            kind=MetaIOKind.READ,
            target_type="file",
            description="取文件元数据 (大小 / mtime / 权限). 不读文件内容.",
            side_effect_scope="local_filesystem.read_only",
            tags=("fs", "read", "metadata"),
        ),
        MetaIO(
            id="meta_io.fs.create_file",
            kind=MetaIOKind.WRITE,
            target_type="file",
            description="创建一份新文件 + 写入指定 bytes/text. 前提: 父目录存在 + 路径不存在.",
            side_effect_scope="local_filesystem.write",
            state_check=StateCheck(
                precondition="父目录存在 + 路径不存在 + 进程有写权限",
                postcondition="文件存在 + 内容 = 指定值 + mtime 是当前时间",
            ),
            tags=("fs", "write", "create"),
        ),
        MetaIO(
            id="meta_io.fs.overwrite_file",
            kind=MetaIOKind.WRITE,
            target_type="file",
            description="覆盖已有文件全文. 不做差异更新, 内容直接替换.",
            side_effect_scope="local_filesystem.write",
            state_check=StateCheck(
                precondition="文件存在 + 进程有写权限",
                postcondition="内容 = 指定值 + mtime 是当前时间 + 大小 = len(指定值)",
            ),
            tags=("fs", "write", "overwrite"),
        ),
        MetaIO(
            id="meta_io.fs.append_to_file",
            kind=MetaIOKind.WRITE,
            target_type="file",
            description="在文件尾追加. 不读取或修改已有内容.",
            side_effect_scope="local_filesystem.write",
            tags=("fs", "write", "append"),
        ),
        MetaIO(
            id="meta_io.fs.delete_file",
            kind=MetaIOKind.WRITE,
            target_type="file",
            description="删一份文件. 前提: 文件存在 + 进程有删除权限.",
            side_effect_scope="local_filesystem.write",
            tags=("fs", "write", "delete"),
        ),
    ]


def _http_meta_io() -> list[MetaIO]:
    return [
        MetaIO(
            id="meta_io.http.get",
            kind=MetaIOKind.READ,
            target_type="api",
            description="单一 HTTP GET, 返回 body. 不修改远端状态. 重试视作幂等.",
            side_effect_scope="external_service.read_only",
            tags=("http", "read", "idempotent"),
        ),
        MetaIO(
            id="meta_io.http.post",
            kind=MetaIOKind.WRITE,
            target_type="api",
            description="单一 HTTP POST, 改远端状态. 重试可能造成重复操作 (幂等需 endpoint 支持).",
            side_effect_scope="external_service.write",
            tags=("http", "write"),
        ),
        MetaIO(
            id="meta_io.http.put",
            kind=MetaIOKind.MUTATE,
            target_type="api",
            description="单一 HTTP PUT, 设置远端资源 (语义幂等).",
            side_effect_scope="external_service.write",
            tags=("http", "write", "idempotent"),
        ),
        MetaIO(
            id="meta_io.http.delete",
            kind=MetaIOKind.WRITE,
            target_type="api",
            description="单一 HTTP DELETE, 删远端资源.",
            side_effect_scope="external_service.write",
            tags=("http", "write", "delete"),
        ),
    ]


def _git_meta_io() -> list[MetaIO]:
    return [
        MetaIO(
            id="meta_io.git.read_log",
            kind=MetaIOKind.READ,
            target_type="process",
            description="读 git log. 不改本地或远端状态. 输出范围 + 字段由参数决定.",
            side_effect_scope="local_git.read_only",
            tags=("git", "read"),
        ),
        MetaIO(
            id="meta_io.git.read_diff",
            kind=MetaIOKind.READ,
            target_type="process",
            description="读 git diff (work tree / staged / 范围 commits 之一).",
            side_effect_scope="local_git.read_only",
            tags=("git", "read"),
        ),
        MetaIO(
            id="meta_io.git.commit_local",
            kind=MetaIOKind.WRITE,
            target_type="process",
            description="git commit (本地仓库). 不推送到远端.",
            side_effect_scope="local_git.write",
            tags=("git", "write", "local"),
        ),
        MetaIO(
            id="meta_io.git.push_remote",
            kind=MetaIOKind.WRITE,
            target_type="network",
            description="git push 到远端. 改远端状态. 前提: 远端可达 + 本地有 commit.",
            side_effect_scope="git_remote.push",
            tags=("git", "write", "remote"),
        ),
    ]


def register_builtin_meta_io() -> int:
    """登记三组内置元 IO. 返回登记数."""
    count = 0
    for meta_io in (*_fs_meta_io(), *_http_meta_io(), *_git_meta_io()):
        register_meta_io(meta_io)
        count += 1
    return count
