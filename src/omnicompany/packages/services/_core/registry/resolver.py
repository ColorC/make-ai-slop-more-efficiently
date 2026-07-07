# [OMNI] origin=claude-code domain=services/_core/registry ts=2026-07-02T00:00:00Z type=material status=active agent=claude
# [OMNI] summary="统一引用解析器: 把类型化双链[[kind:id]]/URI omni://kind/id/裸id 归一后分派给八个适配器(material/entity/decision/review/whatnow/note/plan/file), 每个适配器带回指自检(verify), 解析成功但真源失真必须显式报, 禁静默错解析。"
# [OMNI] why="语义OS目标架构3.2节: 八套旧ID各写适配器+适配器自检铁律。是无痕挂载与决策复用的寻址脊椎; 纯确定性代码零LLM。收编既有双链解析(notes controlplane只认裸[[name]], 这里补类型化前缀), 与既有 omni://(entity_registry 全量扫描式) 分工: 那套是索引投影, 这套是按 kind 分派到各体系原生查询接口的适配器。"
# [OMNI] tags=registry,resolver,unified-reference,adapter,self-check,semantic-os
# [OMNI] material_id="material:core.registry.unified_reference_resolver.py"
"""统一引用解析器 (Unified Reference Resolver)。

一个引用有三种写法, 归一后分派给按 kind 命名的适配器:

  1. 类型化双链   [[kind:id]]  /  [[kind:id|别名]]  /  [[kind:id#锚点]]  /  ![[kind:id]]
  2. URI 形式     omni://<kind>/<id>[@版本][#锚点]
  3. 裸 id 自动识别:
       material:...           → material 适配器 (MaterialIdIndex)
       DEC-/BLF-/CMT-...      → decision 适配器 (决策库)
       mat_...                → review 适配器 (审阅材料库)
       p_... / whatnow 任务   → whatnow 适配器 (HTTP :8230)
       poof-note:// / note-.. → note 适配器 (poof-notes index.json)
       [YYYY-MM-DD]NAME 等    → plan 适配器 (docs/plans)
       存在的文件路径          → file 适配器

八个适配器各自实现 ``resolve(id) -> ResolveResult`` 与 ``verify(result) -> (ok, note)``。

**自检铁律 (语义OS目标架构 3.2)**: 每个适配器解析出结果后, verify() 必须能回指真源
并核对廉价指纹(文件=存在+大小, HTTP=响应体非空, 索引=条目在)。解析成功但自检失败 =
"失真", 必须显式报出(result.verified=False + verify_note), 禁止静默返回一个坏结果。

本模块是**纯确定性代码, 零 LLM 调用**。不 import dashboard 的网页框架(fastapi 等):
审阅材料走 ``MaterialStore`` 干净直读, 不经 ``routes.get_store``(那条路径拖 fastapi)。

与既有引用设施的关系(后续收编):
  - dashboard/controlplane/notes.py 的 ``_extract_links`` 只解析裸 ``[[name]]``(fuzzy
    到最近 note), 不认类型化前缀; 本模块补上类型化前缀分派。双链语法(alias/anchor/embed)
    在这里独立、最小地重实现(``parse_reference``), 未来可反向让 notes.py 复用本模块。
  - dashboard/boss_sight/entity_registry.py 的 ``omni://<kind>/<id>`` 是全量扫描式索引
    投影(build_entity_index); 本模块的 omni:// 语法与之兼容, 但走"按 kind 分派到各体系
    原生查询接口"的适配器路线(更轻、可回指真源自检)。两者 kind 词表有重叠, 暂各自存在。
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional


# ── 统一解析结果数据类 ────────────────────────────────────────────────────────

@dataclass
class ResolveResult:
    """一次引用解析的统一结果。

    字段:
      kind:        归一后的种类 (material/entity/decision/review/whatnow/note/plan/file/unknown)
      id:          归一后的裸 id (去掉前缀/别名/锚点/版本)
      exists:      真源里是否真的存在这个对象
      location:    真源位置 (本地文件路径 或 URL/URI 字符串)
      resolver:    命中的适配器名 (= kind, 便于日志追踪)
      meta:        元信息 dict (标题/状态/大小/指纹 等适配器自定义)
      version:     URI @版本 限定 (无则空串)
      anchor:      #锚点 (无则空串)
      raw:         调用方传入的原始引用串
      verified:    是否通过回指自检 (None=还没跑 verify)
      verify_note: 自检说明 (失真时讲清哪里对不上)
      error:       解析层面的错误说明 (无法识别/无法定位)
    """

    kind: str
    id: str
    exists: bool = False
    location: str = ""
    resolver: str = ""
    meta: dict[str, Any] = field(default_factory=dict)
    version: str = ""
    anchor: str = ""
    raw: str = ""
    verified: Optional[bool] = None
    verify_note: str = ""
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "id": self.id,
            "exists": self.exists,
            "location": self.location,
            "resolver": self.resolver,
            "meta": dict(self.meta),
            "version": self.version,
            "anchor": self.anchor,
            "raw": self.raw,
            "verified": self.verified,
            "verify_note": self.verify_note,
            "error": self.error,
        }


class ResolveError(Exception):
    """引用无法归一/识别时抛出(调用方可 catch 转成 ResolveResult(error=...))。"""


# ── 引用语法解析(双链 / URI / 裸 id 归一)──────────────────────────────────────

# 类型化双链: 可带前导 ! (embed), [[ ... ]] 内是 body
_WIKILINK_RE = re.compile(r"^!?\[\[(.+)\]\]$", re.DOTALL)
# kind 前缀: 字母开头, 字母/数字/下划线/连字符, 后跟冒号
_KIND_PREFIX_RE = re.compile(r"^([A-Za-z_][\w-]*):(.+)$", re.DOTALL)

# 各适配器认领裸 id 的形状(顺序敏感: 前面的先匹配)
_PLAN_DATE_RE = re.compile(r"\[\d{4}-\d{2}-\d{2}\]")
_DECISION_RE = re.compile(r"^(DEC|BLF|CMT)-\d{4}-\d{2}-\d{2}-\d+$")


@dataclass
class ParsedReference:
    """从任意写法归一出来的中间态: kind(可能为空=待自动识别)+ id + version + anchor。"""

    kind: str
    id: str
    version: str = ""
    anchor: str = ""


def _strip_alias_anchor_version(body: str) -> tuple[str, str, str]:
    """从 id 主体里剥离 |别名 / #锚点 / @版本, 返回 (纯id, version, anchor)。

    容错两种书写顺序(Obsidian 规范是 id#anchor|alias, 但也见 id|alias#anchor):
    先切 |别名(别名整段丢弃, 但别名段里若含 #锚点/@版本 先抢救出来), 再从 id 段切 #锚点/@版本。
    注意 file 路径里可能含 @/#, 但双链/URI 语义下这里的剥离是约定俗成的; file 适配器
    另有兜底(见 FileAdapter, 剥离后定位不到会带 anchor 再试)。
    """
    anchor = ""
    version = ""
    # |别名 —— id 取第一个 | 前, 但别名段里若带 #锚点/@版本, 先抢救(兼容 id|alias#anchor 顺序)
    if "|" in body:
        head, alias_seg = body.split("|", 1)
        if "#" in alias_seg:
            anchor = alias_seg.split("#", 1)[1].strip()
        if "@" in alias_seg and not anchor:
            version = alias_seg.split("@", 1)[1].strip()
        body = head
    # #锚点(id 段自己带的, 优先级高于别名段抢救出来的)
    if "#" in body:
        body, anchor = body.split("#", 1)
    # @版本
    if "@" in body:
        body, version = body.split("@", 1)
    return body.strip(), version.strip(), anchor.strip()


def parse_reference(ref: str) -> ParsedReference:
    """把三种写法归一成 ParsedReference。kind 为空表示"裸 id, 待自动识别"。

    - [[kind:id|alias#anchor]] / ![[...]]  → kind=kind
    - [[bareid]]                            → kind="" (交给自动识别)
    - omni://kind/id[@ver][#anchor]         → kind=kind
    - kind:id                               → kind=kind (裸写类型化前缀)
    - 其余裸 id                              → kind="" (交给自动识别)
    """
    if ref is None:
        raise ResolveError("引用为空")
    s = ref.strip()
    if not s:
        raise ResolveError("引用为空")

    # 1) 双链 [[...]] / ![[...]]
    m = _WIKILINK_RE.match(s)
    if m:
        inner = m.group(1).strip()
        return _parse_prefixed_or_bare(inner)

    # 2) URI omni://kind/id
    if s.lower().startswith("omni://"):
        return _parse_omni_uri(s)

    # 3) poof-note:// 作为一种 URI 形式(note 适配器专属), 保留整串交给自动识别的 kind 提示
    if s.lower().startswith("poof-note://"):
        # poof-note://<id> 或 poof-note://note/<id>#block
        rest = s[len("poof-note://"):]
        pure, version, anchor = _strip_alias_anchor_version(rest)
        # 允许 note/<id> 形式, 取最后一段作 id
        note_id = pure.split("/")[-1].strip()
        return ParsedReference(kind="note", id=note_id, version=version, anchor=anchor)

    # 4) 裸串(可能含类型化前缀 kind:id)
    return _parse_prefixed_or_bare(s)


def _parse_prefixed_or_bare(inner: str) -> ParsedReference:
    """内部体: 先剥 alias/anchor/version, 再看有没有 kind: 前缀。"""
    pure, version, anchor = _strip_alias_anchor_version(inner)
    m = _KIND_PREFIX_RE.match(pure)
    if m:
        prefix = m.group(1)
        rest = m.group(2).strip()
        # material: 是 material_id 命名空间自身的一部分(material:core.x.y),
        # 不能把 "material" 当外层 kind 前缀剥掉 —— 保留整串作 id, kind=material。
        if prefix == "material":
            return ParsedReference(kind="material", id=pure, version=version, anchor=anchor)
        # 已知类型化前缀 → 作 kind; 未知前缀(如 http)不当 kind, 整串交自动识别
        if prefix in _KNOWN_KINDS:
            return ParsedReference(kind=prefix, id=rest, version=version, anchor=anchor)
    return ParsedReference(kind="", id=pure, version=version, anchor=anchor)


def _parse_omni_uri(uri: str) -> ParsedReference:
    """omni://<kind>/<id>[@ver][#anchor] 解析(自实现, 不引 entity_registry 以免耦合)。

    id 段支持百分号编码(如 %2F 表示 /), 与 unified-reference.md 承诺的
    "可粘贴单串"等价形式一致; 明文 / 也照常接受。
    """
    from urllib.parse import unquote

    rest = uri[len("omni://"):]
    # 先切锚点/版本(在整个 rest 上切, 因为 id 可能含 / )
    body, version, anchor = _strip_alias_anchor_version(rest)
    if "/" not in body and "%2F" not in body and "%2f" not in body:
        raise ResolveError(f"omni URI 缺少 id 段: {uri}")
    if "/" in body:
        kind, entity_id = body.split("/", 1)
    else:
        # 整段被百分号编码(kind%2Fid 不合法; kind 后必须有明文 / 或编码在 id 内),
        # 此分支处理 kind 与 id 之间也被编码的容错: 解码后再切一次
        decoded = unquote(body)
        if "/" not in decoded:
            raise ResolveError(f"omni URI 缺少 id 段: {uri}")
        kind, entity_id = decoded.split("/", 1)
    kind = kind.strip()
    entity_id = unquote(entity_id).strip()
    if not kind or not entity_id:
        raise ResolveError(f"omni URI 非法: {uri}")
    return ParsedReference(kind=kind, id=entity_id, version=version, anchor=anchor)


def _autodetect_kind(bare_id: str, *, workspace_root: Path) -> str:
    """裸 id 自动识别 kind(顺序敏感)。识别不出返回 ""。"""
    s = bare_id.strip()
    if not s:
        return ""
    if s.startswith("material:"):
        return "material"
    if _DECISION_RE.match(s):
        return "decision"
    if s.startswith("mat_"):
        return "review"
    if s.startswith("poof-note://") or s.startswith("note-"):
        return "note"
    if s.startswith("p_"):
        return "whatnow"
    # plan_id: 含 [YYYY-MM-DD] 日期段(basename 或 主题/[日期]名)
    if _PLAN_DATE_RE.search(s):
        return "plan"
    # 存在的文件路径(绝对 或 相对工作区)
    if _looks_like_existing_file(s, workspace_root):
        return "file"
    return ""


def _looks_like_existing_file(s: str, workspace_root: Path) -> bool:
    try:
        p = Path(s)
        if p.is_absolute() and p.exists():
            return True
        cand = (workspace_root / s)
        if cand.exists():
            return True
    except OSError:
        return False
    return False


# ── 适配器基类 ────────────────────────────────────────────────────────────────

class BaseAdapter:
    """适配器接口: resolve(裸id, parsed) -> ResolveResult; verify(result) -> (ok, note)。"""

    name: str = "base"

    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = workspace_root

    def resolve(self, ref_id: str, parsed: ParsedReference) -> ResolveResult:  # pragma: no cover - abstract
        raise NotImplementedError

    def verify(self, result: ResolveResult) -> tuple[bool, str]:  # pragma: no cover - abstract
        raise NotImplementedError

    # 便捷: 构造带公共字段的空壳结果
    def _new(self, ref_id: str, parsed: ParsedReference) -> ResolveResult:
        return ResolveResult(
            kind=self.name,
            id=ref_id,
            resolver=self.name,
            version=parsed.version,
            anchor=parsed.anchor,
        )


# ── material 适配器: MaterialIdIndex(缺索引时现场 rebuild)────────────────────────

class MaterialAdapter(BaseAdapter):
    name = "material"

    def resolve(self, ref_id: str, parsed: ParsedReference) -> ResolveResult:
        from .material_index import get_material_id_index

        res = self._new(ref_id, parsed)
        index = get_material_id_index()
        entry = index.lookup(ref_id)
        # 缺索引(从未 rebuild 过)时现场 rebuild 一次再查 —— 语义OS要求"缺索引时现场 rebuild"
        if entry is None and not index.index_path.exists():
            self._rebuild(index)
            entry = index.lookup(ref_id)
        if entry is None:
            # 索引存在但没这条: 也尝试一次 rebuild(可能是新写的头还没进索引)
            if index.index_path.exists():
                self._rebuild(index)
                entry = index.lookup(ref_id)
        if entry is None:
            res.exists = False
            res.location = str(index.index_path)
            res.error = f"material_id 不在索引中(索引={index.index_path})"
            return res
        abs_path = self.workspace_root / entry.file_path
        res.exists = True
        res.location = str(abs_path)
        res.meta = {
            "file_path": entry.file_path,
            "kind": entry.kind,
            "domain": entry.domain,
            "summary": entry.summary,
            "index_path": str(index.index_path),
        }
        return res

    def _rebuild(self, index) -> None:
        try:
            scopes = [
                self.workspace_root / "src" / "omnicompany",
                self.workspace_root / "templates",
                self.workspace_root / "docs",
            ]
            scopes = [s for s in scopes if s.exists()]
            index.rebuild_from_headers(scopes, self.workspace_root)
        except Exception:
            pass

    def verify(self, result: ResolveResult) -> tuple[bool, str]:
        if not result.exists:
            return False, "索引里无此 material_id"
        p = Path(result.location)
        if not p.is_file():
            return False, f"索引指向的文件不存在: {result.location}(失真: 索引条目在但真源丢了)"
        size = p.stat().st_size
        result.meta["file_size"] = size
        if size == 0:
            return False, f"真源文件存在但为空(0 字节): {result.location}"
        return True, f"文件存在, {size} 字节"


# ── entity 适配器: InstanceRegistry ────────────────────────────────────────────

class EntityAdapter(BaseAdapter):
    name = "entity"

    def resolve(self, ref_id: str, parsed: ParsedReference) -> ResolveResult:
        from . import get_registry

        res = self._new(ref_id, parsed)
        reg = get_registry()
        entry = reg.read(ref_id)
        if entry is None:
            res.exists = False
            res.location = str(reg.registry_dir)
            res.error = f"entity_id 不在注册表中: {ref_id}"
            return res
        entity_path = reg._entity_path(ref_id)
        res.exists = True
        res.location = str(entity_path)
        res.meta = {
            "type": entry.type,
            "name": entry.name,
            "package": entry.package,
            "source_file": entry.source_file,
        }
        return res

    def verify(self, result: ResolveResult) -> tuple[bool, str]:
        if not result.exists:
            return False, "注册表里无此 entity_id"
        p = Path(result.location)
        if not p.is_file():
            return False, f"注册表条目文件不存在: {result.location}(失真)"
        size = p.stat().st_size
        result.meta["record_size"] = size
        if size == 0:
            return False, "注册表条目文件为空(0 字节)"
        return True, f"注册表条目存在, {size} 字节"


# ── decision 适配器: 决策库 ────────────────────────────────────────────────────

class DecisionAdapter(BaseAdapter):
    name = "decision"

    def resolve(self, ref_id: str, parsed: ParsedReference) -> ResolveResult:
        from omnicompany.packages.domains.decisions import library
        from omnicompany.packages.domains.decisions._paths import RECORDS_PATH

        res = self._new(ref_id, parsed)
        res.location = str(RECORDS_PATH)
        rec = library.get(ref_id)
        if rec is None:
            # 区分"不存在"与"墓碑软删": fold 里有但 status=deleted → 已删
            folded = library.fold().get(ref_id)
            res.exists = False
            if folded is not None and folded.get("status") == "deleted":
                res.error = f"决策 {ref_id} 已软删(墓碑)"
                res.meta = {"tombstoned": True}
            else:
                res.error = f"决策库里无此 id: {ref_id}"
            return res
        res.exists = True
        res.meta = {
            "kind": rec.get("kind"),
            "status": rec.get("status"),
            "statement": (rec.get("statement") or "")[:200],
            "records_path": str(RECORDS_PATH),
        }
        return res

    def verify(self, result: ResolveResult) -> tuple[bool, str]:
        p = Path(result.location)
        if not p.is_file():
            return False, f"决策库文件不存在: {result.location}"
        if not result.exists:
            return False, "决策库里无此 id(或已墓碑)"
        # 廉价指纹: 重新 fold 确认该 id 折叠后仍是活的
        from omnicompany.packages.domains.decisions import library
        rec = library.get(result.id)
        if rec is None:
            return False, "复核: 决策 id 折叠后不可见(失真)"
        result.meta["records_size"] = p.stat().st_size
        return True, f"决策存在, kind={rec.get('kind')} status={rec.get('status')}"


# ── review 适配器: 审阅材料库(直读 MaterialStore, 不拖 fastapi)──────────────────

class ReviewAdapter(BaseAdapter):
    name = "review"

    def _store_root(self) -> Path:
        return self.workspace_root / "data" / "boss_sight" / "reviewstage"

    def resolve(self, ref_id: str, parsed: ParsedReference) -> ResolveResult:
        res = self._new(ref_id, parsed)
        root = self._store_root()
        json_path = root / f"{ref_id}.json"
        res.location = str(json_path)
        try:
            from omnicompany.dashboard.boss_sight.reviewstage.store import MaterialStore
            store = MaterialStore(root=root)
            m = store.get(ref_id)
        except Exception as e:  # noqa: BLE001
            # store 不可用时回退直读 JSON(设计允许: "不行就直读 <id>.json")
            m = None
            if not json_path.is_file():
                res.exists = False
                res.error = f"审阅材料不存在, 且 store 不可用: {e}"
                return res
        if m is None and json_path.is_file():
            # 直读兜底
            try:
                data = json.loads(json_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as e:
                res.exists = False
                res.error = f"审阅材料 JSON 损坏: {e}"
                return res
            res.exists = True
            res.meta = {
                "title": data.get("title"),
                "kind": data.get("kind"),
                "tier": data.get("tier"),
                "status": data.get("status"),
                "file_relpath": data.get("file_relpath"),
            }
            return res
        if m is None:
            res.exists = False
            res.error = f"审阅材料库里无此 id: {ref_id}"
            return res
        kind = m.kind.value if hasattr(m.kind, "value") else m.kind
        tier = m.tier.value if hasattr(m.tier, "value") else m.tier
        status = m.status.value if hasattr(m.status, "value") else m.status
        res.exists = True
        res.meta = {
            "title": m.title,
            "kind": kind,
            "tier": tier,
            "status": status,
            "file_relpath": m.file_relpath,
        }
        return res

    def verify(self, result: ResolveResult) -> tuple[bool, str]:
        p = Path(result.location)
        if not p.is_file():
            return False, f"审阅材料 JSON 不存在: {result.location}(失真)"
        if not result.exists:
            return False, "审阅材料库里无此 id"
        size = p.stat().st_size
        result.meta["json_size"] = size
        if size == 0:
            return False, "审阅材料 JSON 为空(0 字节)"
        return True, f"材料存在, JSON {size} 字节"


# ── whatnow 适配器: HTTP :8230(服务不在=明确报, 不装死)────────────────────────

class WhatnowAdapter(BaseAdapter):
    name = "whatnow"
    BASE = "http://127.0.0.1:8230"

    def _fetch_board(self) -> tuple[Optional[dict], str]:
        """拉 /api/board?archived=1(whatnow 无按 id 单查, 只能整树拉)。

        返回 (board, error)。服务不在时 board=None, error 明确写"服务未运行"。
        """
        url = self.BASE + "/api/board?archived=1"
        try:
            with urllib.request.urlopen(url, timeout=10) as r:
                raw = r.read().decode("utf-8", "replace")
            return json.loads(raw), ""
        except urllib.error.URLError as e:
            return None, f"whatnow 服务未运行 / 不可达(:8230): {e}"
        except (json.JSONDecodeError, OSError) as e:
            return None, f"whatnow 响应异常: {e}"

    def _find_task(self, board: dict, task_id: str) -> Optional[dict]:
        for c in board.get("clusters", []) or []:
            for g in c.get("goals", []) or []:
                for t in g.get("tasks", []) or []:
                    if t.get("id") == task_id:
                        return t
                    for st in t.get("subtasks", []) or []:
                        if st.get("id") == task_id:
                            return st
        return None

    def resolve(self, ref_id: str, parsed: ParsedReference) -> ResolveResult:
        res = self._new(ref_id, parsed)
        res.location = f"{self.BASE}/api/board (task id={ref_id})"
        board, err = self._fetch_board()
        if board is None:
            res.exists = False
            res.error = err  # 明确报服务未运行, 不装死
            res.meta = {"service_down": True}
            return res
        task = self._find_task(board, ref_id)
        if task is None:
            res.exists = False
            res.error = f"whatnow 任务不存在: {ref_id}"
            return res
        res.exists = True
        res.meta = {
            "title": task.get("title"),
            "status": task.get("status"),
            "completion": task.get("completion"),
            "goal_id": task.get("goal_id"),
            "plan_id": task.get("plan_id"),
            "channel": task.get("channel"),
        }
        return res

    def verify(self, result: ResolveResult) -> tuple[bool, str]:
        # HTTP 指纹: 重新拉一次 board, 响应体非空 + 任务仍在
        board, err = self._fetch_board()
        if board is None:
            return False, err
        if not board:
            return False, "whatnow /api/board 响应体为空(失真)"
        if not result.exists:
            return False, "whatnow 任务不存在"
        task = self._find_task(board, result.id)
        if task is None:
            return False, "复核: 任务在 board 里已找不到(失真)"
        return True, f"任务存在, status={task.get('status')}"


# ── note 适配器: poof-notes index.json(只读)────────────────────────────────────

class NoteAdapter(BaseAdapter):
    name = "note"

    def _norm_id(self, ref_id: str) -> str:
        s = ref_id.strip()
        if s.startswith("poof-note://"):
            s = s[len("poof-note://"):]
            s = s.split("/")[-1]
        return s.strip()

    def resolve(self, ref_id: str, parsed: ParsedReference) -> ResolveResult:
        from omnicompany.packages.services._core.lifecycle.note_source import read_note_source

        note_id = self._norm_id(ref_id)
        res = self._new(note_id, parsed)
        src = read_note_source()
        res.location = str(src.index_path)
        if not src.available():
            res.exists = False
            res.error = f"poof-notes 索引不可用: {src.index_path}"
            return res
        note = src.get_note(note_id)
        if note is None:
            res.exists = False
            res.error = f"poof 笔记不存在: {note_id}"
            return res
        md_abs = None
        if note.md_rel:
            md_abs = src.root / note.md_rel
        res.exists = True
        res.location = str(md_abs) if md_abs else str(src.index_path)
        res.meta = {
            "title": note.title,
            "has_body": note.has_body,
            "md_rel": note.md_rel,
            "index_path": str(src.index_path),
            "anchor": f"poof-note://{note.id}",
        }
        return res

    def verify(self, result: ResolveResult) -> tuple[bool, str]:
        if not result.exists:
            return False, "poof-notes 索引里无此笔记"
        # index.json 必须在(廉价指纹)
        index_path = Path(result.meta.get("index_path") or "")
        if not index_path.is_file():
            return False, f"poof-notes index.json 不存在: {index_path}(失真)"
        # md 正文是懒导出的: 有则核对大小, 没有则如实说明(不算失真, 是尚未导出)
        if result.meta.get("has_body") and result.meta.get("md_rel"):
            md = Path(result.location)
            if not md.is_file():
                return False, f"笔记标记有正文但 md 文件不存在: {result.location}(失真)"
            size = md.stat().st_size
            result.meta["md_size"] = size
            return True, f"笔记存在, 正文 md {size} 字节"
        return True, "笔记存在(正文尚未懒导出, index 条目在)"


# ── plan 适配器: docs/plans 路径解析 ───────────────────────────────────────────

class PlanAdapter(BaseAdapter):
    name = "plan"
    _PLAN_DIR_RE = re.compile(r"^\[(\d{4}-\d{2}-\d{2})\](.+)$")

    def _plans_root(self) -> Path:
        return self.workspace_root / "docs" / "plans"

    def _walk_plans(self, root: Path) -> list[tuple[str, Path]]:
        out: list[tuple[str, Path]] = []
        if not root.is_dir():
            return out

        def _walk(d: Path) -> None:
            try:
                for entry in d.iterdir():
                    if not entry.is_dir():
                        continue
                    if entry.name in ("_archive", "_scratch"):
                        continue
                    if self._PLAN_DIR_RE.match(entry.name):
                        rel = entry.relative_to(root).as_posix()
                        out.append((rel, entry))
                        continue
                    _walk(entry)
            except OSError:
                pass

        _walk(root)
        return out

    def resolve(self, ref_id: str, parsed: ParsedReference) -> ResolveResult:
        res = self._new(ref_id, parsed)
        root = self._plans_root()
        res.location = str(root)
        plans = self._walk_plans(root)
        # 三种输入: 完整 id / 目录 basename / 纯 NAME(去日期前缀)
        # 1) 完整 id 精确匹配
        for pid, p in plans:
            if pid == ref_id:
                return self._hit(res, pid, p)
        # 2) basename 精确匹配
        basename_hits = [(pid, p) for pid, p in plans if p.name == ref_id]
        if len(basename_hits) == 1:
            return self._hit(res, *basename_hits[0])
        if len(basename_hits) > 1:
            res.exists = False
            res.error = f"plan basename 歧义: {len(basename_hits)} 个匹配 {ref_id!r}"
            res.meta = {"candidates": [pid for pid, _ in basename_hits]}
            return res
        # 3) NAME-only(剥日期前缀)
        name_hits = []
        for pid, p in plans:
            m = self._PLAN_DIR_RE.match(p.name)
            if m and m.group(2) == ref_id:
                name_hits.append((pid, p))
        if len(name_hits) == 1:
            return self._hit(res, *name_hits[0])
        if len(name_hits) > 1:
            res.exists = False
            res.error = f"plan NAME 歧义: {len(name_hits)} 个匹配 {ref_id!r}"
            res.meta = {"candidates": [pid for pid, _ in name_hits]}
            return res
        res.exists = False
        res.error = f"plan 不存在: {ref_id}"
        return res

    def _hit(self, res: ResolveResult, pid: str, p: Path) -> ResolveResult:
        plan_md = p / "plan.md"
        res.id = pid
        res.exists = True
        res.location = str(p)
        res.meta = {
            "plan_id": pid,
            "plan_dir": str(p),
            "plan_md": str(plan_md),
            "plan_md_exists": plan_md.is_file(),
        }
        return res

    def verify(self, result: ResolveResult) -> tuple[bool, str]:
        if not result.exists:
            return False, "plan 目录不存在"
        d = Path(result.location)
        if not d.is_dir():
            return False, f"plan 目录不存在: {result.location}(失真)"
        plan_md = d / "plan.md"
        if not plan_md.is_file():
            return False, f"plan 目录在但缺 plan.md: {plan_md}(失真: 不是合法 plan)"
        size = plan_md.stat().st_size
        result.meta["plan_md_size"] = size
        if size == 0:
            return False, "plan.md 为空(0 字节)"
        return True, f"plan 存在, plan.md {size} 字节"


# ── file 适配器: 工作区内路径, 存在性 ───────────────────────────────────────────

class FileAdapter(BaseAdapter):
    name = "file"

    def _resolve_path(self, ref_id: str) -> Path:
        p = Path(ref_id)
        if p.is_absolute():
            return p
        return self.workspace_root / ref_id

    def resolve(self, ref_id: str, parsed: ParsedReference) -> ResolveResult:
        res = self._new(ref_id, parsed)
        abs_p = self._resolve_path(ref_id)
        res.location = str(abs_p)
        if not abs_p.exists():
            # anchor 可能被误剥离掉(如文件名带 #), 带回 anchor 再试一次
            if parsed.anchor:
                alt = self._resolve_path(f"{ref_id}#{parsed.anchor}")
                if alt.exists():
                    abs_p = alt
                    res.location = str(abs_p)
        if not abs_p.exists():
            res.exists = False
            res.error = f"文件不存在: {abs_p}"
            return res
        is_dir = abs_p.is_dir()
        res.exists = True
        res.meta = {
            "is_dir": is_dir,
            "abs_path": str(abs_p),
        }
        if not is_dir:
            res.meta["size"] = abs_p.stat().st_size
        return res

    def verify(self, result: ResolveResult) -> tuple[bool, str]:
        if not result.exists:
            return False, "路径不存在"
        p = Path(result.location)
        if not p.exists():
            return False, f"路径已消失: {result.location}(失真)"
        if p.is_dir():
            return True, "目录存在"
        size = p.stat().st_size
        result.meta["size"] = size
        return True, f"文件存在, {size} 字节"


# ── 分派器 ────────────────────────────────────────────────────────────────────

# 已知 kind → 适配器类(供 kind 前缀识别 + 分派)
_ADAPTER_CLASSES: dict[str, type[BaseAdapter]] = {
    "material": MaterialAdapter,
    "entity": EntityAdapter,
    "decision": DecisionAdapter,
    "review": ReviewAdapter,
    "whatnow": WhatnowAdapter,
    "note": NoteAdapter,
    "plan": PlanAdapter,
    "file": FileAdapter,
}

# 类型化双链命名空间前缀 → 归一到八个适配器 kind 的别名映射。
# (tags_and_wikilinks.md 列了 14 种前缀; 这里把语义等价的收敛到八适配器。)
_KIND_ALIASES: dict[str, str] = {
    "material": "material",
    "task": "whatnow",       # whatnow 任务
    "plan": "plan",
    "decision": "decision",
    "belief": "decision",
    "comment": "decision",
    "review": "review",
    "note": "note",
    "file": "file",
    "workspace": "file",
    "path": "file",
    # 以下双链前缀都是"注册表实体"(worker/agent/tool/hook/team/data/meta_io/standard/package)
    "entity": "entity",
    "worker": "entity",
    "agent": "entity",
    "tool": "entity",
    "hook": "entity",
    "team": "entity",
    "router": "entity",
    "format": "entity",
    "pipeline": "entity",
    "data": "entity",
    "meta_io": "entity",
    "standard": "entity",
    "package": "entity",
}

# 供 parse_reference 判断"是不是一个已知类型化前缀"
_KNOWN_KINDS = set(_KIND_ALIASES.keys())


def _canonical_kind(kind: str) -> str:
    return _KIND_ALIASES.get(kind, kind)


class UnifiedResolver:
    """统一引用解析器: 归一 → 自动识别 kind → 分派适配器 → (可选)自检。"""

    def __init__(self, workspace_root: Path | None = None) -> None:
        if workspace_root is None:
            from omnicompany.core.config import omni_workspace_root
            workspace_root = omni_workspace_root()
        self.workspace_root = Path(workspace_root)
        self._adapters: dict[str, BaseAdapter] = {}

    def _adapter(self, kind: str) -> Optional[BaseAdapter]:
        cls = _ADAPTER_CLASSES.get(kind)
        if cls is None:
            return None
        if kind not in self._adapters:
            self._adapters[kind] = cls(self.workspace_root)
        return self._adapters[kind]

    def resolve(self, ref: str, *, verify: bool = False) -> ResolveResult:
        """解析一个引用。verify=True 时附带回指自检(失真显式标记)。"""
        try:
            parsed = parse_reference(ref)
        except ResolveError as e:
            return ResolveResult(kind="unknown", id=str(ref), raw=str(ref), error=str(e))

        kind = parsed.kind
        if not kind:
            kind = _autodetect_kind(parsed.id, workspace_root=self.workspace_root)
        kind = _canonical_kind(kind)

        if not kind:
            return ResolveResult(
                kind="unknown", id=parsed.id, raw=str(ref),
                version=parsed.version, anchor=parsed.anchor,
                error=f"无法识别引用种类: {ref!r}(不匹配任何裸 id 形状, 也无类型化前缀)",
            )

        adapter = self._adapter(kind)
        if adapter is None:
            return ResolveResult(
                kind=kind, id=parsed.id, raw=str(ref),
                version=parsed.version, anchor=parsed.anchor,
                error=f"无 {kind} 适配器(已知: {sorted(_ADAPTER_CLASSES)})",
            )

        result = adapter.resolve(parsed.id, parsed)
        result.raw = str(ref)
        if verify:
            self._run_verify(adapter, result)
        return result

    @staticmethod
    def _run_verify(adapter: BaseAdapter, result: ResolveResult) -> None:
        try:
            ok, note = adapter.verify(result)
        except Exception as e:  # noqa: BLE001
            result.verified = False
            result.verify_note = f"自检抛异常(视为失真): {e}"
            return
        result.verified = bool(ok)
        result.verify_note = note


# ── 模块级便捷函数 ──────────────────────────────────────────────────────────────

_DEFAULT_RESOLVER: Optional[UnifiedResolver] = None


def get_resolver(workspace_root: Path | None = None) -> UnifiedResolver:
    """拿一个 UnifiedResolver(默认单例, 指定 workspace_root 时新建)。"""
    global _DEFAULT_RESOLVER
    if workspace_root is not None:
        return UnifiedResolver(workspace_root)
    if _DEFAULT_RESOLVER is None:
        _DEFAULT_RESOLVER = UnifiedResolver()
    return _DEFAULT_RESOLVER


def resolve_reference(ref: str, *, verify: bool = False, workspace_root: Path | None = None) -> ResolveResult:
    """一行解析一个引用。"""
    return get_resolver(workspace_root).resolve(ref, verify=verify)


__all__ = [
    "ResolveResult",
    "ResolveError",
    "ParsedReference",
    "parse_reference",
    "UnifiedResolver",
    "BaseAdapter",
    "MaterialAdapter",
    "EntityAdapter",
    "DecisionAdapter",
    "ReviewAdapter",
    "WhatnowAdapter",
    "NoteAdapter",
    "PlanAdapter",
    "FileAdapter",
    "get_resolver",
    "resolve_reference",
]
