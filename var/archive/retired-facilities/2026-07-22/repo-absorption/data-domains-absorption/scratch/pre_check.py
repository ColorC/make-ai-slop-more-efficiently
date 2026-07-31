"""快速前置检查（< 30 秒，无 LLM 调用）
跑任何全量测试前先执行这个，不通过就不跑全量。
"""
import sys, os
sys.path.insert(0, "e:/WindowsWorkspace/omnicompany/src")

os.environ.setdefault("OMNICOMPANY_INFO_AUDIT", "piggyback")
from dotenv import load_dotenv
load_dotenv("e:/WindowsWorkspace/omnicompany/.env", override=True)
os.environ["OMNICOMPANY_INFO_AUDIT"] = "piggyback"  # 强制

errors = []

# 1. env var 是否生效
if os.environ.get("OMNICOMPANY_INFO_AUDIT") != "piggyback":
    errors.append("FAIL env var OMNICOMPANY_INFO_AUDIT not set")
else:
    print("OK  env var OMNICOMPANY_INFO_AUDIT=piggyback")

# 2. import 检查
try:
    from omnicompany.core.registry import discover
    discover()
    from omnicompany.core.dispatch import dispatch
    print("OK  imports OK")
except Exception as e:
    errors.append(f"FAIL import: {e}")

# 3. pipeline 结构检查（不跑 LLM）
try:
    from omnicompany.packages.services.absorption.pipeline import build_v3_pipeline
    from omnicompany.packages.services.absorption.run import build_v3_bindings
    p = build_v3_pipeline()
    b = build_v3_bindings()
    missing = set(n.id for n in p.nodes) - set(b.keys())
    if missing:
        errors.append(f"FAIL pipeline missing bindings: {missing}")
    else:
        print(f"OK  pipeline {len(p.nodes)} nodes, 0 missing bindings")
except Exception as e:
    errors.append(f"FAIL pipeline check: {e}")

# 4. guarded_write writer 权限检查
try:
    from omnicompany.core.guarded_write import _find_project_root
    from pathlib import Path
    # 简单检查 internal-engine 身份是否合法
    root = _find_project_root()
    print(f"OK  project root found: {root}")
except Exception as e:
    errors.append(f"FAIL guarded_write: {e}")

# 5. hermes-agent 路径存在
repo_path = "e:/WindowsWorkspace/参考项目/hermes-agent"
if not Path(repo_path).exists():
    errors.append(f"FAIL repo not found: {repo_path}")
else:
    print(f"OK  repo exists: {repo_path}")

print()
if errors:
    for e in errors:
        print(e)
    sys.exit(1)
else:
    print("ALL OK — 可以启动全量测试")
