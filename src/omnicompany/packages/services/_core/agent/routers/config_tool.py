# [OMNI] origin=claude-code domain=services/agent/routers ts=2026-05-04T00:00:00Z type=infrastructure
"""ConfigToolRouter · 读/改 omnicompany 项目配置 SingleTool.

对齐 claude-code ConfigTool, 但目标对象是 omnicompany 自己的 config 文件:
  - omnicompany/config/*.yaml (项目配置)
  - omnicompany/.omni/*.json (本地状态, 极少用)
  - 可选 ~/.claude 全局配置 (claude-code 风格), omnicompany 不强用

操作:
  - get <key>: 读单个配置项
  - list: 列所有顶层 key
  - set <key> <value>: 改 (注: 真改前需要 ToolContext.allowed_config_targets 白名单, 跟 write_file 一样)

边界:
  - omnicompany 没有强统一配置中心 (yaml 散在 config/ 下), 本工具只动 config/global.yaml 这种已知文件
  - secret 类配置 (.env / api keys) 不读不写
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import ClassVar

from omnicompany.packages.services._core.agent.routers.single_tool import (
    SingleToolRouter,
    ToolContext,
    ToolExecutionError,
)

logger = logging.getLogger(__name__)


_DEFAULT_CONFIG_FILE = "config/global.yaml"  # omnicompany 主配置约定


class ConfigToolRouter(SingleToolRouter):
    """Read or modify omnicompany project configuration values."""

    CONSUMED_META_IO: ClassVar[tuple[str, ...]] = ("meta_io.fs.read_file",)
    PRODUCED_META_IO: ClassVar[tuple[str, ...]] = ("meta_io.fs.modify_file",)

    TOOL_NAME: ClassVar[str] = "Config"
    DESCRIPTION: ClassVar[str] = (
        "Read or modify omnicompany configuration values.\n"
        "\n"
        "Operations:\n"
        "- `get`: read value at `key` (dotted path, e.g. 'llm.model')\n"
        "- `list`: list all top-level keys\n"
        "- `set`: write value at `key` (requires ToolContext.allowed_config_targets allowlist)\n"
        "\n"
        "Notes:\n"
        "- Secrets (.env, api keys) are NOT readable through this tool — refused.\n"
        "- `set` operations need a write allowlist injected by the calling Worker.\n"
        "- Default config file is `config/global.yaml` relative to project root."
    )
    INPUT_SCHEMA: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "enum": ["get", "list", "set"],
                "description": "What to do",
            },
            "key": {
                "type": "string",
                "description": "Dotted key path (required for get/set)",
            },
            "value": {
                "description": "New value (required for set; any JSON-compatible type)",
            },
            "config_file": {
                "type": "string",
                "description": f"Override config file path (default: {_DEFAULT_CONFIG_FILE})",
            },
        },
        "required": ["operation"],
    }
    IS_CONCURRENCY_SAFE: ClassVar[bool] = False
    IS_READONLY: ClassVar[bool] = False

    def _execute(self, args: dict, ctx: ToolContext) -> str:
        op = args.get("operation", "")
        if op not in ("get", "list", "set"):
            raise ToolExecutionError(f"operation must be get/list/set, got {op!r}")

        config_file = args.get("config_file") or _DEFAULT_CONFIG_FILE
        # 拒绝 secrets
        forbidden = (".env", "secrets", "api_keys", "credentials")
        for f in forbidden:
            if f in config_file.lower():
                raise ToolExecutionError(
                    f"Config tool refuses to access {config_file!r} — "
                    f"secret-class file. Use environment variables for secrets."
                )

        cfg_path = Path(config_file)
        if not cfg_path.is_absolute():
            project_root = Path(ctx.project_root) if ctx.project_root else Path.cwd()
            cfg_path = project_root / cfg_path

        if not cfg_path.exists() and op != "set":
            raise ToolExecutionError(f"config file does not exist: {cfg_path}")

        if op == "list":
            return self._list_keys(cfg_path)
        if op == "get":
            key = args.get("key", "").strip()
            if not key:
                raise ToolExecutionError("get requires `key`")
            return self._get_value(cfg_path, key)
        if op == "set":
            key = args.get("key", "").strip()
            if not key:
                raise ToolExecutionError("set requires `key`")
            if "value" not in args:
                raise ToolExecutionError("set requires `value`")
            return self._set_value(cfg_path, key, args["value"], ctx)

        raise ToolExecutionError(f"unreachable: op={op}")

    def _load(self, cfg_path: Path) -> dict:
        try:
            import yaml  # type: ignore
        except ImportError:
            raise ToolExecutionError("PyYAML not installed — cannot read yaml config")
        try:
            with cfg_path.open(encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        except Exception as e:
            raise ToolExecutionError(f"failed to parse {cfg_path}: {e}")
        if not isinstance(data, dict):
            raise ToolExecutionError(f"config root must be a dict, got {type(data).__name__}")
        return data

    def _save(self, cfg_path: Path, data: dict) -> None:
        try:
            import yaml  # type: ignore
        except ImportError:
            raise ToolExecutionError("PyYAML not installed — cannot write yaml config")
        try:
            with cfg_path.open("w", encoding="utf-8") as f:
                yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)
        except Exception as e:
            raise ToolExecutionError(f"failed to write {cfg_path}: {e}")

    def _list_keys(self, cfg_path: Path) -> str:
        data = self._load(cfg_path)
        if not data:
            return "(config is empty)"
        return "\n".join(f"- {k}" for k in data.keys())

    def _get_value(self, cfg_path: Path, key: str) -> str:
        data = self._load(cfg_path)
        cur: object = data
        for part in key.split("."):
            if not isinstance(cur, dict) or part not in cur:
                raise ToolExecutionError(f"key {key!r} not found in {cfg_path}")
            cur = cur[part]
        if isinstance(cur, (dict, list)):
            import json
            return json.dumps(cur, ensure_ascii=False, indent=2)
        return str(cur)

    def _set_value(self, cfg_path: Path, key: str, value, ctx: ToolContext) -> str:
        # 白名单: ctx.allowed_config_targets 必须列出本 cfg_path 才允许 set
        allowed = getattr(ctx, "allowed_config_targets", None) or ()
        allowed_resolved = {str(Path(p).resolve()) for p in allowed}
        if str(cfg_path.resolve()) not in allowed_resolved:
            raise ToolExecutionError(
                f"Config.set REFUSED: {cfg_path} not in tool context's "
                f"allowed_config_targets. Inject via Worker's build_tool_context()."
            )

        data = self._load(cfg_path) if cfg_path.exists() else {}
        parts = key.split(".")
        cur = data
        for part in parts[:-1]:
            if part not in cur or not isinstance(cur[part], dict):
                cur[part] = {}
            cur = cur[part]
        cur[parts[-1]] = value
        self._save(cfg_path, data)
        return f"Set {key} in {cfg_path}"
