#!/usr/bin/env python3
# [OMNI] origin=ai-ide ts=2026-06-19 type=infra
# [OMNI] material_id="material:scripts.clean_env_smoke.portability_gate.py"
"""轻沙盒 · 无依赖环境验证 (clean-env smoke gate).

模拟"陌生人干净机": 在系统临时目录建全新隔离 venv, 只装本仓 wheel + 其
pyproject 声明依赖 (从 PyPI), 从一个中立临时 cwd 跑 omni 冒烟序列, 逐条
记录退出码 + 首段输出, 并主动检测"硬编码路径偷读开发机真仓"这类泄漏。

设计要点 (见 docs/plans/productization/[2026-06-19]CLEAN-ENV-SANDBOX/plan.md):
  - 不上 Docker。临时目录 + 隔离 venv (优先 uv venv, 退化 python -m venv)。
  - 不继承开发仓 .env / cwd / 路径: 子进程 env 清掉开发仓相关变量, cwd 用中立临时目录。
  - 只装 wheel + 声明依赖, 不装 dev/optional 组。
  - 本机有卡巴/EDR 可能拦子进程或限流; build/venv/pip 被拦或超时时如实记录, 不硬刚。

用法:
    python scripts/clean_env_smoke.py            # 在干净 venv 里跑完整冒烟门
    python scripts/clean_env_smoke.py --keep     # 跑完保留临时目录 (排错用)
    python scripts/clean_env_smoke.py --repo PATH  # 指定被测仓 (默认: 脚本所在仓根)

退出码: 全 pass=0; 有任一阻断失败=1; 环境/工具不可用导致跑不动=2。
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path


# ── 被测仓根 (脚本在 <repo>/scripts/ 下) ─────────────────────────────
def repo_root_default() -> Path:
    return Path(__file__).resolve().parents[1]


# ── 结构化结果累加 ──────────────────────────────────────────────────
class Report:
    def __init__(self) -> None:
        self.steps: list[dict] = []
        self.first_block: str | None = None  # 第一个真正阻断的失败点

    def add(self, name: str, ok: bool, detail: str = "", blocking: bool = True) -> bool:
        self.steps.append({"name": name, "ok": ok, "detail": detail, "blocking": blocking})
        status = "PASS" if ok else ("FAIL" if blocking else "WARN")
        print(f"  [{status}] {name}" + (f" — {detail}" if detail else ""))
        if not ok and blocking and self.first_block is None:
            self.first_block = name
        return ok

    def dump(self) -> None:
        print("\n" + "=" * 64)
        print("结构化结论 (clean-env smoke):")
        print("=" * 64)
        for s in self.steps:
            tag = "PASS" if s["ok"] else ("FAIL" if s["blocking"] else "WARN")
            print(f"  {tag:>4}  {s['name']}")
        print("-" * 64)
        if self.first_block:
            print(f"  第一个阻断失败点: {self.first_block}")
        else:
            print("  无阻断失败点 — 干净环境冒烟全过。")
        print("=" * 64)


def run(cmd: list[str], cwd: Path | None, env: dict, timeout: int = 600) -> subprocess.CompletedProcess:
    """跑子进程, 合并 stdout/stderr, 不抛异常 (由调用方判退出码)。"""
    return subprocess.run(
        cmd, cwd=str(cwd) if cwd else None, env=env,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout,
    )


def head(text: str, n: int = 12) -> str:
    lines = (text or "").splitlines()
    out = "\n".join("      | " + ln for ln in lines[:n])
    if len(lines) > n:
        out += f"\n      | ... (+{len(lines) - n} 行)"
    return out or "      | (无输出)"


def clean_env() -> dict:
    """构造一个尽量干净、不指向开发仓的子进程环境。

    关键: 不继承 OMNICOMPANY_DB_DIR / OMNI_WORKSPACE_ROOT / demogame_SDK_DIR 等
    会把命令导回开发仓的变量; 也不带 THE_COMPANY_API_KEY (验证不需要 LLM)。
    """
    keep = {}
    # 保留系统运行必需变量, 其余 OMNI*/demogame*/THE_COMPANY* 一律剔除。
    passthrough = {
        "PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "COMSPEC",
        "PATHEXT", "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE",
        "HOMEDRIVE", "HOMEPATH", "USERPROFILE", "APPDATA", "LOCALAPPDATA",
        "PYTHONUTF8", "SYSTEMDRIVE", "PROGRAMFILES", "PROGRAMDATA",
    }
    for k, v in os.environ.items():
        if k in passthrough:
            keep[k] = v
    keep["PYTHONUTF8"] = "1"
    keep["PYTHONIOENCODING"] = "utf-8"
    # 跳过 Guardian 启动自检 (它要 archmap.yaml, 干净环境没有 — 不该阻塞冒烟)。
    keep["OMNICOMPANY_SKIP_GUARDIAN_PRECHECK"] = "1"
    return keep


def main() -> int:
    ap = argparse.ArgumentParser(description="clean-env smoke gate for omnicompany")
    ap.add_argument("--repo", type=Path, default=repo_root_default())
    ap.add_argument("--keep", action="store_true", help="跑完保留临时目录")
    args = ap.parse_args()

    repo = args.repo.resolve()
    rep = Report()
    print(f"被测仓: {repo}")
    print(f"主机 python: {sys.executable} ({sys.version.split()[0]})")

    if not (repo / "pyproject.toml").exists():
        rep.add("locate-repo", False, f"{repo} 下没有 pyproject.toml")
        rep.dump()
        return 2

    sandbox = Path(tempfile.mkdtemp(prefix="omni_cleanenv_"))
    wheel_dir = sandbox / "wheel"
    venv_dir = sandbox / "venv"
    neutral_cwd = sandbox / "neutral_cwd"  # 中立 cwd: 不是开发仓
    wheel_dir.mkdir()
    neutral_cwd.mkdir()
    print(f"沙盒目录: {sandbox}")
    print(f"中立 cwd: {neutral_cwd}")
    print()

    base_env = clean_env()
    exit_code = 0

    try:
        # ── 步骤 1: 打 wheel ──────────────────────────────────────
        print("[1] 打 wheel")
        have_build = subprocess.run(
            [sys.executable, "-c", "import build"],
            capture_output=True, text=True,
        ).returncode == 0
        if have_build:
            wheel_cmd = [sys.executable, "-m", "build", "--wheel", "--outdir", str(wheel_dir), str(repo)]
            wheel_how = "python -m build --wheel"
        else:
            wheel_cmd = [sys.executable, "-m", "pip", "wheel", "--no-deps", "--wheel-dir", str(wheel_dir), str(repo)]
            wheel_how = "pip wheel --no-deps (build 未装, 降级)"
        try:
            r = run(wheel_cmd, cwd=neutral_cwd, env=base_env, timeout=600)
            wheels = list(wheel_dir.glob("omnicompany*.whl"))
            ok = r.returncode == 0 and bool(wheels)
            detail = f"{wheel_how} → rc={r.returncode}"
            if not ok:
                detail += f"\n{head(r.stdout + r.stderr, 15)}"
            rep.add("build-wheel", ok, detail)
            if not ok:
                rep.dump()
                return 1
            wheel_path = wheels[0]
            print(f"      wheel: {wheel_path.name}")
        except subprocess.TimeoutExpired:
            rep.add("build-wheel", False, f"{wheel_how} 超时 (可能被 EDR/卡巴拦截子进程)")
            rep.dump()
            return 2

        # ── 步骤 2: 建全新隔离 venv ────────────────────────────────
        # 优先 uv venv; 退化 python -m venv; 若本机 python 被精简掉 venv/ensurepip
        # 模块 (常见于公司机定制安装), 再退化到 virtualenv (自带 pip 种子, 不依赖 ensurepip)。
        print("\n[2] 建全新隔离 venv")
        uv = shutil.which("uv")
        have_venv = subprocess.run(
            [sys.executable, "-c", "import venv, ensurepip"],
            capture_output=True, text=True,
        ).returncode == 0
        have_virtualenv = subprocess.run(
            [sys.executable, "-c", "import virtualenv"],
            capture_output=True, text=True,
        ).returncode == 0
        try:
            if uv:
                r = run([uv, "venv", str(venv_dir)], cwd=neutral_cwd, env=base_env, timeout=180)
                venv_how = "uv venv"
            elif have_venv:
                r = run([sys.executable, "-m", "venv", str(venv_dir)], cwd=neutral_cwd, env=base_env, timeout=300)
                venv_how = "python -m venv (uv 未装, 降级)"
            elif have_virtualenv:
                r = run([sys.executable, "-m", "virtualenv", str(venv_dir)], cwd=neutral_cwd, env=base_env, timeout=300)
                venv_how = "virtualenv (本机 python 无 venv/ensurepip 模块, 再降级)"
            else:
                rep.add("create-venv", False,
                        "无可用建 venv 工具: uv 未装, python 缺 venv/ensurepip 模块, virtualenv 也未装")
                rep.dump()
                return 2
            vpy = venv_dir / "Scripts" / "python.exe"  # Windows
            if not vpy.exists():
                vpy = venv_dir / "bin" / "python"        # POSIX 兜底
            ok = r.returncode == 0 and vpy.exists()
            detail = f"{venv_how} → rc={r.returncode}"
            if not ok:
                detail += f"\n{head(r.stdout + r.stderr, 12)}"
            rep.add("create-venv", ok, detail)
            if not ok:
                rep.dump()
                return 2
        except subprocess.TimeoutExpired:
            rep.add("create-venv", False, "venv 创建超时 (可能被 EDR/卡巴限流)")
            rep.dump()
            return 2

        # ── 步骤 3: 只装 wheel + 声明依赖 (从 PyPI), 不装 dev/optional ──
        print("\n[3] 装 wheel + 声明依赖 (不装 dev/optional 组)")
        if uv:
            pip_cmd = [uv, "pip", "install", "--python", str(vpy), str(wheel_path)]
            pip_how = "uv pip install <wheel>"
        else:
            pip_cmd = [str(vpy), "-m", "pip", "install", "--disable-pip-version-check", str(wheel_path)]
            pip_how = "pip install <wheel> (拉声明依赖)"
        try:
            r = run(pip_cmd, cwd=neutral_cwd, env=base_env, timeout=600)
            ok = r.returncode == 0
            detail = f"{pip_how} → rc={r.returncode}"
            if not ok:
                detail += f"\n{head(r.stdout + r.stderr, 18)}"
            rep.add("install-wheel", ok, detail)
            if not ok:
                low = (r.stdout + r.stderr).lower()
                if "could not find a version" in low or "no matching distribution" in low or "connection" in low or "timed out" in low:
                    print("      (装包失败疑似网络/PyPI 不可达或被拦, 属环境问题而非仓问题)")
                rep.dump()
                return 1 if ok is False else 2
        except subprocess.TimeoutExpired:
            rep.add("install-wheel", False, "pip install 超时 (PyPI 不可达 / EDR 限流)")
            rep.dump()
            return 2

        # ── 步骤 4: 从中立 cwd 跑冒烟序列 ───────────────────────────
        print("\n[4] 从中立 cwd 跑冒烟序列")
        vomni_py = [str(vpy), "-m", "omnicompany.cli.main"]  # 不依赖 console_script PATH

        smoke = [
            ("omni --help", vomni_py + ["--help"]),
            ("omni health", vomni_py + ["health"]),
            # 第 3 条挑纯本地、不需要 LLM key、不碰 P4/collab platform的命令。
            # refs catalog 读本地引用目录 (语义检索可关), 候选; 退化到 --help 列表自检。
            ("omni refs --help", vomni_py + ["refs", "--help"]),
        ]
        outputs: dict[str, str] = {}
        for label, cmd in smoke:
            try:
                r = run(cmd, cwd=neutral_cwd, env=base_env, timeout=180)
                out = r.stdout + r.stderr
                outputs[label] = out
                ok = r.returncode == 0
                rep.add(f"smoke: {label}", ok, f"rc={r.returncode}")
                print(head(out, 10))
                # --help 崩了说明导入链断 (典型: 某子模块在 import 期缺依赖)。
                if not ok and "ModuleNotFoundError" in out:
                    import re
                    m = re.search(r"No module named ['\"]([\w.]+)['\"]", out)
                    if m:
                        print(f"      ⇒ 缺模块: {m.group(1)} (该依赖未在 pyproject [project].dependencies 声明)")
            except subprocess.TimeoutExpired:
                outputs[label] = ""
                rep.add(f"smoke: {label}", False, "超时")

        # ── 步骤 4b: 声明依赖完整性 (照出未声明的依赖, 如缺 fastapi) ──
        # 顶层冒烟命令 (--help/health) 走惰性导入, 踩不到 dashboard 子树, 所以
        # 即便缺 fastapi 也能跑通。要让门真照出"缺 fastapi", 直接探子模块导入。
        print("\n[4b] 声明依赖完整性 (import 子模块, 照出未声明依赖)")
        probes = [
            # (子模块, 它在哪个 optional 组里 → 漏进了 dependencies 没有)
            ("omnicompany.dashboard.app", "dashboard (fastapi/uvicorn)"),
        ]
        for mod, note in probes:
            try:
                r = run([str(vpy), "-c", f"import {mod}"], cwd=neutral_cwd, env=base_env, timeout=120)
                out = r.stdout + r.stderr
                outputs[f"import {mod}"] = out
                ok = r.returncode == 0
                miss = ""
                if not ok and "ModuleNotFoundError" in out:
                    import re
                    m = re.search(r"No module named ['\"]([\w.]+)['\"]", out)
                    if m:
                        miss = m.group(1)
                detail = f"import {mod} → rc={r.returncode}"
                if not ok:
                    detail += f"; 缺 {miss or '?'} (属 {note}, 未进 [project].dependencies)"
                # 非阻断 (WARN): 这是 plan 留给"可移植性"business 修的已知缺口,
                # 本门职责是照出并如实报告, 不拦冒烟整体通过。
                rep.add(f"dep-complete: {mod}", ok, detail, blocking=False)
            except subprocess.TimeoutExpired:
                rep.add(f"dep-complete: {mod}", False, "import 探针超时", blocking=False)

        # ── 步骤 4c: 根硬编码探针 (会踩"原仓根硬编码"的命令) ───────
        # debt/guardian 这类命令历史上把默认 --root 写死成
        # "C:/workspace/omnicompany"。修好后默认根应由
        # omni_workspace_root() 从安装位置解析, 干净环境里绝不该指回开发机真仓。
        # 这些命令在干净环境通常"找不到 REGISTRY.md/git 仓"而非 0 退出, 属预期,
        # 故按"是否泄漏开发机真仓路径"判定, 不以退出码作阻断。
        print("\n[4c] 根硬编码探针 (debt/guardian 默认根是否仍指向开发机真仓)")
        root_probes = [
            # guardian patrol 会把解析出的默认 root 直接回显 (root: <X>),
            # 是最直观的"默认根指哪"证据。
            ("omni guardian patrol", vomni_py + ["guardian", "patrol", "--json-out"]),
            # debt list 默认从 <默认根>/docs/tech_debt/REGISTRY.md 读;
            # 默认根错指开发机就会偷到真仓数据。
            ("omni debt list", vomni_py + ["debt", "list", "--json"]),
        ]
        for label, cmd in root_probes:
            try:
                r = run(cmd, cwd=neutral_cwd, env=base_env, timeout=180)
                out = r.stdout + r.stderr
                outputs[label] = out
                # 判据 = 输出里不含开发机真仓路径 (退出码非 0 不算失败:
                # 干净环境本就没有 REGISTRY.md / git 仓)。
                repo_str_l = str(repo).replace("\\", "/").lower()
                low = out.replace("\\", "/").lower()
                leaked_here = ("C:/workspace/" in low) or (repo_str_l in low)
                rep.add(f"root-probe: {label}", not leaked_here,
                        f"rc={r.returncode}; " +
                        ("默认根指向开发机真仓 (泄漏!)" if leaked_here
                         else "默认根未指向开发机真仓 (随安装位置解析)"))
                print(head(out, 8))
            except subprocess.TimeoutExpired:
                outputs[label] = ""
                rep.add(f"root-probe: {label}", False, "超时 (可能被 EDR 拦)", blocking=False)

        # ── 步骤 5: 硬编码路径泄漏检测 ─────────────────────────────
        print("\n[5] 硬编码路径泄漏检测 (是否偷读开发机真仓)")
        repo_str = str(repo).replace("\\", "/").lower()
        leak_markers = ["C:/workspace/", repo_str]
        leaked: list[str] = []
        for label, out in outputs.items():
            low = out.replace("\\", "/").lower()
            for marker in leak_markers:
                if marker in low:
                    # 找出泄漏行做证据
                    for ln in out.splitlines():
                        if marker in ln.replace("\\", "/").lower():
                            leaked.append(f"{label}: {ln.strip()}")
                            break
                    break
        leak_ok = not leaked
        detail = "干净环境未输出开发机真仓路径" if leak_ok else \
            "命令在干净环境里输出了开发机真仓路径 (硬编码泄漏):\n" + \
            "\n".join("      ! " + x for x in leaked[:6])
        # 泄漏是 plan 预期要照出的已知问题, 标为阻断 (它就是门要拦的东西)。
        rep.add("no-hardcoded-path-leak", leak_ok, detail)

        rep.dump()
        # 退出码: 任一阻断失败 → 1
        if rep.first_block is not None:
            exit_code = 1
        return exit_code

    finally:
        if args.keep:
            print(f"\n(--keep) 临时目录保留: {sandbox}")
        else:
            shutil.rmtree(sandbox, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
