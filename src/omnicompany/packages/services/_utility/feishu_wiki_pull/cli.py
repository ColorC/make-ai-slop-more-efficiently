# [OMNI] origin=omnicompany domain=utility/feishu_wiki_pull ts=2026-07-22T14:09:05Z type=script status=active
# [OMNI] summary="提供不依赖 lark-cli 登录态的 auth-start 与 pull 命令行入口"
# [OMNI] why="让应用凭据和可选用户授权都通过独立进程内通道完成，不污染共享机器配置"
# [OMNI] tags=feishu,wiki,cli,auth,pull

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

from .client import FeishuApiError, FeishuClient
from .puller import WikiPuller, write_failure_report


DEFAULT_SCOPES = " ".join(
    [
        "wiki:node:retrieve",
        "docs:document.content:read",
        "docs:document:export",
        "docx:document:readonly",
        "drive:drive.metadata:readonly",
        "drive:file:download",
    ]
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="feishu-wiki-pull",
        description="隔离鉴权并完整拉取collab platform Wiki；不读取或写入 ~/.lark-cli。",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    auth = subparsers.add_parser("auth-start", help="发起独立的用户 device authorization")
    auth.add_argument("--app-id", default=os.environ.get("FEISHU_WIKI_PULL_APP_ID", ""))
    auth.add_argument("--scopes", default=DEFAULT_SCOPES)
    auth.add_argument("--app-secret-stdin", action="store_true")

    pull = subparsers.add_parser("pull", help="递归拉取空间全部节点")
    pull.add_argument("--space", required=True, help="数字 space_id 或 /wiki/space/<id> URL")
    pull.add_argument("--output-dir", required=True)
    pull.add_argument("--auth", choices=("bot", "device", "user-token"), default="bot")
    pull.add_argument("--app-id", default=os.environ.get("FEISHU_WIKI_PULL_APP_ID", ""))
    pull.add_argument("--app-secret-stdin", action="store_true")
    pull.add_argument("--device-code", default="")
    pull.add_argument("--device-interval", type=int, default=5)
    pull.add_argument("--device-expires-in", type=int, default=600)
    pull.add_argument("--overwrite", action="store_true")
    pull.add_argument("--no-native-docx", action="store_true")
    pull.add_argument("--export-timeout", type=float, default=180.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    space_id = ""
    source_url = ""
    try:
        if args.command == "auth-start":
            app_secret = _secret(args.app_secret_stdin)
            if not args.app_id:
                raise FeishuApiError("缺少 --app-id 或 FEISHU_WIKI_PULL_APP_ID")
            authorization = FeishuClient.request_device_authorization(
                args.app_id, app_secret, args.scopes
            )
            del app_secret
            print(json.dumps(authorization.__dict__, ensure_ascii=False, indent=2))
            return 0

        space_id, source_url = _space(args.space)
        app_secret = ""
        if args.auth in ("bot", "device"):
            if not args.app_id:
                raise FeishuApiError("缺少 --app-id 或 FEISHU_WIKI_PULL_APP_ID")
            app_secret = _secret(args.app_secret_stdin)
        if args.auth == "bot":
            token = FeishuClient.tenant_access_token(args.app_id, app_secret)
        elif args.auth == "device":
            if not args.device_code:
                raise FeishuApiError("--auth device 必须提供 --device-code")
            token = FeishuClient.poll_device_access_token(
                args.app_id,
                app_secret,
                args.device_code,
                interval=args.device_interval,
                expires_in=args.device_expires_in,
            )
        else:
            token = os.environ.get("FEISHU_WIKI_PULL_USER_ACCESS_TOKEN", "")
            if not token and not sys.stdin.isatty():
                token = sys.stdin.read().strip()
            if not token:
                raise FeishuApiError(
                    "缺少 FEISHU_WIKI_PULL_USER_ACCESS_TOKEN，或通过 stdin 传入 user access token"
                )
        del app_secret

        output_dir = Path(args.output_dir)
        result = WikiPuller(
            FeishuClient(token),
            output_dir,
            overwrite=args.overwrite,
            include_native_docx=not args.no_native_docx,
            export_timeout=args.export_timeout,
        ).pull(
            space_id,
            source_url=source_url,
            auth_identity=args.auth,
            app_id=args.app_id,
        )
        del token
        print(
            json.dumps(
                {
                    "status": result.status,
                    "output_dir": str(result.output_dir),
                    "manifest": str(result.manifest_path),
                    "report": str(result.report_path),
                    "total": result.total,
                    "succeeded": result.succeeded,
                    "partial": result.partial,
                    "failed": result.failed,
                    "unsupported": result.unsupported,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0 if result.status == "success" else 2
    except FeishuApiError as error:
        if args.command == "pull":
            if not space_id:
                space_id, source_url = _space_for_report(args.space)
            failure = write_failure_report(
                Path(args.output_dir),
                space_id=space_id,
                source_url=source_url,
                auth_identity=args.auth,
                app_id=args.app_id,
                error=error,
            )
            payload = {"ok": False, "error": error.as_dict(), "failure_report": str(failure)}
        else:
            payload = {"ok": False, "error": error.as_dict()}
        print(json.dumps(payload, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1


def _secret(force_stdin: bool) -> str:
    value = os.environ.get("FEISHU_WIKI_PULL_APP_SECRET", "")
    if not value and (force_stdin or not sys.stdin.isatty()):
        value = sys.stdin.read().strip()
    if not value:
        raise FeishuApiError(
            "缺少 FEISHU_WIKI_PULL_APP_SECRET；也可加 --app-secret-stdin 从 stdin 读取"
        )
    return value


def _space(value: str) -> tuple[str, str]:
    value = value.strip()
    if value.isdecimal():
        return value, ""
    match = re.search(r"/wiki/space/(\d+)", value)
    if not match:
        raise FeishuApiError("--space 必须是数字 space_id 或包含 /wiki/space/<id> 的 URL")
    return match.group(1), value


def _space_for_report(value: str) -> tuple[str, str]:
    """Best-effort source description that must never mask the original error."""
    stripped = value.strip()
    if stripped.isdecimal():
        return stripped, ""
    match = re.search(r"/wiki/space/(\d+)", stripped)
    return (match.group(1) if match else "invalid", stripped)


if __name__ == "__main__":
    raise SystemExit(main())
