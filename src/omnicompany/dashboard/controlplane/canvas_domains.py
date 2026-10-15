"""controlplane/canvas_domains.py — 画布域注册与跨域感知（omnidashboard 为主体架构）。

架构裁决（2026-08-16，spark.workshop 需求文档 §10.3）：
- dashboard 是画布/概览/审阅的主体宿主；workshop 等外部系统注册为"域"被感知。
- dashboard 与 workshop 零互相依赖：本模块不 import workshop 代码，只按共同契约
  （@omni/material-core/note 的文档形状）读其磁盘存储；workshop 侧无对应反向依赖。
- 感知单向：dashboard → 域。域内容操作权限由注册配置 access 声明。

形态：
- builtin 域 = overlay-note-store（dashboard 自身存储，invoke 桥读写同库）。
- disk 外部域 = 注册 root 路径（如 workshop data/material/notes），只读感知 +
  提审卡聚合；写回走域自己的协议端点（protocol 形态），不在本模块内做跨库写。
- protocol 形态预留：配置里 transport=protocol 的域仅登记 descriptor，端点由前端直连。

契约一致性约束：disk 域文档必须与 core 契约同形（version/sessionId/revision/cards/
relations + _entityRevisions/_mutations）；解析失败的文件跳过并计数，不整体失败。
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

canvas_domains_router = APIRouter(tags=["canvas-domains"])

_SCHEMA_VERSION = 1
_CANVAS_CAP = 500
_VALID_CAPABILITIES = {"canvas", "material", "wiki", "review"}


@dataclass(frozen=True)
class CanvasDomain:
    id: str
    title: str
    transport: str            # builtin | disk | protocol
    access: str               # readonly | readwrite
    capabilities: tuple[str, ...]
    root: str | None = None          # disk/builtin
    endpoint: str | None = None      # protocol
    product_ref: str | None = None
    workspace_kinds: tuple[str, ...] = ()
    poll_seconds: int = 15
    descriptor_extra: dict = field(default_factory=dict)

    def descriptor(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "transport": self.transport,
            "capabilities": list(self.capabilities),
            "access": self.access,
            "schema_version": _SCHEMA_VERSION,
            "root": self.root,
            "endpoint": self.endpoint,
            "product_ref": self.product_ref,
            "workspace_kinds": list(self.workspace_kinds),
            "poll_seconds": self.poll_seconds,
            **self.descriptor_extra,
        }


class DomainNotFound(KeyError):
    pass


def _config_path() -> Path:
    return Path(os.environ.get("OMNI_CANVAS_DOMAINS_FILE", r"C:/workspace/config\canvas-domains.yaml"))


def _default_workshop_root() -> str:
    return os.environ.get(
        "OMNI_WORKSHOP_NOTES_ROOT",
        r"C:/workspace/spark.workshop\workshop\data\material\notes",
    )


def _parse_entry(entry: dict, source: str) -> CanvasDomain:
    domain_id = str(entry.get("id") or "")
    if not domain_id or not all(ch.islower() or ch.isdigit() or ch in "-_" for ch in domain_id) or len(domain_id) > 64:
        raise ValueError(f"{source}: 域 id 非法: {domain_id!r}")
    transport = str(entry.get("transport") or "disk")
    if transport not in {"disk", "protocol"}:
        raise ValueError(f"{source}: 域 {domain_id} transport 必须是 disk|protocol")
    access = str(entry.get("access") or "readonly")
    if access not in {"readonly", "readwrite"}:
        raise ValueError(f"{source}: 域 {domain_id} access 非法")
    capabilities = entry.get("capabilities") or ["canvas", "review"]
    if not isinstance(capabilities, list) or not capabilities or not set(map(str, capabilities)) <= _VALID_CAPABILITIES:
        raise ValueError(f"{source}: 域 {domain_id} capabilities 非法")
    poll = entry.get("poll_seconds", 15)
    if not isinstance(poll, int) or poll < 1:
        poll = 15
    root = None
    endpoint = None
    product_ref = str(entry.get("product_ref") or "").strip() or None
    workspace_kinds_raw = entry.get("workspace_kinds") or []
    if not isinstance(workspace_kinds_raw, list) or not all(
        isinstance(kind, str) and kind.strip() for kind in workspace_kinds_raw
    ):
        raise ValueError(f"{source}: domain {domain_id} workspace_kinds invalid")
    workspace_kinds = tuple(dict.fromkeys(kind.strip() for kind in workspace_kinds_raw))
    if transport == "disk":
        root = str(entry.get("root") or "")
        if not root:
            raise ValueError(f"{source}: 域 {domain_id} disk 形态必须给 root")
    else:
        endpoint = str(entry.get("endpoint") or "").rstrip("/")
        if not endpoint.startswith(("http://", "https://")):
            raise ValueError(f"{source}: 域 {domain_id} protocol 形态必须给 http(s) endpoint")
    return CanvasDomain(
        id=domain_id,
        title=str(entry.get("title") or domain_id),
        transport=transport,
        access=access,
        capabilities=tuple(map(str, capabilities)),
        root=root,
        endpoint=endpoint,
        product_ref=product_ref,
        workspace_kinds=workspace_kinds,
        poll_seconds=poll,
    )


def _load_config_domains() -> list[CanvasDomain]:
    path = _config_path()
    if not path.is_file():
        return []
    raw = path.read_text(encoding="utf-8")
    entries: list[dict] = []
    try:
        import yaml  # omnicompany 依赖内已有 PyYAML
        loaded = yaml.safe_load(raw) or {}
        entries = loaded.get("domains") if isinstance(loaded, dict) else None
        if not isinstance(entries, list):
            raise ValueError("顶层必须是 {domains: [...]}")
    except ImportError:
        # 无 yaml 时退化为 JSON 兼容子集
        loaded = json.loads(raw)
        entries = loaded.get("domains", []) if isinstance(loaded, dict) else []
    except Exception as error:
        logger.warning("canvas-domains 配置解析失败(%s): %s", path, error)
        return []
    domains: list[CanvasDomain] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        try:
            domains.append(_parse_entry(entry, str(path)))
        except ValueError as error:
            logger.warning("canvas-domains 配置项跳过: %s", error)
    return domains


_REGISTRY: dict[str, CanvasDomain] = {}


def _builtin_domain() -> CanvasDomain:
    from . import overlay_notes
    return CanvasDomain(
        id="builtin",
        title="Omni Dashboard",
        transport="builtin",
        access="readwrite",
        capabilities=("canvas", "review"),
        root=str(overlay_notes._ROOT),
    )


def registry(refresh: bool = False) -> dict[str, CanvasDomain]:
    """builtin 永远在册；外部域来自配置文件（默认 workshop disk 域，root 缺失则跳过）。"""
    global _REGISTRY
    if _REGISTRY and not refresh:
        return _REGISTRY
    domains: dict[str, CanvasDomain] = {"builtin": _builtin_domain()}
    configured_ids = set()
    for domain in _load_config_domains():
        configured_ids.add(domain.id)
        if domain.id == "builtin":
            continue
        if domain.transport == "disk" and (not domain.root or not Path(domain.root).is_dir()):
            logger.info("canvas-domains: 域 %s 的 root 不存在, 跳过注册", domain.id)
            continue
        domains[domain.id] = domain
    if "workshop" not in configured_ids:
        root = Path(_default_workshop_root())
        if (root / "canvases").is_dir():
            domains["workshop"] = CanvasDomain(
                id="workshop",
                title="Spark Workshop",
                transport="disk",
                access="readonly",
                capabilities=("canvas", "material", "review"),
                root=str(root),
                product_ref="spark-workshop",
                workspace_kinds=("workshop", "workshop-canvas", "workshop-feature", "legacy-whiteboard"),
            )
    _REGISTRY = domains
    return domains


def require_domain(domain_id: str) -> CanvasDomain:
    domain = registry().get(domain_id)
    if domain is None:
        raise DomainNotFound(domain_id)
    return domain


# ── 域内读取（与 core 契约同形；损坏文件跳过不整体失败） ──


def _document_belongs_to_domain(domain: CanvasDomain, document: dict) -> bool:
    """Return whether a document belongs to an explicitly filtered domain.

    Historical storage roots can contain pre-domain Canvas files. Product
    ownership comes from stable Material identity/responsibility first, with
    configured legacy workspace kinds as the compatibility fallback. Titles
    and labels are deliberately not ownership signals.
    """
    if not domain.product_ref and not domain.workspace_kinds:
        return True
    if document.get("_productRef") == domain.product_ref:
        return True
    workspace = document.get("_workspaceRef")
    if isinstance(workspace, dict) and str(workspace.get("kind") or "") in domain.workspace_kinds:
        return True
    prefix = f"material:{domain.product_ref}/" if domain.product_ref else ""
    cards = document.get("cards") if isinstance(document.get("cards"), list) else []
    for card in cards:
        material = card.get("material") if isinstance(card, dict) else None
        if not isinstance(material, dict):
            continue
        material_id = material.get("id")
        if prefix and isinstance(material_id, str) and material_id.startswith(prefix):
            return True
        metadata = material.get("metadata")
        responsibility = metadata.get("responsibility") if isinstance(metadata, dict) else None
        maintenance = responsibility.get("maintenance") if isinstance(responsibility, dict) else None
        if isinstance(maintenance, dict) and maintenance.get("ref") == domain.product_ref:
            return True
    return False


def _read_domain_canvas_document(domain: CanvasDomain, canvas_id: str) -> dict | None:
    # builtin 与 disk 域同走 L2 直读(本机直读磁盘, 零 HTTP 开销); 只读提取不涉契约语义。
    if domain.transport not in ("builtin", "disk") or not domain.root:
        return None
    if not canvas_id or len(canvas_id) > 220 or any(ch in canvas_id for ch in "/\\"):
        return None
    path = Path(domain.root) / "canvases" / f"{canvas_id}.json"
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        return document if isinstance(document, dict) and _document_belongs_to_domain(domain, document) else None
    except FileNotFoundError:
        return None
    except Exception as error:
        logger.warning("域 %s 画布损坏 %s: %s", domain.id, canvas_id, error)
        return None


def list_domain_canvases(domain: CanvasDomain) -> list[dict]:
    items: list[dict] = []
    if domain.transport in ("builtin", "disk") and domain.root:
        root = Path(domain.root) / "canvases"
    else:
        return items
    if not root.is_dir():
        return items
    for path in sorted(root.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:_CANVAS_CAP]:
        document = _read_domain_canvas_document(domain, path.stem)
        if not isinstance(document, dict):
            continue
        cards = document.get("cards") if isinstance(document.get("cards"), list) else []
        items.append({
            "document_id": path.stem,
            "title": str(document.get("title") or document.get("sessionId") or path.stem),
            "revision": int(document.get("revision") or 0),
            "updated_at": str(document.get("updatedAt") or ""),
            "cards": len(cards),
            "relations": len(document.get("relations") or []),
        })
    return items


def _submission_entries(domain: CanvasDomain, canvas_id: str, document: dict) -> list[dict]:
    out: list[dict] = []
    cards = document.get("cards") if isinstance(document.get("cards"), list) else []
    for card in cards:
        if not isinstance(card, dict) or card.get("kind") == "group":
            continue
        submission = card.get("submission")
        if not isinstance(submission, dict) or not submission.get("state"):
            continue
        entry = {
            "ref": f"omni://domain/{domain.id}/canvas/{canvas_id}#card={card.get('id')}",
            "domain": domain.id,
            "canvas": canvas_id,
            "canvasTitle": str(document.get("title") or ""),
            "cardId": str(card.get("id") or ""),
            "title": str(card.get("title") or ""),
            "origin": str(submission.get("submittedBy") or ""),
            "state": str(submission.get("state") or ""),
            "submittedAt": str(submission.get("submittedAt") or ""),
            # 审阅库融合(2026-08-20): 归档/分类/版本是筛选与分组的必需字段。
            # 只投 state 会让 899 条历史归档件与真待审混在一个「未看」桶里
            # (归档件占 584 条 unreviewed) —— 前端必须能看到 archived 才能默认收起。
            "archived": bool(submission.get("archived")),
            "tier": str(submission.get("tier") or ""),
            "project": str(submission.get("project") or ""),
            "track": str(submission.get("track") or ""),
            "materialKind": str(submission.get("materialKind") or ""),
            "reason": str(submission.get("reason") or ""),
            "verdictAt": str(submission.get("verdictAt") or ""),
            "updatedAt": str(submission.get("updatedAt") or ""),
            "adapter": str(card.get("adapter") or ""),
            "counts": {
                "comments": len(submission.get("comments") or []),
                "annotations": len(submission.get("annotations") or []),
                "history": len(submission.get("history") or []),
            },
        }
        for key in ("version", "versionFamily", "subjectId", "subjectType", "revision",
                    "sourcePlanId", "links", "pushed"):
            value = submission.get(key)
            if value not in (None, "", {}, []):
                entry[key] = value
        if isinstance(card.get("source"), dict):
            entry["source"] = card["source"]
        out.append(entry)
    return out


_INBOX_STATE_ORDER = {"unreviewed": 0, "seen": 1, "fully-passed": 2, "not-fully-passed": 3}


def _inbox_sort_key(entry: dict) -> tuple:
    """收件箱排序：归档件永远最后（历史不是待办），同组内新提交在前。

    融合前只按 state 正序排，于是 584 条 unreviewed 的历史归档件压在真待审前面 ——
    审阅台第一屏全是几个月前的旧账。归档必须先降权。
    """
    submitted = str(entry.get("submittedAt") or "")
    return (
        1 if entry.get("archived") else 0,
        _INBOX_STATE_ORDER.get(str(entry.get("state") or ""), 9),
        tuple(-ord(ch) for ch in submitted),
    )


def domain_review_inbox(domain: CanvasDomain) -> list[dict]:
    """聚合一个域内所有画布的提审卡（审阅台统一收件的真源入口）。"""
    entries: list[dict] = []
    if domain.transport == "protocol":
        return entries  # protocol 域的 inbox 由前端直连域端点
    for item in list_domain_canvases(domain):
        document = _read_domain_canvas_document(domain, item["document_id"])
        if isinstance(document, dict):
            entries.extend(_submission_entries(domain, item["document_id"], document))
    entries.sort(key=_inbox_sort_key)
    return entries


def list_domain_materials(domain: CanvasDomain) -> list[dict]:
    """感知域内材料。disk 域机会式读取 workshop 布局的输出记录
    （<root>/../outputs/records.json，schema=workshop.material-output-record/1）；
    builtin 的材料面走 material 注册表，不在此暴露；protocol 域由前端直连端点。"""
    if domain.transport != "disk" or not domain.root or "material" not in domain.capabilities:
        return []
    records_file = Path(domain.root).parent / "outputs" / "records.json"
    if not records_file.is_file():
        return []
    try:
        records = json.loads(records_file.read_text(encoding="utf-8"))
    except Exception as error:
        logger.warning("域 %s 材料记录损坏: %s", domain.id, error)
        return []
    items: list[dict] = []
    for record in records if isinstance(records, list) else []:
        material = record.get("material") if isinstance(record, dict) else None
        if not isinstance(material, dict) or not material.get("id"):
            continue
        if domain.product_ref and not str(material["id"]).startswith(f"material:{domain.product_ref}/"):
            continue
        labels = material.get("labels")
        items.append({
            "id": str(material["id"]),
            "title": str(material.get("title") or material["id"]),
            "labels": [str(label) for label in labels] if isinstance(labels, list) else [],
            "ref": f"omni://domain/{domain.id}/material/{material['id']}",
        })
    return items


def aggregated_review_inbox() -> list[dict]:
    entries: list[dict] = []
    for domain in registry().values():
        if "review" in domain.capabilities:
            try:
                entries.extend(domain_review_inbox(domain))
            except Exception as error:
                logger.warning("域 %s inbox 聚合失败: %s", domain.id, error)
    entries.sort(key=_inbox_sort_key)
    return entries


# ── HTTP 面 ──


@canvas_domains_router.get("/api/domains")
def api_domains(refresh: bool = False):
    return {"domains": [domain.descriptor() for domain in registry(refresh=refresh).values()]}


@canvas_domains_router.get("/api/domains/{domain_id}/canvases")
def api_domain_canvases(domain_id: str):
    try:
        domain = require_domain(domain_id)
    except DomainNotFound:
        return JSONResponse({"error": f"域未注册: {domain_id}"}, status_code=404)
    return {"domain": domain_id, "canvases": list_domain_canvases(domain)}


@canvas_domains_router.get("/api/domains/{domain_id}/canvases/{canvas_id}")
def api_domain_canvas(domain_id: str, canvas_id: str):
    try:
        domain = require_domain(domain_id)
    except DomainNotFound:
        return JSONResponse({"error": f"域未注册: {domain_id}"}, status_code=404)
    document = _read_domain_canvas_document(domain, canvas_id)
    if document is None:
        return JSONResponse({"error": f"画布不存在: {canvas_id}"}, status_code=404)
    return {"domain": domain_id, "canvas": canvas_id, "document": document}


@canvas_domains_router.get("/api/domains/{domain_id}/materials")
def api_domain_materials(domain_id: str):
    try:
        domain = require_domain(domain_id)
    except DomainNotFound:
        return JSONResponse({"error": f"域未注册: {domain_id}"}, status_code=404)
    return {"domain": domain_id, "materials": list_domain_materials(domain)}


@canvas_domains_router.get("/api/review/inbox")
def api_review_inbox():
    """跨域聚合收件箱：builtin + 所有带 review 能力的域。"""
    return {"items": aggregated_review_inbox()}
