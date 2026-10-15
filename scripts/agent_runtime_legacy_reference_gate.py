"""Semantic reference gate for deleting Omni's legacy Python Agent inner loop."""

from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
import sys
from typing import Any
import warnings


LEGACY_SYMBOLS = frozenset(
    {
        "AgentNodeLoop",
        "AgentNodeLoopWithRetry",
        "ConfigurableAgent",
        "run_json_agent",
    }
)
AUTHORITY_PREFIXES = (
    "src/omnicompany/packages/services/_core/agent/",
    "src/omnicompany/runtime/agent/",
)


def _excluded(path: Path) -> bool:
    return "_graveyard" in path.parts or any(
        part.startswith("_archive") or part.startswith("_legacy")
        for part in path.parts
    )


def _tail(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def scan(root: Path) -> dict[str, Any]:
    references: list[dict[str, Any]] = []
    parse_errors: list[dict[str, str]] = []
    for path in sorted(root.rglob("*.py")):
        if _excluded(path.relative_to(root)):
            continue
        relative = path.relative_to(root.parent.parent).as_posix()
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", SyntaxWarning)
                tree = ast.parse(
                    path.read_text(encoding="utf-8-sig"),
                    filename=str(path),
                )
        except (OSError, SyntaxError, UnicodeError) as error:
            parse_errors.append({"file": relative, "error": str(error)})
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if alias.name in LEGACY_SYMBOLS:
                        references.append(
                            {
                                "file": relative,
                                "line": node.lineno,
                                "kind": "import",
                                "symbol": alias.name,
                            }
                        )
            elif isinstance(node, ast.ClassDef):
                for base in node.bases:
                    symbol = _tail(base)
                    if symbol in LEGACY_SYMBOLS:
                        references.append(
                            {
                                "file": relative,
                                "line": node.lineno,
                                "kind": "base",
                                "symbol": symbol,
                            }
                        )
            elif isinstance(node, ast.Call):
                symbol = _tail(node.func)
                if symbol in LEGACY_SYMBOLS:
                    references.append(
                        {
                            "file": relative,
                            "line": node.lineno,
                            "kind": "call",
                            "symbol": symbol,
                        }
                    )
    unique = {
        (item["file"], item["line"], item["kind"], item["symbol"]): item
        for item in references
    }
    references = [unique[key] for key in sorted(unique)]
    authority = [
        item
        for item in references
        if str(item["file"]).startswith(AUTHORITY_PREFIXES)
    ]
    consumers = [item for item in references if item not in authority]
    return {
        "schema_version": 1,
        "symbols": sorted(LEGACY_SYMBOLS),
        "consumer_reference_count": len(consumers),
        "consumer_file_count": len({item["file"] for item in consumers}),
        "authority_reference_count": len(authority),
        "authority_file_count": len({item["file"] for item in authority}),
        "parse_errors": parse_errors,
        "consumer_references": consumers,
        "authority_references": authority,
    }


def check_baseline(report: dict[str, Any], baseline: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    for field in (
        "consumer_reference_count",
        "consumer_file_count",
        "authority_reference_count",
        "authority_file_count",
    ):
        current = int(report[field])
        maximum = int(baseline[field])
        if current > maximum:
            failures.append(f"{field} increased: {current} > {maximum}")
    if report["parse_errors"]:
        failures.append(f"parse errors: {len(report['parse_errors'])}")
    return failures


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source-root",
        type=Path,
        default=Path("src/omnicompany"),
    )
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--require-zero", action="store_true")
    parser.add_argument("--details", action="store_true")
    args = parser.parse_args()

    report = scan(args.source_root.resolve())
    failures: list[str] = []
    if args.baseline is not None:
        baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
        failures.extend(check_baseline(report, baseline))
    if args.require_zero and (
        report["consumer_reference_count"] or report["authority_reference_count"]
    ):
        failures.append("legacy Agent references have not reached zero")

    output = report if args.details else {
        key: value
        for key, value in report.items()
        if key not in {"consumer_references", "authority_references"}
    }
    output["gate"] = "fail" if failures else "pass"
    output["failures"] = failures
    print(json.dumps(output, ensure_ascii=False, indent=2))
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
