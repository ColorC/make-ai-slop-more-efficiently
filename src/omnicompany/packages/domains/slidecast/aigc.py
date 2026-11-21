# [OMNI] origin=ai-ide domain=slidecast ts=2026-06-21T00:00:00Z type=helper status=active
# [OMNI] summary="AIGC 氛围配图:复用 aigc-lab 项目卡管线(liclick gateway)给 deck 生封面/章节氛围图。'无文字'统一 field-manual 风格。"
# [OMNI] why="用户 2026-06-21:用 AIGC 图辅助配图可以(逻辑/氛围皆可)。精确数据走矢量,AIGC 只做氛围,不承载文字数字。"
# [OMNI] tags=slidecast,aigc,liclick,cover
"""AIGC 氛围图生成(liclick gateway,submit→poll→download,幂等)。"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path

from ._llm import safe_json

# field-manual 氛围风格后缀(硬约束"无文字")
STYLE = ("暗调抽象扁平插画, 美军野战手册(field manual)做旧氛围, "
         "米黄纸底配橄榄绿·暗红·卡其点缀, 做旧纸张颗粒质感, 构图简洁有呼吸感, "
         "杂志封面感, 画面中绝对没有任何文字或字母数字")
_SKILLHUB = "atlas-skillhub.cmd" if os.name == "nt" else "atlas-skillhub"
_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def _gw(args: list[str]) -> str:
    entry = None
    if os.name == "nt" and os.environ.get("APPDATA"):
        cand = Path(os.environ["APPDATA"]) / "npm" / "node_modules" / "@the_company" / "atlas-skillhub" / "dist" / "index.js"
        if cand.is_file():
            entry = cand
    cmd = ["node", str(entry), *args] if entry else [_SKILLHUB, *args]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", shell=False, creationflags=_NO_WINDOW,
                           timeout=120)
        return (r.stdout or "") + (r.stderr or "")
    except Exception as e:  # noqa: BLE001
        return f"__ERR__ {e}"


def _subject_for(title: str) -> str:
    """据标题产一句氛围主体(色调+居中象征物)。失败用通用兜底。"""
    res = safe_json(
        "为一页讲解封面想一句'氛围配图'主体描述(中文,只一句:色调 + 一个居中的抽象象征物 + 质感)。"
        "不要任何文字类元素。只输出 JSON {\"subject\": \"...\"}。",
        {"title": title}, {"type": "object", "properties": {"subject": {"type": "string"}}, "required": ["subject"]},
        caller="slidecast.aigc.subject", max_tokens=300, default=None)
    if isinstance(res, dict) and res.get("subject"):
        return str(res["subject"])
    return "墨绿与暗红色调, 一个悬浮的发光齿轮与纸页交织的抽象意象, 做旧质感"


def gen_cover(run_dir: Path, title: str, slug: str = "cover") -> Path | None:
    """生成封面氛围图 → run_dir/assets/<slug>-bg.png。幂等:已存在则跳过。"""
    run_dir = Path(run_dir)
    assets = run_dir / "assets"; assets.mkdir(parents=True, exist_ok=True)
    dest = assets / f"{slug}-bg.png"
    if dest.is_file() and dest.stat().st_size > 10000:
        return dest
    subject = _subject_for(title)
    args_json = json.dumps({
        "prompt": f"{subject}, {STYLE}",
        "model": "gpt-image-2",
        "extra_params": {"model": "gpt-image-2", "name": f"slidecast-{slug}",
                         "aspect_ratio": "16:9", "image_size": "1K", "quality": "high", "n": 1},
    }, ensure_ascii=False)
    out = _gw(["gateway", "call-tool", "--service", "liclick", "--tool", "generate_image", "--args", args_json])
    m = re.search(r"task_id:\s*([0-9a-f-]{8,})", out, re.I)
    if not m:
        print(f"  aigc submit 无 task_id: {out[:200]}")
        return None
    tid = m.group(1)
    for _ in range(12):
        st = _gw(["gateway", "call-tool", "--service", "liclick", "--tool", "get_task_status",
                  f"task_id={tid}", "task_type=image"])
        u = re.search(r"https://ai-assets[^\s\"\\]+", st)
        if u:
            r = subprocess.run(["curl", "-s", "-m", "90", "-o", str(dest), u.group(0)],
                               capture_output=True, text=True, creationflags=_NO_WINDOW)
            if dest.is_file() and dest.stat().st_size > 10000:
                return dest
        if "isError" in st and "true" in st.lower():
            break
        time.sleep(20)
    return None
