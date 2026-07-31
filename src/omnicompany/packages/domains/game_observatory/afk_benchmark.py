from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Literal
from urllib.request import Request, urlopen

from pydantic import BaseModel, Field

from .models import BenchmarkTask, ObjectiveCheck, utc_now


class AfkHeroUpgradeSnapshot(BaseModel):
    id: str
    availability: Literal["contract_only", "verified"] = "contract_only"
    account_alias: str
    hero_id: str
    starting_level: int = Field(ge=1)
    target_level: int = Field(ge=2)
    resources_before: dict[str, int]
    attributes_before: dict[str, float]
    reset_strategy: str
    build_scope_id: str
    state_hash: str
    verified_at: str | None = None


class AfkHeroUpgradeOracle:
    """Source-backed task/oracle contract without mutating the Unity project."""

    SOURCE_FILES = {
        "upgrade_view": Path("Binary/Src/UI/Hero/View/HeroUpgradeView.lua"),
        "hero_model": Path("Binary/Src/UI/Hero/HeroModel.lua"),
        "tutorial": Path("Binary/Src/UI/Tutorial/Hero/HeroUpgrade/HeroUpgradeTutorialTask.lua"),
    }
    REQUIRED_SYMBOLS = {
        "upgrade_view": ["HeroUpgradeView", "GetLevelUpCost", "nextLevel"],
        "hero_model": ["GetLevelUpCost"],
        "tutorial": ["HeroUpgradeTutorialTask", "btn_upgrade"],
    }

    def __init__(self, source_root: Path = Path("D:/P4/main/Client")) -> None:
        self.source_root = source_root.resolve()

    @staticmethod
    def _bridge_json(path: str, *, port: int) -> dict[str, Any]:
        request = Request(
            f"http://127.0.0.1:{port}{path}",
            headers={"User-Agent": "game-observatory/0.2"},
        )
        with urlopen(request, timeout=5) as response:  # noqa: S310 - fixed loopback endpoint
            payload = json.loads(response.read().decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"Unity bridge {path} returned a non-object payload")
        return payload

    def source_evidence(self) -> dict[str, Any]:
        files: dict[str, Any] = {}
        all_symbols = True
        for name, relative in self.SOURCE_FILES.items():
            path = self.source_root / relative
            if not path.is_file():
                files[name] = {"path": str(path), "exists": False, "symbols": {}}
                all_symbols = False
                continue
            raw = path.read_bytes()
            text = raw.decode("utf-8", "replace")
            symbols = {symbol: symbol in text for symbol in self.REQUIRED_SYMBOLS[name]}
            all_symbols = all_symbols and all(symbols.values())
            files[name] = {
                "path": str(path),
                "exists": True,
                "sha256": hashlib.sha256(raw).hexdigest(),
                "bytes": len(raw),
                "symbols": symbols,
            }
        return {"ok": all_symbols, "root": str(self.source_root), "files": files}

    def task(self, snapshot: AfkHeroUpgradeSnapshot) -> BenchmarkTask:
        return BenchmarkTask(
            id="task.afk.hero-upgrade.v1",
            title="从固定账号快照完成一次英雄升级",
            start_state=f"snapshot={snapshot.id}; hero={snapshot.hero_id}; level={snapshot.starting_level}",
            goal=f"hero {snapshot.hero_id} reaches level {snapshot.target_level}",
            allowed_actions=["tap", "swipe", "wait", "back", "reset"],
            reset_method=snapshot.reset_strategy,
            checks=[
                ObjectiveCheck(
                    id="hero_level_delta",
                    description="目标英雄等级严格增加 1",
                    expected=1,
                ),
                ObjectiveCheck(
                    id="resource_delta_matches_oracle",
                    description="资源扣减与 GetLevelUpCost 白盒结果一致",
                    expected=True,
                ),
                ObjectiveCheck(
                    id="attributes_match_oracle",
                    description="HP/ATK/DEF 的最终值与白盒属性计算一致",
                    expected=True,
                ),
                ObjectiveCheck(
                    id="ui_before_after_visible",
                    description="升级前后等级、资源与属性均有外部可见证据",
                    expected=True,
                ),
                ObjectiveCheck(
                    id="source_formula_present",
                    description="当前源码仍包含升级成本与预览计算入口",
                    expected=True,
                ),
            ],
            metadata={
                "target_state": "hero_upgrade",
                "task_prompt": (
                    f"在隔离研究快照 {snapshot.id} 中，将英雄 {snapshot.hero_id} 从 "
                    f"{snapshot.starting_level} 级升级到 {snapshot.target_level} 级；禁止购买、聊天、"
                    "改名、组织操作和账号绑定；资源不足立即停止，不注入资源。"
                ),
                "max_steps": 30,
                "snapshot": snapshot.model_dump(mode="json"),
                "safety": {
                    "forbidden": [
                        "purchase",
                        "chat",
                        "rename",
                        "join_or_leave_organization",
                        "account_binding",
                        "arbitrary_lua_mutation",
                    ],
                    "stop_on_insufficient_resources": True,
                },
                "oracle": {
                    "source_files": [str(path) for path in self.SOURCE_FILES.values()],
                    "before_after_fields": ["hero_level", "resources", "HP", "ATK", "DEF"],
                },
            },
        )

    def preflight(
        self,
        snapshot_path: Path | None,
        *,
        bridge_port: int = 18820,
    ) -> dict[str, Any]:
        errors: list[str] = []
        try:
            status = self._bridge_json("/status", port=bridge_port)
            health = self._bridge_json("/health", port=bridge_port)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            status = {"error": str(exc)}
            health = {"ok": False}
            errors.append(f"Unity AgentBridge unavailable: {exc}")
        source = self.source_evidence()
        if not source["ok"]:
            errors.append("required AFK source symbols are missing")
        project_path = str(status.get("projectPath") or "").replace("\\", "/").rstrip("/")
        if project_path.endswith("/Assets"):
            project_path = project_path.removesuffix("/Assets")
        if project_path.lower() != str(self.source_root).replace("\\", "/").lower():
            errors.append(f"AgentBridge project mismatch: {status.get('projectPath')!r}")
        if not status.get("isPlaying"):
            errors.append("Unity Editor is not in PlayMode")
        snapshot: AfkHeroUpgradeSnapshot | None = None
        if not snapshot_path or not snapshot_path.is_file():
            errors.append("verified fixed-account snapshot manifest is missing")
        else:
            try:
                snapshot = AfkHeroUpgradeSnapshot.model_validate_json(
                    snapshot_path.read_text(encoding="utf-8")
                )
                if snapshot.availability != "verified":
                    errors.append("snapshot manifest is contract_only, not verified")
                if snapshot.target_level != snapshot.starting_level + 1:
                    errors.append("snapshot target_level must equal starting_level + 1")
            except (OSError, ValueError) as exc:
                errors.append(f"invalid snapshot manifest: {exc}")
        result = {
            "schema": "game-observatory.afk-hero-upgrade-preflight.v1",
            "generated_at": utc_now(),
            "ready": not errors,
            "bridge": {"port": bridge_port, "status": status, "health": health},
            "source": source,
            "snapshot": snapshot.model_dump(mode="json") if snapshot else None,
            "errors": errors,
        }
        return result


def write_contract_snapshot(path: Path) -> Path:
    payload = AfkHeroUpgradeSnapshot(
        id="snapshot.afk.hero-upgrade.contract-only",
        availability="contract_only",
        account_alias="isolated-research-account",
        hero_id="fixture-hero",
        starting_level=20,
        target_level=21,
        resources_before={"hero_exp": 1000, "hero_essence": 100},
        attributes_before={"HP": 100.0, "ATK": 10.0, "DEF": 5.0},
        reset_strategy="restore approved Unity QA account snapshot",
        build_scope_id="scope.afk.local-source.2026-07-13",
        state_hash="contract-only-not-a-real-account-state",
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload.model_dump_json(indent=2), encoding="utf-8")
    return path