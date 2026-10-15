"""应用入口：装配 FastAPI 应用、全局 CORS 与静态前端。

CORS 走全局中间件（allow_origins=["*"]）而不是各个路由分别配，取舍见 docs/DESIGN_NOTES.md。

前端页面在 travel_assistant/static/，做法是在所有 API 路由注册之后再 mount 一个
StaticFiles(html=True)，这样 / 上能出欢迎页。mount 的顺序即匹配顺序，
"/api/" 与 /docs、/openapi.json 都注册在前，因此不会被静态挂载遮住。
"""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .api.ask_controller import router as ask_router
from .api.auth_controller import router as auth_router
from .api.error_handlers import register_exception_handlers
from .api.map_controller import router as map_router
from .api.preference_controller import router as preference_router
from .api.trip_controller import router as trip_router
from .config.runtime_logger import log_travel_runtime
from .config.settings import get_settings

_APP_NAME = "travel-assistant"
_STATIC_DIR = Path(__file__).parent / "static"

# httpx 在 INFO 级会把完整请求 URL 打进日志，而高德 URL 里带 key=<真实密钥>，
# 自己写的日志行已经打码，这一层库日志必须压到 WARNING，否则凭据会落盘。
for _logger_name in ("httpx", "httpcore"):
    logging.getLogger(_logger_name).setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(app: FastAPI):
    log_travel_runtime()
    yield


def create_app() -> FastAPI:
    app = FastAPI(title=_APP_NAME, lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    register_exception_handlers(app)
    app.include_router(auth_router)
    app.include_router(trip_router)
    app.include_router(ask_router)
    app.include_router(map_router)
    app.include_router(preference_router)
    app.mount("/", StaticFiles(directory=_STATIC_DIR, html=True), name="static")
    return app


app = create_app()


def main() -> None:
    uvicorn.run(app, host="0.0.0.0", port=get_settings().server_port)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
