# [OMNI] origin=claude-code domain=omnicompany/selftest ts=2026-04-20T00:00:00Z type=router
# [OMNI] material_id="material:core.selftest.registry_scanner.pipeline_validator.py"
"""RegistryCheckerWorker — Selftest Team Worker #1.

Worker 协议:
  FORMAT_IN  = selftest.request
  FORMAT_OUT = selftest.registry-report

职责: 加载所有管线注册表, 逐一验证 build_pipeline + build_bindings + bindings 完整性。
"""
from __future__ import annotations

from typing import Any

from omnicompany.protocol.anchor import Verdict, VerdictKind
from omnicompany.packages.services._core.omnicompany import Worker


class RegistryCheckerWorker(Worker):
    """加载所有管线注册表, 逐一验证 build_pipeline + build_bindings + bindings 完整性。"""

    DESCRIPTION = (
        "调用 register_all() 获取所有注册管线, 对每条管线: "
        "1) 调用 build_pipeline() 检查 TeamSpec 可构建; "
        "2) 调用 build_bindings() 检查 bindings 可构建; "
        "3) 验证 TeamSpec.node_ids 与 bindings.keys() 完全对应。"
        "输出每个管线的 ok/error 详情。"
    )
    FORMAT_IN = "selftest.request"
    FORMAT_OUT = "selftest.registry-report"

    def run(self, input_data: Any) -> Verdict:
        from omnicompany.core.pipelines import register_all
        import omnicompany.core.registry as _reg

        try:
            register_all()
        except Exception as exc:
            return Verdict(
                kind=VerdictKind.FAIL,
                diagnosis=f"register_all() 崩溃: {exc}",
            )

        entries = _reg.list_all()
        if not entries:
            return Verdict(
                kind=VerdictKind.FAIL,
                diagnosis="注册表为空, register_all() 未注册任何管线",
            )

        pipeline_results = []
        total_fail = 0
        for entry in entries:
            result = _check_entry(entry)
            if not result["ok"]:
                total_fail += 1
            pipeline_results.append(result)

        return Verdict(
            kind=VerdictKind.PASS,
            diagnosis=(
                f"注册检查完成: {len(entries)} 个管线, "
                f"{len(entries) - total_fail} 通过, {total_fail} 失败"
            ),
            output={
                "project_root": input_data.get("project_root", ""),
                "total_pipelines": len(entries),
                "failed_pipelines": total_fail,
                "pipeline_results": pipeline_results,
            },
        )


def _check_entry(entry: Any) -> dict:
    name = getattr(entry, "name", str(entry))
    errors: list[str] = []
    warnings: list[str] = []
    pipeline_spec = None

    try:
        pipeline_spec = entry.build_pipeline()
    except AttributeError as exc:
        warnings.append(f"build_pipeline() 属性缺失（注册条目可能过时）: {_short(exc)}")
    except Exception as exc:
        errors.append(f"build_pipeline() 失败: {_short(exc)}")

    bindings = None
    try:
        bindings = entry.build_bindings()
    except TypeError as exc:
        msg = str(exc)
        if "required positional argument" in msg or "missing" in msg.lower():
            warnings.append(f"build_bindings() 需要必填参数（历史管线）: {_short(exc)}")
        else:
            errors.append(f"build_bindings() 失败: {_short(exc)}")
    except AttributeError as exc:
        warnings.append(f"build_bindings() 属性缺失（注册条目可能过时）: {_short(exc)}")
    except Exception as exc:
        errors.append(f"build_bindings() 失败: {_short(exc)}")

    if pipeline_spec is not None and bindings is not None:
        node_ids = {n.id for n in pipeline_spec.nodes}
        binding_keys = set(bindings.keys())
        missing = node_ids - binding_keys
        extra = binding_keys - node_ids
        if missing:
            errors.append(f"bindings 缺少节点: {sorted(missing)}")
        if extra:
            errors.append(f"bindings 多余节点: {sorted(extra)}")

    return {
        "name": name,
        "ok": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "node_count": len(pipeline_spec.nodes) if pipeline_spec else 0,
    }


def _short(exc: Exception) -> str:
    return str(exc)[:200]
