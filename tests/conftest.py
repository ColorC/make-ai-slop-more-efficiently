"""pytest 配置文件 — 提供共享 fixture 和集成测试跳过逻辑。"""
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


@pytest.fixture
def cid():
    """Docker 容器 ID fixture — 需要 Docker 环境运行。

    运行方式: python tests/test_container_write.py --image <IMAGE>
    不通过 pytest 直接运行。
    """
    pytest.skip("Docker integration test — 需要 Docker 环境，请直接运行脚本")
