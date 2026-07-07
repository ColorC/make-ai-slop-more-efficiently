"""真·UI 端到端冒烟:Playwright 驱动真实浏览器,遍历每个视图,捕获控制台报错。

前置:后端已构建前端并在 :8330 提供服务(python -m omnicompany.packages.narrative_studio)。
用法:PYTHONPATH=src venv/Scripts/python.exe src/omnicompany/packages/narrative_studio/webui/e2e/smoke.py
退出码非 0 = 有失败视图或控制台 error。
"""

from __future__ import annotations

import sys
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8330"


def main() -> int:
    errors: list[str] = []
    console_errors: list[str] = []
    visited: list[str] = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        page.on("console", lambda m: console_errors.append(f"{m.type}: {m.text}")
                if m.type == "error" else None)
        page.on("pageerror", lambda e: console_errors.append(f"pageerror: {e}"))

        page.goto(BASE, wait_until="networkidle")
        # 应用壳渲染
        try:
            page.wait_for_selector(".topbar", timeout=10000)
        except Exception as e:
            print("FATAL: 应用壳未渲染:", e)
            browser.close()
            return 2

        # 遍历左侧每个导航项
        items = page.query_selector_all(".nav .item")
        labels = [it.inner_text().strip().split("\n")[0] for it in items]
        print(f"发现 {len(labels)} 个视图:{labels}")

        for i in range(len(labels)):
            items = page.query_selector_all(".nav .item")
            if i >= len(items):
                break
            label = labels[i]
            items[i].click()
            page.wait_for_timeout(400)
            center = page.query_selector(".center")
            txt = center.inner_text() if center else ""
            if "视图缺失" in txt or txt.strip() == "":
                errors.append(f"视图[{label}] 中栏空/缺失")
            else:
                visited.append(label)

        # 点一个角色卡,验证检查器能开(v2 角色视图标签=设定 · 人设)
        items = page.query_selector_all(".nav .item")
        for it in items:
            if "人设" in it.inner_text():
                it.click(); page.wait_for_timeout(400); break
        # 点卡内名字(卡内多处 stopPropagation,点名字是用户真实可靠路径)
        nm = page.query_selector(".center .card b") or page.query_selector(".center .card")
        if nm:
            nm.click()
            page.wait_for_timeout(400)
            if not page.query_selector(".inspector"):
                errors.append("点角色卡后检查器未出现")

        # 交互冒烟:开替换面板(Ctrl-H 路径用按钮触发)
        try:
            page.click("text=替换", timeout=3000)
            page.wait_for_timeout(300)
            if len(page.query_selector_all(".overlay input")) < 2:
                errors.append("替换面板未出现两个输入框")
            page.keyboard.press("Escape")
        except Exception as e:
            errors.append(f"替换面板打开失败: {e}")

        # 命令面板 Ctrl-P
        try:
            page.keyboard.press("Control+p")
            page.wait_for_timeout(250)
            if not page.query_selector(".palette"):
                errors.append("命令面板(Ctrl-P)未出现")
            page.keyboard.press("Escape")
        except Exception as e:
            errors.append(f"命令面板失败: {e}")

        # 演练:进入演练视图并点第一个按钮跑一次
        try:
            for it in page.query_selector_all(".nav .item"):
                if "演练" in it.inner_text():
                    it.click(); break
            page.wait_for_timeout(300)
            btns = page.query_selector_all(".center button")
            if btns:
                btns[0].click(); page.wait_for_timeout(700)
        except Exception as e:
            errors.append(f"演练交互失败: {e}")

        # 工具视图各点一次第一个按钮(分布/健康/完成度/对照/追踪/出处),抓运行期错误
        for vname in ["分布", "健康检查", "完成度", "版本对照", "贯穿追踪", "出处钻取"]:
            try:
                for it in page.query_selector_all(".nav .item"):
                    if vname in it.inner_text():
                        it.click(); break
                page.wait_for_timeout(350)
            except Exception as e:
                errors.append(f"视图[{vname}] 交互失败: {e}")

        # 截图存档
        page.screenshot(path="src/omnicompany/packages/narrative_studio/webui/e2e/smoke.png", full_page=True)
        browser.close()

    print(f"\n通过视图({len(visited)}):{visited}")
    if errors:
        print("\n视图错误:")
        for e in errors:
            print("  -", e)
    if console_errors:
        print("\n控制台 error:")
        for e in console_errors[:30]:
            print("  -", e)

    ok = not errors and not console_errors
    print("\n=== E2E", "PASS" if ok else "FAIL", "===")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
