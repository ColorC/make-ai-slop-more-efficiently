# [OMNI] origin=claude-code domain=project_atlas ts=2026-06-21 type=config status=active
# [OMNI] summary="待收集的工作空间注册:space → 根路径 + 层级(auto 可自动勘察 / snapshot 受控漂移)。"
# [OMNI] why="跨空间收集要知道扫哪;P4(aiworkspace)结构会随 checkout 漂移,标 snapshot 另眼相待(评审 2)。"
# [OMNI] tags=project_atlas,spaces
"""project_atlas 收集的工作空间注册(轻量字典;后续可外置 spaces.yaml)。"""

from __future__ import annotations

# tier: auto = 结构规整、本地常驻、可自动勘察; snapshot = 受控/漂移,结果标 checkout 时点、不保证全
SPACES: dict[str, dict] = {
    "omnicompany": {"root": r"E:\WindowsWorkspace\omnicompany", "tier": "auto", "group": "omnicompany"},
    "quant-lab": {"root": r"E:\WindowsWorkspace\quant-lab", "tier": "auto", "group": "omnicompany"},
    "webworks": {"root": r"E:\WindowsWorkspace\webworks", "tier": "auto", "group": "omnicompany"},
    "overlay-shell": {"root": r"E:\WindowsWorkspace\overlay-shell", "tier": "auto", "group": "omnicompany"},
    "aiworkspace": {"root": r"D:\P4\main\AIWorkSpace", "tier": "snapshot", "group": "demogame"},
    "walker": {"root": r"E:\WindowsWorkspace\webworks\apps\walker-game", "tier": "auto", "group": "omnicompany"},
}


def resolve(space: str) -> dict | None:
    return SPACES.get(space)
