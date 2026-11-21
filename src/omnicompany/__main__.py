# [OMNI] origin=claude-code ts=2026-04-08T03:23:35Z
# [OMNI] material_id="material:omnicompany.cli_entry.agent_launcher.py"
"""omnicompany CLI — LAP + EventBus 驱动的 Agent

用法:
    python -m omnicompany "列出当前目录下的文件"
    python -m omnicompany  (交互模式)
"""

import asyncio
import sys

from dotenv import load_dotenv


async def _run(task: str):
    from omnicompany.runtime.agent.agent_loop import run_agent
    return await run_agent(task)


def main():
    load_dotenv()

    if len(sys.argv) > 1:
        task = " ".join(sys.argv[1:])
    else:
        print("omnicompany — LAP Agent")
        print("输入任务 (Ctrl+D 结束):")
        try:
            task = sys.stdin.read().strip()
        except KeyboardInterrupt:
            print("\n退出")
            return

    if not task:
        print("未提供任务")
        return

    print(f"\n> 任务: {task}\n")

    try:
        result = asyncio.run(_run(task))
        print(f"\n{'=' * 60}")
        try:
            print(result)
        except UnicodeEncodeError:
            sys.stdout.buffer.write((str(result) + "\n").encode("utf-8", errors="replace"))
    except KeyboardInterrupt:
        print("\n中断")
    except Exception as e:
        print(f"\n错误: {e}")
        raise


if __name__ == "__main__":
    main()
