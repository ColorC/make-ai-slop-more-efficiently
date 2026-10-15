# [OMNI] origin=ai-ide ts=2026-08-18 type=infra
"""game_observatory 独立服务壳（DOMAIN-AUTONOMY-P1 A2）。

域自治：171k 行的 game_observatory 域从 8210 背板剥离为独立进程，
由 dashboard 生命周期托管（controlplane/game_observatory_service.py）。

铁律（用户裁定 2026-08-18）：对外出站只有 8210 一口——本服务只绑 127.0.0.1
内部口（默认 8213，登记于 config/backplane/domains.yaml），仅供 dashboard
反代（controlplane/game_observatory_proxy.py）访问，不对外暴露。

路由 100% 复用 api.py 的 game_observatory_router（142 条，含 /robots.txt），
行为保持；本文件只是 uvicorn 小壳，不做任何业务。
"""
from __future__ import annotations

import argparse


def build_app():
    from fastapi import FastAPI

    from omnicompany.packages.domains.game_observatory.api import game_observatory_router

    app = FastAPI(title="game-observatory", docs_url=None, redoc_url=None)
    app.include_router(game_observatory_router)
    return app


def main() -> None:
    import uvicorn

    parser = argparse.ArgumentParser(description="game_observatory 域服务（内部口，仅供 8210 反代）")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8213)
    args = parser.parse_args()
    uvicorn.run(build_app(), host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
