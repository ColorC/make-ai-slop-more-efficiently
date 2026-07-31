# [OMNI] origin=claude-code domain=runtime/buses ts=2026-04-23T00:00:00Z type=infrastructure
# [OMNI] material_id="material:runtime.buses.workspace_scope_definition_and_loader.py"
"""Workspace · bus 读写范围声明 (用户 2026-04-23 明示).

**概念** (用户原话):
> workspace = bus 读写范围. 对 team_builder 来说, 首先要确认自己的 workspace. 一般来说,
> 其写入限定在特定 package 的特定范围内 (取决于 package 的单独的架构设计, 每个 package
> 都要对自己的子域进行 arch 固定, 防止架构漂移污染), 但读取范围应该非常广, 暂时不做限制.

**核心语义**:
- **写紧**: 写入紧限在声明的 prefix 集合内 (超出 → BusRejection)
- **读宽**: 读取默认无限制 (为 agent 探针留空间)
- **bash_cwd 紧**: subprocess 的 cwd 也紧限
- 每个 package 声明自己的 Workspace (通常在 `<pkg>/.omni/workspace.py` 或 manifest)

**与 agent-first 哲学的耦合**:
- workspace 先搭完 (完整信息库), 再让 agent 作探针 — agent 不能写到 workspace 外
- workspace 是 package 架构固定的唯一权威 (防架构漂移污染)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


# 哨兵值: 表示"无限制"读取 (默认).
READ_ANY = ("<any>",)


@dataclass(frozen=True)
class Workspace:
    """bus 的读写范围声明.

    Example:
      # team_builder 的 workspace: 只能写入自己的 package + generated 目录, 读 ANY
      ws = Workspace(
          name="team_builder",
          write_prefixes=(
              "C:/workspace/omnicompany/src/omnicompany/packages/services/team_builder/",
              "C:/workspace/omnicompany/data/services/team_builder/",
          ),
          read_prefixes=READ_ANY,
          bash_cwd_prefixes=(
              "C:/workspace/omnicompany/",
          ),
      )
    """

    name: str
    """workspace 名字 (通常是 package 名)."""

    write_prefixes: tuple[str, ...] = field(default_factory=tuple)
    """写入允许的路径前缀. 空集 = 拒绝所有写入."""

    read_prefixes: tuple[str, ...] = READ_ANY
    """读取允许的前缀. 默认 READ_ANY (= 无限制). 本版不检查, 预留."""

    bash_cwd_prefixes: tuple[str, ...] = field(default_factory=tuple)
    """BashBus cwd 允许前缀. 空集 = 拒绝所有 subprocess."""

    def __post_init__(self):
        # 规范化: 小写 + 使用正斜杠 + 末尾补斜杠
        object.__setattr__(self, "write_prefixes", tuple(self._normalize(p) for p in self.write_prefixes))
        object.__setattr__(self, "bash_cwd_prefixes", tuple(self._normalize(p) for p in self.bash_cwd_prefixes))
        # read_prefixes READ_ANY 不规范化
        if self.read_prefixes != READ_ANY:
            object.__setattr__(self, "read_prefixes", tuple(self._normalize(p) for p in self.read_prefixes))

    @staticmethod
    def _normalize(prefix: str) -> str:
        p = str(prefix).replace("\\", "/").lower()
        if not p.endswith("/"):
            p = p + "/"
        return p

    def allows_write(self, path: Path | str) -> bool:
        norm = self._path_norm(path)
        return any(norm.startswith(p) for p in self.write_prefixes)

    def allows_read(self, path: Path | str) -> bool:
        if self.read_prefixes == READ_ANY:
            return True
        norm = self._path_norm(path)
        return any(norm.startswith(p) for p in self.read_prefixes)

    def allows_bash_cwd(self, path: Path | str) -> bool:
        norm = self._path_norm(path)
        return any(norm.startswith(p) for p in self.bash_cwd_prefixes)

    @staticmethod
    def _path_norm(path: Path | str) -> str:
        p = Path(path)
        if not p.is_absolute():
            p = p.resolve()
        # 统一末尾加 / · 让 startswith 能匹配 "prefix == path" 的情况
        # (如 prefix="c:/ws/team_builder/" 要能匹配自身作为 cwd 的情况)
        s = str(p).replace("\\", "/").lower()
        if not s.endswith("/"):
            s = s + "/"
        return s


# ==================== 预定义 workspaces ====================


def _project_root() -> Path:
    """定位 omnicompany 项目根 (有 src/omnicompany/ 的目录)."""
    cursor = Path.cwd()
    for _ in range(8):
        if (cursor / "src" / "omnicompany").is_dir():
            return cursor
        if cursor.parent == cursor:
            break
        cursor = cursor.parent
    # fallback: 假设当前目录
    return Path.cwd()


def load_workspace(yaml_path: str | Path) -> Workspace:
    """从 `.omni/workspace.yaml` 加载 Workspace 声明.

    设计说明: `.omni/` 目录不是 Python 包, Python 不能直接 `import workspace`,
    所以 workspace 用 yaml 声明 + 本 loader 加载. 规范副本可被 Guardian rule 扫描,
    人类也可读.

    Args:
      yaml_path: 绝对或相对路径. 绝对路径直接读; 相对路径相对项目根.

    yaml schema:
      name: str
      write_prefixes: list[str]  # 相对项目根的路径, 自动展开为绝对
      read_prefixes: "READ_ANY" | list[str]
      bash_cwd_prefixes: list[str]  # 空字符串 "" = 项目根
    """
    import yaml

    yaml_path = Path(yaml_path)
    if not yaml_path.is_absolute():
        yaml_path = (_project_root() / yaml_path).resolve()
    data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))

    root = _project_root()

    def _expand(rel: str) -> str:
        if rel == "" or rel == ".":
            return str(root)
        return str(root / rel)

    write_prefixes = tuple(_expand(p) for p in (data.get("write_prefixes") or ()))
    bash_cwd_prefixes = tuple(_expand(p) for p in (data.get("bash_cwd_prefixes") or ()))
    rp = data.get("read_prefixes", "READ_ANY")
    read_prefixes = READ_ANY if rp in ("READ_ANY", "any", "*") else tuple(_expand(p) for p in rp)

    return Workspace(
        name=data["name"],
        write_prefixes=write_prefixes,
        read_prefixes=read_prefixes,
        bash_cwd_prefixes=bash_cwd_prefixes,
    )


def for_package(package_path: str, *, name: str | None = None, extra_write: tuple[str, ...] = ()) -> Workspace:
    """便捷: 为一个 package 构造标准 workspace.

    Args:
      package_path: 相对项目根的 package 路径 (例 "packages/services/team_builder").
      name: workspace 名字, 默认取 package 名.
      extra_write: 额外允许写入的绝对前缀 (例 `data/services/team_builder/`).

    写入: `<root>/src/omnicompany/<package_path>/` + `<root>/data/<service_sub>/` + extra
    读取: ANY (agent 探针需要宽读)
    bash_cwd: 整个 project_root
    """
    root = _project_root()
    pkg_abs = root / "src" / "omnicompany" / package_path
    data_sub = package_path.replace("packages/services/", "services/").replace("packages/domains/", "domains/")
    data_abs = root / "data" / data_sub

    ws_name = name or package_path.rsplit("/", 1)[-1]
    write_prefixes = (
        str(pkg_abs),
        str(data_abs),
        *extra_write,
    )
    return Workspace(
        name=ws_name,
        write_prefixes=write_prefixes,
        read_prefixes=READ_ANY,
        bash_cwd_prefixes=(str(root),),
    )
