"""pytest 配置文件 — 提供共享 fixture 和集成测试跳过逻辑。"""
from functools import wraps

import pytest

# 归档目录：不参与普通测试收集 (_archive/_legacy 是改名前 omnifactory 包的旧测试, 收集即报错)
collect_ignore_glob = [
    "_graveyard/**",
    "_archive/**",
    "_legacy/**",
    # Retired contract tests whose target modules were removed or migrated.
    "domains/voxelcraft/entity/entity_walk3_smoke_test.py",
    "domains/voxelcraft/mechanism/mechanism_walk2_smoke_test.py",
    "domains/demogame/test_demogame_*.py",
    "domains/software_engineering/test_sw_chain.py",
    "domains/software_engineering/test_sw_chain_e2e.py",
    "domains/software_engineering/test_sw_design.py",
    "domains/software_engineering/test_sw_implement.py",
    "domains/software_engineering/test_sw_tdd.py",
    "packages/services/spec_verification_test.py",
    "pipeline/test_real_dag_features.py",
]


#: 会从执行者那条真会话继承进来的"我是谁"变量。
#: identity.resolve_active_trace_id 的优先级: OMNI_CC_TRACE_ID > CODEX_THREAD_ID > OMNI_CC_PTY_ID。
_SESSION_IDENTITY_ENV = (
    "OMNI_CC_TRACE_ID",
    "CODEX_THREAD_ID",
    "OMNI_CC_PTY_ID",
    "OMNI_AGENT_PROVIDER",
    "OMNI_HOOK_PROVIDER",
)


@pytest.fixture(autouse=True)
def isolate_session_identity(tmp_path_factory, monkeypatch):
    """测试不许读、更不许写真实的会话身份台账。

    测试是在一条真实 CLI 会话里跑的, 会继承它的身份变量; identity 解析于是把
    执行者当成被测会话, `omni plan use` 这类命令就直接改到真台账上。
    2026-08-10 实测: 跑一次 tests/cli 把执行者会话的 active_plan 改成了测试里的
    假计划(`_infra/diagnosis/[2026-05-02]BETA`), 同时 3 条断言因为读到真 trace_id
    而失败 —— 同一个根因既污染现场又制造假红。
    """
    for key in _SESSION_IDENTITY_ENV:
        monkeypatch.delenv(key, raising=False)
    try:
        from omnicompany.packages.services._core.identity import resolver
    except Exception:  # noqa: BLE001 - 不让身份隔离拖垮无关测试的收集
        return
    root = tmp_path_factory.mktemp("session-identity")
    monkeypatch.setattr(resolver, "_active_file", lambda: root / "cc_session_active.json")
    monkeypatch.setattr(resolver, "_bindings_file", lambda: root / "cc_session_bindings.json")


@pytest.fixture(autouse=True)
def isolate_runtime_writes(tmp_path, monkeypatch):
    """Keep default runtime writes in each test's own temporary directory."""
    from omnicompany.packages.services._core.guardian import sentinel

    runtime_root = tmp_path / "runtime-state"
    monkeypatch.setenv("OMNICOMPANY_DB_DIR", str(runtime_root))
    original = sentinel.write_activity_ts

    @wraps(original)
    def write_activity_ts(*args, **kwargs):
        if not args and "root" not in kwargs:
            kwargs["root"] = runtime_root
        return original(*args, **kwargs)

    monkeypatch.setattr(sentinel, "write_activity_ts", write_activity_ts)
    return runtime_root


@pytest.fixture
def cid():
    """Docker 容器 ID fixture — 需要 Docker 环境运行。

    运行方式: python tests/test_container_write.py --image <IMAGE>
    不通过 pytest 直接运行。
    """
    pytest.skip("Docker integration test — 需要 Docker 环境，请直接运行脚本")
