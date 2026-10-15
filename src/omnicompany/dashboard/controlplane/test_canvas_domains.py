"""canvas_domains.py 单元测试: 配置解析 / disk 域读取 / 提审聚合（不依赖 dashboard 运行）。

运行: python -m pytest test_canvas_domains.py -q
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from omnicompany.dashboard.controlplane import canvas_domains as cd


def _make_canvas_document(title: str, cards: list[dict], revision: int = 3) -> dict:
    return {
        "version": 1,
        "sessionId": "external:test",
        "title": title,
        "revision": revision,
        "updatedAt": "2026-08-16T00:00:00.000Z",
        "cards": cards,
        "relations": [],
    }


def _submission_card(card_id: str, title: str, state: str, submitted_at: str) -> dict:
    return {
        "id": card_id, "kind": "text", "title": title, "adapter": "text",
        "source": {"kind": "file", "path": f"{card_id}.md", "token": "t"},
        "x": 60, "y": 72, "width": 560, "height": 420,
        "submission": {"state": state, "submittedAt": submitted_at, "submittedBy": "cli:test"},
    }


class ParseEntryTest(unittest.TestCase):
    def test_disk_entry(self) -> None:
        domain = cd._parse_entry({
            "id": "workshop", "transport": "disk", "root": "E:/x",
            "product_ref": "spark-workshop", "workspace_kinds": ["workshop-canvas"],
        }, "test")
        self.assertEqual(domain.transport, "disk")
        self.assertEqual(domain.access, "readonly")
        self.assertEqual(domain.capabilities, ("canvas", "review"))
        self.assertEqual(domain.product_ref, "spark-workshop")
        self.assertEqual(domain.workspace_kinds, ("workshop-canvas",))

    def test_protocol_entry_requires_http_endpoint(self) -> None:
        with self.assertRaises(ValueError):
            cd._parse_entry({"id": "c", "transport": "protocol", "endpoint": "ftp://x"}, "test")
        domain = cd._parse_entry(
            {"id": "c", "transport": "protocol", "endpoint": "https://x.example/", "access": "readwrite"}, "test")
        self.assertEqual(domain.endpoint, "https://x.example")

    def test_invalid_fields(self) -> None:
        with self.assertRaises(ValueError):
            cd._parse_entry({"id": "Bad!", "transport": "disk", "root": "/x"}, "test")
        with self.assertRaises(ValueError):
            cd._parse_entry({"id": "a", "transport": "disk"}, "test")  # 缺 root
        with self.assertRaises(ValueError):
            cd._parse_entry({"id": "a", "transport": "disk", "root": "/x", "capabilities": ["bogus"]}, "test")
        with self.assertRaises(ValueError):
            cd._parse_entry({"id": "a", "transport": "smoke"}, "test")


class DiskDomainReadTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "canvases").mkdir()
        self.domain = cd.CanvasDomain(
            id="t", title="T", transport="disk", access="readonly",
            capabilities=("canvas", "review"), root=str(self.root),
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _write_canvas(self, canvas_id: str, document: dict) -> None:
        (self.root / "canvases" / f"{canvas_id}.json").write_text(
            json.dumps(document, ensure_ascii=False), encoding="utf-8")

    def test_list_and_read(self) -> None:
        self._write_canvas("session-a", _make_canvas_document("画布A", [
            _submission_card("c1", "卡一", "unreviewed", "2026-08-16T01:00:00.000Z"),
        ]))
        (self.root / "canvases" / "corrupt.json").write_text("{not json", encoding="utf-8")
        items = cd.list_domain_canvases(self.domain)
        self.assertEqual([item["document_id"] for item in items], ["session-a"])  # 损坏文件跳过
        document = cd._read_domain_canvas_document(self.domain, "session-a")
        self.assertEqual(document["title"], "画布A")
        self.assertIsNone(cd._read_domain_canvas_document(self.domain, "../escape"))

    def test_review_inbox_aggregates_and_orders(self) -> None:
        self._write_canvas("session-a", _make_canvas_document("画布A", [
            _submission_card("c2", "已看", "seen", "2026-08-16T02:00:00.000Z"),
            {"id": "g1", "kind": "group", "title": "组", "submission": {"state": "unreviewed"}},  # 组卡忽略
            {"id": "n1", "kind": "text", "title": "无提审"},
        ]))
        self._write_canvas("session-b", _make_canvas_document("画布B", [
            _submission_card("c1", "未看", "unreviewed", "2026-08-16T01:00:00.000Z"),
        ]))
        entries = cd.domain_review_inbox(self.domain)
        self.assertEqual([(e["cardId"], e["state"]) for e in entries],
                         [("c1", "unreviewed"), ("c2", "seen")])
        self.assertTrue(all(e["domain"] == "t" and e["ref"].startswith("omni://domain/t/canvas/")
                            for e in entries))

    def test_protocol_domain_inbox_empty(self) -> None:
        protocol = cd.CanvasDomain(id="p", title="P", transport="protocol", access="readonly",
                                   capabilities=("review",), endpoint="https://x.example")
        self.assertEqual(cd.domain_review_inbox(protocol), [])

    def test_product_filter_hides_foreign_canvas_from_list_and_direct_read(self) -> None:
        filtered = cd.CanvasDomain(
            id="workshop", title="Spark Workshop", transport="disk", access="readonly",
            capabilities=("canvas", "review"), root=str(self.root), product_ref="spark-workshop",
            workspace_kinds=("workshop-canvas",),
        )
        spark = _make_canvas_document("Spark", [])
        spark["_workspaceRef"] = {"kind": "workshop-canvas", "ref": "canvas:spark"}
        walker = _make_canvas_document("Walker", [])
        walker["_workspaceRef"] = {"kind": "workspace", "ref": "external:walker"}
        self._write_canvas("spark", spark)
        self._write_canvas("walker", walker)

        self.assertEqual([item["document_id"] for item in cd.list_domain_canvases(filtered)], ["spark"])
        self.assertIsNotNone(cd._read_domain_canvas_document(filtered, "spark"))
        self.assertIsNone(cd._read_domain_canvas_document(filtered, "walker"))


class MaterialAwarenessTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.material_root = Path(self._tmp.name) / "material"
        (self.material_root / "notes").mkdir(parents=True)
        (self.material_root / "outputs").mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _domain(self, capabilities=("canvas", "material", "review")) -> cd.CanvasDomain:
        return cd.CanvasDomain(id="w", title="W", transport="disk", access="readonly",
                               capabilities=capabilities, root=str(self.material_root / "notes"))

    def test_list_materials_from_records(self) -> None:
        records = [{
            "schema_version": "workshop.material-output-record/1",
            "material": {"id": "material:x/out/a", "title": "材料A", "labels": ["交付"]},
        }, {"material": {"id": "material:x/out/b"}}, {"bogus": True}]
        (self.material_root / "outputs" / "records.json").write_text(
            json.dumps(records, ensure_ascii=False), encoding="utf-8")
        items = cd.list_domain_materials(self._domain())
        self.assertEqual([(i["id"], i["title"], i["labels"]) for i in items], [
            ("material:x/out/a", "材料A", ["交付"]),
            ("material:x/out/b", "material:x/out/b", []),
        ])
        self.assertTrue(all(i["ref"].startswith("omni://domain/w/material/") for i in items))

    def test_missing_or_corrupt_records(self) -> None:
        self.assertEqual(cd.list_domain_materials(self._domain()), [])
        (self.material_root / "outputs" / "records.json").write_text("{bad", encoding="utf-8")
        self.assertEqual(cd.list_domain_materials(self._domain()), [])

    def test_product_filter_hides_foreign_materials(self) -> None:
        (self.material_root / "outputs" / "records.json").write_text(json.dumps([
            {"material": {"id": "material:spark-workshop/out/a"}},
            {"material": {"id": "material:walker/out/b"}},
        ]), encoding="utf-8")
        domain = cd.CanvasDomain(
            id="w", title="W", transport="disk", access="readonly",
            capabilities=("material",), root=str(self.material_root / "notes"),
            product_ref="spark-workshop",
        )
        self.assertEqual([item["id"] for item in cd.list_domain_materials(domain)], [
            "material:spark-workshop/out/a",
        ])

    def test_without_material_capability(self) -> None:
        (self.material_root / "outputs" / "records.json").write_text(
            json.dumps([{"material": {"id": "m"}}]), encoding="utf-8")
        self.assertEqual(cd.list_domain_materials(self._domain(capabilities=("canvas",))), [])


if __name__ == "__main__":
    unittest.main()
