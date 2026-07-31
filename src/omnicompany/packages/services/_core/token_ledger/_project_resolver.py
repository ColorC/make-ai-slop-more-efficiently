# [OMNI] origin=claude-code ts=2026-07-03T00:00:00Z type=module summary="token_ledger 项目归属解析器: 只认 E:\WindowsWorkspace\workspace.yaml 的 entries 名单(闭集即白名单)+ D:\P4\main\AIWorkSpace 特判, 其余(含 Temp/workdir/new-chat 类 cwd)一律返回 None 落未关联桶" why="overnight-run.md 第六节'首轮验收打回后的硬化'㈡: 默认 resolver 太宽松(取 cwd 末段当项目名), 生产数据里未关联桶恒空是归属造假的实证; 改为闭集白名单, 读法容错(文件不存在时全部落未关联)" tags=token-ledger,project-resolver,whitelist,unlinked
"""token_ledger 项目归属解析器 —— 闭集白名单, 禁猜。

铁律(overnight-run.md 第六节"首轮验收打回后的硬化"㈡):
    项目归属只认 E:\\WindowsWorkspace\\workspace.yaml 的 entries 名单(闭集即白名单)。
    - cwd 在 E:\\WindowsWorkspace\\<登记名>\\ 之下 -> project = 登记名。
    - cwd 在 D:\\P4\\main\\AIWorkSpace 之下(特判, 不在 workspace.yaml entries 里,
      是另一台工作区的已知例外) -> project = "AIWorkSpace"。
    - 其余一律返回 None(调用方按约定落"未关联"桶) —— 包括 Temp 目录、workdir 之类的
      cron/agent 临时工作目录、new-chat 等未登记路径。不允许对着 cwd 猜一个像样的项目名。

"未关联桶恒空 = 归属造假"(打回原话)——这条 resolver 必须在生产数据里真的产出
未关联样本, 而不是把一切都努力关联上。
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Iterable

import yaml

_DEFAULT_WORKSPACE_YAML = Path("E:/WindowsWorkspace/workspace.yaml")
_DEFAULT_WORKSPACE_ROOT = "E:/WindowsWorkspace"
_AIWORKSPACE_ROOT = "D:/P4/main/AIWorkSpace"
_AIWORKSPACE_PROJECT_NAME = "AIWorkSpace"


def load_workspace_entry_names(workspace_yaml_path: Path | None = None) -> list[str]:
    """读 workspace.yaml 的 entries[].name 列表(闭集白名单)。

    容错: 文件不存在/解析失败/字段缺失一律返回空列表(不抛异常) —— 调用方据此让
    resolver 全部落未关联桶, 而不是崩溃或编造名单。
    """
    path = workspace_yaml_path or _DEFAULT_WORKSPACE_YAML
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return []
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        return []
    if not isinstance(data, dict):
        return []
    entries = data.get("entries")
    if not isinstance(entries, list):
        return []
    names: list[str] = []
    for entry in entries:
        if isinstance(entry, dict):
            name = entry.get("name")
            if isinstance(name, str) and name:
                names.append(name)
    return names


def _normalize(path_str: str) -> str:
    return (path_str or "").replace("\\", "/").rstrip("/")


def build_workspace_project_resolver(
    entry_names: Iterable[str] | None = None,
    *,
    workspace_yaml_path: Path | None = None,
) -> Callable[[str], str | None]:
    """构造闭集白名单 project_resolver(cwd) -> str | None。

    Args:
        entry_names: 显式注入的白名单(测试用假名单); 省略时从 workspace_yaml_path
            (默认真实 E:\\WindowsWorkspace\\workspace.yaml)读取。
        workspace_yaml_path: 仅在 entry_names 省略时生效, 供测试注入假路径。

    白名单是闭集: 不在名单里的顶层目录段一律不关联(返回 None), 由调用方落未关联桶。
    """
    if entry_names is None:
        entry_names = load_workspace_entry_names(workspace_yaml_path)
    names = list(entry_names)
    # 按名字长度降序, 避免短名字前缀误吞长名字(闭集内目前无此风险, 但保守处理)
    names_sorted = sorted(set(names), key=len, reverse=True)

    def resolve(cwd: str) -> str | None:
        normalized = _normalize(cwd)
        if not normalized:
            return None
        lower = normalized.lower()

        aiworkspace_root_lower = _AIWORKSPACE_ROOT.lower()
        if lower == aiworkspace_root_lower or lower.startswith(aiworkspace_root_lower + "/"):
            return _AIWORKSPACE_PROJECT_NAME

        workspace_root_lower = _DEFAULT_WORKSPACE_ROOT.lower()
        if not (lower == workspace_root_lower or lower.startswith(workspace_root_lower + "/")):
            return None  # 不在 E:\WindowsWorkspace 之下, 也不是 AIWorkSpace 特判 -> 未关联

        rest = normalized[len(_DEFAULT_WORKSPACE_ROOT):].lstrip("/")
        if not rest:
            return None
        top_segment = rest.split("/", 1)[0]
        for name in names_sorted:
            if top_segment == name:
                return name
        return None  # 白名单闭集之外, 一律未关联(禁猜)

    return resolve


__all__ = ["build_workspace_project_resolver", "load_workspace_entry_names"]
