# [OMNI] origin=claude-code domain=omnicompany/runtime ts=2026-04-08T03:23:44Z
# [OMNI] material_id="material:runtime.storage.tool_pattern_registry.hawkes_energy.py"
"""
跨任务持久化工具模板 Registry（Tool Pattern Registry）

注意：此模块与 protocol/format.py FormatRegistry（语义类型系统）无关。
这里的"Format"指 LAP 理论中"可复用的操作模式"，原名 PersistentFormatRegistry，
重命名为 PersistentToolPatternRegistry 以消除与语义 Format 的命名歧义。

这是 LAP 与 Live-SWE-agent 的核心差异所在：
  Live-SWE-agent：工具在任务内创建，任务结束即消失（per-task ephemeral）
  LAP：工具/Format 跨任务持久化，复用率是进化信号（cross-task accumulation）

工具模板具体形态 = 可复用的脚本/模式，由 agent 在解题过程中发现并命名。
每次 register_tool 调用 = 模板注册。
跨任务 used_in_tasks 增长 = 模板能量（Hawkes 激活）。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


DEFAULT_REGISTRY_PATH = Path("data/format_registry.json")


class PersistentFormatRegistry:
    """跨任务持久化的 Format Registry

    LAP 理论对应：
        register()       → Format 注册（首次发现）
        record_usage()   → Hawkes 激活事件（成功使用一次）
        get_reuse_rate() → format_reuse_rate（进化 reward 的组成部分）
        get_energy()     → ε_i(t)（基于 Hawkes 公式的当前能量）
    """

    def __init__(self, registry_path: Path | str = DEFAULT_REGISTRY_PATH):
        self.path = Path(registry_path)
        self._data: dict[str, Any] = self._load()

    def _load(self) -> dict[str, Any]:
        if self.path.exists():
            try:
                return json.loads(self.path.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {"formats": {}, "stats": {"total_tasks_run": 0}}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self._data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    # -- Format 注册 --

    def register(
        self,
        tool_id: str,
        name: str,
        description: str,
        script: str,
        task_id: str | None = None,
    ) -> str:
        """注册一个新 Format（agent 发现的可复用模式）

        Args:
            tool_id:     唯一标识（snake_case，如 "find_syntax_errors"）
            name:        人类可读名称
            description: 语义描述（这个工具做什么，适合什么场景）
            script:      具体脚本内容或命令模板
            task_id:     当前任务 ID（记录首次发现于哪个任务）

        Returns:
            注册结果描述
        """
        if tool_id in self._data["formats"]:
            # 已存在则更新描述（允许 agent 改进已有 Format）
            existing = self._data["formats"][tool_id]
            existing["description"] = description
            existing["script"] = script
            self._save()
            return f"Format '{tool_id}' updated. Used in {len(existing['used_in_tasks'])} tasks so far."

        self._data["formats"][tool_id] = {
            "id": tool_id,
            "name": name,
            "description": description,
            "script": script,
            "registered_at": time.time(),
            "first_task": task_id,
            "used_in_tasks": [task_id] if task_id else [],
            "use_timestamps": [time.time()] if task_id else [],
            "energy": 1.0,  # 初始能量（Hawkes 基线）
        }
        self._save()
        return f"Format '{tool_id}' registered. This tool will persist across all future tasks."

    def record_usage(self, tool_id: str, task_id: str) -> None:
        """记录 Format 在某任务中被使用（Hawkes 激活事件）"""
        if tool_id not in self._data["formats"]:
            return
        fmt = self._data["formats"][tool_id]
        if task_id not in fmt["used_in_tasks"]:
            fmt["used_in_tasks"].append(task_id)
        fmt["use_timestamps"].append(time.time())
        # Hawkes 能量更新：ε(t) = μ + α·∑e^{-β(t-t_k)}
        fmt["energy"] = self._compute_energy(fmt["use_timestamps"])
        self._save()

    def _compute_energy(
        self,
        timestamps: list[float],
        mu: float = 0.5,   # 基线能量
        alpha: float = 1.0, # 每次激励强度
        beta: float = 0.01, # 衰减率（单位：秒）
    ) -> float:
        """Hawkes 过程能量公式（R7a）

        ε_i(t) = μ + α · ∑_{k} e^{-β(t - t_k)}
        """
        now = time.time()
        excited = sum(alpha * (2.718 ** (-beta * (now - t))) for t in timestamps)
        return round(mu + excited, 4)

    # -- 查询 --

    def get_all(self) -> dict[str, dict]:
        """返回所有已注册的 Format"""
        return self._data["formats"]

    def get(self, tool_id: str) -> dict | None:
        return self._data["formats"].get(tool_id)

    def get_reuse_rate(self, total_tasks: int | None = None) -> float:
        """计算跨任务平均复用率（进化 reward 的 w2 项）

        reuse_rate = mean(len(used_in_tasks) / total_tasks) across all formats
        """
        total = total_tasks or max(self._data["stats"]["total_tasks_run"], 1)
        formats = self._data["formats"]
        if not formats:
            return 0.0
        rates = [len(f["used_in_tasks"]) / total for f in formats.values()]
        return round(sum(rates) / len(rates), 4)

    def get_top_formats(self, n: int = 5) -> list[dict]:
        """按能量排序的 Top N Format（最活跃的可复用模式）"""
        return sorted(
            self._data["formats"].values(),
            key=lambda f: f["energy"],
            reverse=True,
        )[:n]

    def increment_task_count(self) -> int:
        """每完成一个任务调用，更新总任务计数"""
        self._data["stats"]["total_tasks_run"] += 1
        self._save()
        return self._data["stats"]["total_tasks_run"]

    def summary(self) -> str:
        """人类可读的 Registry 状态摘要"""
        formats = self._data["formats"]
        total_tasks = self._data["stats"]["total_tasks_run"]
        if not formats:
            return "Format Registry is empty. No reusable patterns discovered yet."
        top = self.get_top_formats(3)
        lines = [
            f"Format Registry: {len(formats)} patterns across {total_tasks} tasks",
            f"Reuse rate: {self.get_reuse_rate(total_tasks):.1%}",
            "Top formats by energy:",
        ]
        for f in top:
            lines.append(
                f"  [{f['energy']:.2f}] {f['id']}: {f['description'][:60]}"
                f" (used in {len(f['used_in_tasks'])} tasks)"
            )
        return "\n".join(lines)
