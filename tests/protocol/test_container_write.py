"""测试 ToolExecutor 容器内文件写入的可靠性

核心验证目标：
  1. _container_write 写入的内容 == 读回的内容（字节级一致）
  2. str_replace_editor 编辑后，docker exec git diff 能检测到改动
  3. mock LLM agent 完整跑一轮后，git diff 非空

使用方式：
  python tests/test_container_write.py [--image IMAGE_NAME]

前提：Docker 可用，SWE-bench 容器镜像已 pull。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import os
import time
from pathlib import Path

# 让 omnicompany 可导入
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

IMAGE_PREFIX = "ghcr.io/epoch-research/swe-bench.eval.x86_64"
DEFAULT_TASK = "django__django-14534"


def start_container(image: str) -> str:
    """启动容器，返回 container id"""
    r = subprocess.run(
        ["docker", "run", "-d", "--rm", "-e", "PYTHONUNBUFFERED=1", image, "sleep", "300"],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, f"容器启动失败: {r.stderr}"
    return r.stdout.strip()


def stop_container(cid: str):
    subprocess.run(["docker", "stop", "-t", "5", cid], capture_output=True, timeout=30)


def git_diff_in_container(cid: str) -> str:
    """获取容器内 /testbed 的 git diff"""
    r = subprocess.run(
        ["docker", "exec", cid, "git", "-C", "/testbed", "diff"],
        capture_output=True, text=True,
    )
    return r.stdout


def test_container_write_readback(cid: str):
    """测试1：写入 → 读回 → 内容一致"""
    from omnicompany.runtime.exec.tool_executor import ToolExecutor

    executor = ToolExecutor(timeout=30, container_id=cid)

    # 读取原始文件
    original, err = executor._container_read("/testbed/django/forms/boundfield.py")
    assert original is not None, f"读取失败: {err}"
    print(f"  原始文件: {len(original)} chars, md5={hashlib.md5(original.encode()).hexdigest()[:8]}")

    # 修改内容
    marker = f"# TEST_WRITE_VERIFICATION_{int(time.time())}"
    modified = original + f"\n{marker}\n"

    # 写入
    write_err = executor._container_write("/testbed/django/forms/boundfield.py", modified)
    assert not write_err, f"写入失败: {write_err}"

    # 读回
    readback, err = executor._container_read("/testbed/django/forms/boundfield.py")
    assert readback is not None, f"读回失败: {err}"

    # 比较
    assert readback == modified, (
        f"写入/读回不一致！写入 {len(modified)} chars, 读回 {len(readback)} chars. "
        f"First diff at char {next((i for i,(a,b) in enumerate(zip(modified, readback)) if a!=b), min(len(modified),len(readback)))}"
    )

    print(f"  写入/读回一致: {len(readback)} chars ✅")

    # 恢复原始
    executor._container_write("/testbed/django/forms/boundfield.py", original)


def test_str_replace_produces_git_diff(cid: str):
    """测试2：str_replace_editor 编辑后，git diff 能检测到改动"""
    from omnicompany.runtime.exec.tool_executor import ToolExecutor

    executor = ToolExecutor(timeout=30, container_id=cid)

    # 确认初始状态 git diff 为空
    diff_before = git_diff_in_container(cid)
    assert not diff_before.strip(), f"初始 git diff 应为空，但得到: {diff_before[:200]}"
    print(f"  初始 git diff: 空 ✅")

    # 用 str_replace_editor 做一个真实编辑
    # 读取文件找到 id_for_label 方法
    content, _ = executor._container_read("/testbed/django/forms/boundfield.py")
    assert content is not None

    # 找到一个唯一的可替换字符串（BoundWidget 类的 id_for_label）
    old_str = "        return 'id_%s_%s' % (self.data['name'], self.data['index'])"
    assert content.count(old_str) == 1, f"old_str 出现 {content.count(old_str)} 次，需要唯一"

    new_str = "        return 'id_%s_%s' % (self.data['name'], self.data['index'])  # MODIFIED BY TEST"
    result = executor.execute_editor({
        "command": "str_replace",
        "path": "/testbed/django/forms/boundfield.py",
        "old_str": old_str,
        "new_str": new_str,
    })
    assert "has been edited" in result, f"编辑失败: {result}"
    print(f"  str_replace 编辑: 成功 ✅")

    # 检查 git diff
    diff_after = git_diff_in_container(cid)
    assert diff_after.strip(), f"编辑后 git diff 仍为空！这是 _container_write 的 bug"
    assert "MODIFIED BY TEST" in diff_after, f"git diff 中没有我们的修改: {diff_after[:300]}"
    print(f"  git diff 检测到改动: {len(diff_after)} bytes ✅")

    # 恢复
    executor.execute_editor({
        "command": "str_replace",
        "path": "/testbed/django/forms/boundfield.py",
        "old_str": new_str,
        "new_str": old_str,
    })

    # 确认恢复
    diff_restored = git_diff_in_container(cid)
    assert not diff_restored.strip(), f"恢复后 git diff 应为空: {diff_restored[:200]}"
    print(f"  恢复后 git diff: 空 ✅")


def test_mock_agent_edit_flow(cid: str):
    """测试3：模拟完整 agent 编辑流程（mock LLM）

    模拟 agent 的典型行为：
      1. view 文件
      2. str_replace 编辑
      3. bash 跑测试
    然后验证 meta_evolve 的 git diff 采集能看到改动。
    """
    from omnicompany.runtime.exec.tool_executor import ToolExecutor

    executor = ToolExecutor(timeout=120, container_id=cid, task_id=DEFAULT_TASK)

    # Step 1: Agent views the file
    view_result = executor.execute_editor({
        "command": "view",
        "path": "/testbed/django/forms/boundfield.py",
    })
    assert "id_for_label" in view_result, "view 应该能看到 id_for_label"
    print(f"  [mock step 1] view: 看到文件 ✅")

    # Step 2: Agent does str_replace (模拟真实修复)
    edit_result = executor.execute_editor({
        "command": "str_replace",
        "path": "/testbed/django/forms/boundfield.py",
        "old_str": "        return 'id_%s_%s' % (self.data['name'], self.data['index'])",
        "new_str": "        return self.data['attrs'].get('id', 'id_%s_%s' % (self.data['name'], self.data['index']))",
    })
    assert "has been edited" in edit_result, f"编辑失败: {edit_result}"
    print(f"  [mock step 2] str_replace: 编辑成功 ✅")

    # Step 3: Agent runs test via bash
    test_result = executor.execute_shell(
        "cd /testbed && PYTHONPATH=/testbed:$PYTHONPATH "
        "/opt/miniconda3/envs/testbed/bin/python /testbed/tests/runtests.py "
        "forms_tests.tests.test_forms.FormsTestCase.test_iterable_boundfield_select "
        "2>&1 | tail -5"
    )
    print(f"  [mock step 3] test: {test_result.strip()}")

    # === 关键验证：meta_evolve 的 git diff 采集 ===
    diff = git_diff_in_container(cid)
    print(f"\n  === GIT DIFF ({len(diff)} bytes) ===")
    if diff.strip():
        print(f"  {diff[:500]}")
        print(f"\n  ✅ git diff 非空！agent 的编辑被正确持久化到容器文件系统")
    else:
        print(f"  ❌ git diff 为空！这就是 20 代 django 任务全部 NO_DIFF 的根因")
        print(f"     _container_write 写入了但 git 看不到，或者写入内容和原文件相同")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="测试容器内文件写入可靠性")
    parser.add_argument("--image", default=f"{IMAGE_PREFIX}.{DEFAULT_TASK}:latest")
    parser.add_argument("--container", default=None, help="复用已有容器 ID（跳过启动/停止）")
    args = parser.parse_args()

    cid = args.container
    own_container = cid is None

    if own_container:
        print(f"启动容器: {args.image}")
        cid = start_container(args.image)
        print(f"容器 ID: {cid[:12]}")
    else:
        print(f"复用容器: {cid[:12]}")

    try:
        print("\n--- Test 1: 写入/读回一致性 ---")
        test_container_write_readback(cid)

        print("\n--- Test 2: str_replace → git diff ---")
        test_str_replace_produces_git_diff(cid)

        print("\n--- Test 3: Mock Agent 完整编辑流程 ---")
        test_mock_agent_edit_flow(cid)

        print("\n" + "=" * 50)
        print("所有测试通过 ✅")
        print("agent 100% 有能力在容器内做持久化编辑，git diff 能正确检测")

    except AssertionError as e:
        print(f"\n❌ 测试失败: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n💥 异常: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        if own_container:
            print(f"\n停止容器: {cid[:12]}")
            stop_container(cid)


if __name__ == "__main__":
    main()
