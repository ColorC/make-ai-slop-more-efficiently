"""命令行入口。

  python -m omnicompany.packages.narrative_studio              # 启动服务(:8330)
  python -m omnicompany.packages.narrative_studio serve --port 8330
  python -m omnicompany.packages.narrative_studio import-vilo  # 从讨论稿重生成 vilo 项目
"""

from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="narrative_studio")
    sub = parser.add_subparsers(dest="cmd")

    p_serve = sub.add_parser("serve", help="启动 HTTP 服务")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8330)

    sub.add_parser("import-vilo", help="从 vilo 讨论稿重生成项目")

    args = parser.parse_args(argv)
    cmd = args.cmd or "serve"

    if cmd == "import-vilo":
        from .api import VILO_REPO, _active_root
        from .importer import import_vilo
        from . import storage
        p = import_vilo(VILO_REPO)
        storage.save_project(p, _active_root())
        print(f"已重生成 vilo 项目 → {_active_root()}  (game_texts={len(p.game_texts)}, "
              f"chars={len(p.characters)}, rejected={len(p.rejected_archive)})")
        return 0

    # serve
    import uvicorn
    print(f"Narrative Studio API → http://{args.host}:{args.port}  (前端 dev: npm run dev @ :5319)")
    uvicorn.run("omnicompany.packages.narrative_studio.api:app", host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    sys.exit(main())
