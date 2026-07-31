# [OMNI] origin=claude-code domain=omnicompany/packages/services/_core/registry ts=2026-07-03T00:00:00Z type=infra agent=claude-code
# [OMNI] material_id="material:services._core.registry.external_mounts.mount_loader.py"
# [OMNI] summary="外部域挂载机制: 读 config/external_mounts.yaml 登记表 + 各业务仓根 .omni-mount.yaml 清单, 动态把外部业务仓的管线注册进 core.registry._REGISTRY。三层公开接口: discover(纯发现,无副作用)/register(真注册,单条失败隔离)/list_mounted_repo_roots(供 guardian 巡逻排除)。挂载管线数据落业务仓自己的 data/, 不落 omnicompany。名字/前缀/重名三重校验; 入口懒加载, 调用时把业务仓根加入 sys.path。"
# [OMNI] why="overnight-run.md 批7首件开工锚(外部域挂载机制): 一次只动一个业务, 挂载登记表与端口表/账本表同一套声明式登记范式; 业务仓自带清单命名刻意避开 domains.yaml; 借 _register_g2_yaml_teams() 的'读声明式清单动态注册'形状, 新增'外部路径进导入路径'层(全仓无先例); 每个挂载仓暴露唯一顶层包, 重名拒绝; 巡逻散落文件扫描排除已挂载仓路径。"
# [OMNI] tags=registry,external-mounts,pipeline,semantic-os,batch7
"""外部域挂载机制 — 把独立业务仓(如 quant-lab)的管线挂进 omnicompany 注册表。

## 定位

omnicompany 的管线注册表 (:mod:`omnicompany.core.registry`) 历来只认仓内绝对导入
路径。批7首件("业务外迁")要把自有的独立业务仓(quant-lab / webworks / …)当作
**外部域**挂进来: 业务仓的代码/数据/内容真源都留在它自己那里, omnicompany 只
通过一份声明式清单知道"这个仓暴露了哪几条管线", 从而 ``omni run <mount>.<x>``
能调到它。

## 三份契约文件

1. **登记表** ``config/external_mounts.yaml`` (与 ``config/ports.yaml`` /
   ``config/ledgers.yaml`` 同一套声明式登记范式) —— 声明"哪些业务仓被挂载":

   .. code-block:: yaml

       mounts:
         - path: "C:/workspace/quant-lab"
           owner: quant-lab
           note: "只读状态入口; 重型日更管线只声明不执行(18:00 有计划任务在跑)"

2. **业务仓自带清单** ``<repo_root>/.omni-mount.yaml`` (文件名刻意避开
   ``domains.yaml``, 见任务锚) —— 业务仓声明"我暴露哪几条管线":

   .. code-block:: yaml

       mount_name: quant_lab            # 挂载名 = 仓根下真实存在的顶层包目录名
       pipelines:
         - name: "quant_lab.status"     # 管线名必须以 mount_name 开头(前缀约束)
           description: "读最新管线日期与产物健康(便宜只读)"
           module: "quant_lab.adapter"  # 业务仓内的导入路径
           function: "build_status_pipeline"

## 三层公开接口

- :func:`discover_external_mounts` —— 纯发现层, 无 import/注册副作用, 供注册层
  与巡逻排除层复用。
- :func:`register_external_pipelines` —— 真注册: 把业务仓根加入 ``sys.path`` →
  import 入口模块 → 包成 :class:`~omnicompany.core.registry.PipelineEntry` →
  调 :func:`~omnicompany.core.registry.register`。单条清单项失败不影响其余
  (每条独立 try/except, 与 ``_register_g2_yaml_teams`` 同一原则)。
- :func:`list_mounted_repo_roots` —— 仅返回"清单文件存在"的仓根绝对路径列表,
  供 guardian ``fs_scanner_worker`` 排除散落文件扫描(错误样本㊄的依赖接口)。

## 三重校验(拒绝注册的三种情形)

- 清单文件缺失 → discover 标 ``ok=False``, 注册层跳过(``skipped``), 其余仓正常。
- 入口模块 import 失败 → 只该条进 ``skipped``, 仓内既有管线数量不减。
- 管线名未带挂载名前缀, 或撞了已注册的内部管线名 → 进 ``rejected``, 绝不静默
  覆盖内部管线。

## 数据落点

挂载管线的 ``PipelineEntry.default_db_dir`` 指向**业务仓自己的**
``<repo_root>/data/mounts/<mount_name>`` (绝对路径), 不经过 omnicompany 的
``core.config.resolve_db_dir`` —— 挂载管线运行产生的数据落业务仓自己的数据目录,
不落 omnicompany (错误样本㊅)。

## 真实调度路径(2026-07-03 修复)

早期实现把业务函数(如 ``build_status_pipeline``)直接当 ``PipelineEntry.build_team``
注册 —— 单元测试里直接调 ``entry.build_team()`` 能拿到裸 dict(掩盖了问题), 但真实
调度入口 ``core.dispatch.dispatch()``(``omni run <name>``)对默认 ``engine="teamrunner"``
会把 ``build_team()`` 的返回值当 ``TeamSpec`` 传给 ``TeamRunner``, 裸 dict 没有
``.nodes`` 属性, 炸 ``AttributeError: 'dict' object has no attribute 'nodes'``。

修复: 挂载管线一律注册为 ``engine="event"``(复用全仓既有的事件型引擎形状, 与
gddecon 各管线同款, 见 ``_register_sedimented_event_teams``), 用
:class:`_MountFunctionWorker` 把业务函数包成单节点 worker
(``build_team()`` 返回 ``[worker]``), 经 ``core.dispatch._run_event_pipeline()``
→ :class:`~omnicompany.packages.services._core.omnicompany.material_dispatcher.MaterialDispatcher`
调度。业务函数执行异常时 worker 返回 FAIL Verdict, 只该条挂载不产出 sink,
不抛出异常炸调度器, 不影响同 job 内其他 worker / 其他管线。

锚点权威: docs/plans/[2026-07-02]SEMANTIC-OS-MAP/overnight-run.md
"批7首件 开工锚(外部域挂载机制)"。
"""
from __future__ import annotations

import importlib
import inspect
import logging
import sys
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)


# ── 模块级常量(唯一权威, 禁各模块各写一份字面量) ────────────────────────────────

MOUNT_MANIFEST_FILENAME = ".omni-mount.yaml"
"""业务仓根清单文件名的唯一权威常量。"""


def _omni_repo_root() -> Path:
    """omnicompany 仓库根 — 委托到 core.config 的唯一权威解析入口。"""
    from omnicompany.core.config import omni_workspace_root

    return omni_workspace_root()


EXTERNAL_MOUNTS_REGISTRY_PATH: Path = _omni_repo_root() / "config" / "external_mounts.yaml"
"""挂载登记表模块级默认路径 = <omnicompany_repo_root>/config/external_mounts.yaml。

与 config/ports.yaml、config/ledgers.yaml 同一套声明式登记范式。
测试与集成可经 ``registry_path=`` 参数注入 tmp_path 下的假登记表, 不碰本文件。
"""


# ── 内部工具 ──────────────────────────────────────────────────────────────────

def _load_yaml(path: Path) -> dict[str, Any] | None:
    """安全读取一个 yaml 文件。文件不存在返回 None; 解析失败也返回 None(容错)。"""
    if not path.exists():
        return None
    try:
        import yaml

        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception as e:  # noqa: BLE001
        logger.warning("external_mounts: 无法解析 %s: %s", path, e)
        return None


def _read_registry(registry_path: Path | None) -> list[dict[str, Any]]:
    """读登记表, 返回 mounts 列表(每项含 path/owner/note)。

    登记表文件不存在 → 空列表(全新仓/尚未登记任何挂载), 不抛异常。
    """
    path = registry_path if registry_path is not None else EXTERNAL_MOUNTS_REGISTRY_PATH
    data = _load_yaml(Path(path))
    if not data:
        return []
    mounts = data.get("mounts")
    if not isinstance(mounts, list):
        return []
    out: list[dict[str, Any]] = []
    for item in mounts:
        if isinstance(item, dict) and item.get("path"):
            out.append(item)
    return out


def _mount_db_dir(repo_path: Path, mount_name: str) -> str:
    """挂载管线的数据目录 = 业务仓自己的 data/mounts/<mount_name> (绝对路径)。

    刻意返回绝对路径指向业务仓内部, 从而不经过 omnicompany core.config.resolve_db_dir,
    保证挂载管线运行产生的数据落业务仓自己的数据目录, 不落 omnicompany/data (错误样本㊅)。
    """
    return str((repo_path / "data" / "mounts" / mount_name).resolve())


# ── 真实调度路径适配层 ───────────────────────────────────────────────────────
# 根因(2026-07-03 修复): 旧实现把 build_fn (业务函数, 如
# quant_lab_mount.adapter.build_status_pipeline) 直接当 PipelineEntry.build_team
# 注册。PipelineEntry.build_team 的协议是"() -> TeamSpec"(teamrunner 引擎默认值),
# 但 build_fn 直接执行业务逻辑返回裸 dict —— 单元测试里直接调 entry.build_team() 能
# 拿到数据(掩盖了问题), 但真实调度入口 core.dispatch.dispatch() 对 engine="teamrunner"
# 会拿 build_team() 的返回值当 TeamSpec 传给 TeamRunner, 裸 dict 没有 .nodes 属性,
# 炸 AttributeError。
#
# 修法: 复用仓内既有的 "engine=event" 形状(见 _register_sedimented_event_teams 及
# gddecon 各管线) —— 这是全仓已有先例、改动最小的路径, 不给调度器加任何挂载特例分支。
# engine="event" 下 PipelineEntry.build_team() 的协议是 "() -> list[Router]"
# (worker 清单), 经 core.dispatch._run_event_pipeline() → MaterialDispatcher 跑。
# 因此把 build_fn 包一层单节点 Worker: FORMAT_IN = 合成的入口 material id,
# FORMAT_OUT = 合成的 sink material id, run() 内部原样调用 build_fn 并透传输入参数。
def _mount_entry_material(pipeline_name: str) -> str:
    """合成的事件入口 material id(engine=event 用, 每条挂载管线独占, 不会与仓内其余
    管线的 material 撞名, 因为带 'mount.' 前缀 + 管线全名)。"""
    return f"mount.{pipeline_name}.request"


def _mount_sink_material(pipeline_name: str) -> str:
    """合成的事件产出(sink) material id。"""
    return f"mount.{pipeline_name}.result"


class _MountFunctionWorker:
    """把外部挂载业务函数包成单节点 event-engine worker(不继承重量级 Worker 基类,
    只需满足 Router 的最小运行时契约: FORMAT_IN/FORMAT_OUT 类属性 + run(input_data)
    返回 Verdict, MaterialDispatcher 用 duck-typing 调度, 无需真正继承 ABC)。

    run() 原样调用业务函数, 参数透传:
        - 业务函数无参(如 build_status_pipeline()) → 直接调用, 忽略输入 payload。
        - 业务函数有参 → 用 input payload 里同名的键做 kwargs 透传;
          调用失败(TypeError 等)不静默吞, 落 FAIL Verdict 带清晰 diagnosis,
          MaterialDispatcher 遇 FAIL 只是不产出 sink, 不会让整个调度器崩溃
          (dispatch() 外层仍正常返回 {"sinks": [], ...})。
    """

    def __init__(
        self, *, pipeline_name: str, mount_name: str, build_fn: Callable[..., Any],
    ) -> None:
        self.FORMAT_IN = _mount_entry_material(pipeline_name)
        self.FORMAT_OUT = _mount_sink_material(pipeline_name)
        self.DESCRIPTION = f"外部挂载业务函数薄包装: {mount_name}.{pipeline_name}"
        self._pipeline_name = pipeline_name
        self._mount_name = mount_name
        self._build_fn = build_fn

    def run(self, input_data: Any):
        from omnicompany.protocol.anchor import Verdict, VerdictKind

        payload: dict[str, Any] = {}
        if isinstance(input_data, dict):
            inner = input_data.get(self.FORMAT_IN)
            payload = dict(inner) if isinstance(inner, dict) else dict(input_data)

        try:
            sig = inspect.signature(self._build_fn)
            if sig.parameters:
                kwargs = {k: v for k, v in payload.items() if k in sig.parameters}
                result = self._build_fn(**kwargs)
            else:
                result = self._build_fn()
        except Exception as e:  # noqa: BLE001
            # 坏挂载在真实调度路径下同样被隔离: 本 worker FAIL, 不抛出异常炸调度器,
            # 也不影响同一 job 里的其他 worker / 其他管线。
            return Verdict(
                kind=VerdictKind.FAIL,
                confidence=1.0,
                output={"error": str(e)},
                diagnosis=(
                    f"[external_mount] {self._mount_name}.{self._pipeline_name} "
                    f"执行失败: {e}"
                ),
            )

        output = result if isinstance(result, dict) else {"_value": result}
        return Verdict(
            kind=VerdictKind.PASS,
            confidence=1.0,
            output=output,
            diagnosis=f"[external_mount] {self._mount_name}.{self._pipeline_name} 执行完成",
        )


# ── 发现层(纯函数, 无 import/注册副作用) ───────────────────────────────────────

def discover_external_mounts(*, registry_path: Path | None = None) -> list[dict[str, Any]]:
    """读登记表, 对每个登记路径尝试读 ``.omni-mount.yaml``, 逐条产出发现结果。

    纯发现层: 不做任何 import / 注册动作, 供注册层与巡逻排除层复用。

    Returns:
        list[dict], 每条含:
            mount_name: str            # 清单里声明的挂载名(仓根顶层包名)
            repo_path: Path            # 登记表里的仓根绝对路径
            ok: bool                   # 是否可用于注册
            reason: str | None         # ok=False 时的失败原因
            pipelines: list[dict]      # 清单声明的管线列表(name/module/function/description)
    """
    results: list[dict[str, Any]] = []
    for entry in _read_registry(registry_path):
        repo_path = Path(str(entry["path"]))
        result: dict[str, Any] = {
            "mount_name": None,
            "repo_path": repo_path,
            "ok": False,
            "reason": None,
            "pipelines": [],
        }

        if not repo_path.exists():
            result["reason"] = f"repo_path_not_found: {repo_path}"
            result["mount_name"] = repo_path.name
            results.append(result)
            continue

        manifest_path = repo_path / MOUNT_MANIFEST_FILENAME
        if not manifest_path.exists():
            # 错误样本㊀: 登记了路径但仓里没有清单文件 → 跳过并警告(其余照常)。
            result["reason"] = f"no_manifest: {repo_path.name} 缺少 {MOUNT_MANIFEST_FILENAME}"
            result["mount_name"] = repo_path.name
            results.append(result)
            continue

        manifest = _load_yaml(manifest_path)
        if not manifest:
            result["reason"] = f"manifest_unreadable: {manifest_path}"
            result["mount_name"] = repo_path.name
            results.append(result)
            continue

        mount_name = manifest.get("mount_name")
        pipelines = manifest.get("pipelines") or []
        result["mount_name"] = mount_name
        result["pipelines"] = pipelines if isinstance(pipelines, list) else []

        if not mount_name or not isinstance(mount_name, str):
            result["reason"] = f"missing_mount_name: {manifest_path}"
            results.append(result)
            continue

        # 挂载名必须是仓根下真实存在的顶层包目录 —— 否则 import 必失败, 提前拦。
        if not (repo_path / mount_name).is_dir():
            result["reason"] = (
                f"mount_name_not_a_toplevel_package: 仓根下无目录 {mount_name}/"
            )
            results.append(result)
            continue

        result["ok"] = True
        results.append(result)

    return results


# ── 巡逻排除依赖接口 ────────────────────────────────────────────────────────────

def list_mounted_repo_roots(*, registry_path: Path | None = None) -> list[Path]:
    """仅返回登记表中"清单文件存在"的仓根绝对路径列表。

    供 guardian fs_scanner_worker 排除散落文件扫描用(错误样本㊄的依赖接口)。
    只要仓里有 ``.omni-mount.yaml`` 就纳入排除清单(即便入口模块坏了也算已挂载,
    因为仓本身是被登记的业务仓, 其正常业务文件不该被巡逻误报)。
    """
    roots: list[Path] = []
    for entry in _read_registry(registry_path):
        repo_path = Path(str(entry["path"]))
        manifest_path = repo_path / MOUNT_MANIFEST_FILENAME
        if manifest_path.exists():
            roots.append(repo_path)
    return roots


# ── 注册层(真注册, 单条失败隔离) ────────────────────────────────────────────────

def register_external_pipelines(*, registry_path: Path | None = None) -> dict[str, Any]:
    """把 discover 出来的每条 ok 挂载注册进 core.registry._REGISTRY。

    对每条 ok 结果: 把 repo_path 加入 sys.path(若未在) → 逐条 import 入口模块拿
    function → 包成 PipelineEntry → 调 core.registry.register()。单条清单项失败
    不影响其余项(每条独立 try/except)。

    三重校验:
        - 清单缺失/不可读 → skipped
        - 入口模块 import 失败 → skipped(仓内既有管线数量不减)
        - 管线名未带挂载名前缀, 或撞已注册的内部管线名 → rejected(绝不覆盖内部管线)

    Returns:
        dict:
            registered: list[str]      # 成功注册的管线名
            skipped: list[dict]        # {mount_name?/pipeline_name?/reason}
            rejected: list[dict]       # {pipeline_name/reason}
    """
    from omnicompany.core.registry import PipelineEntry, get, register

    report: dict[str, list] = {"registered": [], "skipped": [], "rejected": []}

    for disc in discover_external_mounts(registry_path=registry_path):
        mount_name = disc.get("mount_name")
        repo_path: Path = disc["repo_path"]

        if not disc["ok"]:
            report["skipped"].append({
                "mount_name": mount_name,
                "reason": disc.get("reason") or "not_ok",
            })
            continue

        # 把业务仓根加入导入路径(调用时做, 全仓无先例的"外部路径进导入路径"层)。
        repo_str = str(repo_path.resolve())
        if repo_str not in sys.path:
            sys.path.insert(0, repo_str)

        for pipe in disc["pipelines"]:
            if not isinstance(pipe, dict):
                report["skipped"].append({
                    "mount_name": mount_name,
                    "reason": f"malformed_pipeline_entry: {pipe!r}",
                })
                continue

            pipeline_name = pipe.get("name")
            module_path = pipe.get("module")
            function_name = pipe.get("function")
            description = pipe.get("description", "")

            if not pipeline_name or not module_path or not function_name:
                report["skipped"].append({
                    "mount_name": mount_name,
                    "pipeline_name": pipeline_name,
                    "reason": f"incomplete_pipeline_decl: {pipe!r}",
                })
                continue

            # ── 前缀约束: 管线名必须以 mount_name 开头(防重名/防冒充内部管线) ──
            if not pipeline_name.startswith(f"{mount_name}."):
                report["rejected"].append({
                    "pipeline_name": pipeline_name,
                    "mount_name": mount_name,
                    "reason": (
                        f"name_missing_mount_prefix: '{pipeline_name}' 必须以 "
                        f"'{mount_name}.' 开头"
                    ),
                })
                continue

            # ── 重名拒绝: 撞了任何已注册管线(尤其内部管线)即拒, 绝不静默覆盖 ──
            if get(pipeline_name) is not None:
                report["rejected"].append({
                    "pipeline_name": pipeline_name,
                    "mount_name": mount_name,
                    "reason": (
                        f"name_clash_with_existing: '{pipeline_name}' 已被注册, "
                        f"外部挂载绝不覆盖既有(尤其内部)管线"
                    ),
                })
                continue

            # ── import 入口模块拿 function(单条失败隔离) ──
            try:
                mod = importlib.import_module(str(module_path))
                build_fn = getattr(mod, str(function_name))
            except Exception as e:  # noqa: BLE001
                # 错误样本㊁: 入口模块导入失败 → 只该条失败, 仓内既有管线数量不减。
                report["skipped"].append({
                    "mount_name": mount_name,
                    "pipeline_name": pipeline_name,
                    "reason": f"import_failed: {module_path}.{function_name}: {e}",
                })
                continue

            # ── 包成 PipelineEntry 注册; 数据落业务仓自己的 data/ (错误样本㊅) ──
            # engine="event": build_team() 必须返回 list[Router](worker 清单), 复用
            # 全仓既有的事件型引擎形状(gddecon 各管线同款), 不给调度器加挂载特例分支。
            # 单节点 worker 原样包住 build_fn, 真实调度经 MaterialDispatcher 跑。
            try:
                worker = _MountFunctionWorker(
                    pipeline_name=pipeline_name, mount_name=mount_name, build_fn=build_fn,
                )
                register(PipelineEntry(
                    name=pipeline_name,
                    description=description or f"外部挂载管线 {pipeline_name}",
                    domain=f"mount.{mount_name}",
                    engine="event",
                    entry_material=_mount_entry_material(pipeline_name),
                    build_team=lambda _w=worker: [_w],
                    build_bindings=lambda args, _fn=None: {},
                    default_db_dir=_mount_db_dir(repo_path, mount_name),
                ))
                report["registered"].append(pipeline_name)
            except Exception as e:  # noqa: BLE001
                report["skipped"].append({
                    "mount_name": mount_name,
                    "pipeline_name": pipeline_name,
                    "reason": f"register_failed: {e}",
                })

    if report["registered"]:
        logger.debug(
            "external_mounts: 注册 %d 条外部挂载管线: %s",
            len(report["registered"]), report["registered"],
        )
    return report
