from __future__ import annotations

import argparse
import os

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse

from .api import game_observatory_router


def create_public_app() -> FastAPI:
    app = FastAPI(
        title="Game Observatory Public API",
        version="0.2",
        docs_url="/game-observatory/api-docs",
        redoc_url=None,
        openapi_url="/api/game-observatory/openapi.json",
    )
    app.include_router(game_observatory_router)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault(
            "Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()"
        )
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
            "script-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'self'; "
            "base-uri 'self'; form-action 'self'",
        )
        return response

    @app.get("/", include_in_schema=False)
    def root_redirect() -> RedirectResponse:
        return RedirectResponse("/game-observatory/", status_code=307)

    return app


app = create_public_app()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="game-observatory-public")
    parser.add_argument("--host", default=os.environ.get("OMNI_GAME_OBSERVATORY_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("OMNI_GAME_OBSERVATORY_PORT", "8222")))
    args = parser.parse_args(argv)
    import uvicorn

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
