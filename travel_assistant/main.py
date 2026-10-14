"""对应 Java TravelAssistantApplication.java + application.yml 的 server.port。

Spring Boot 的 @CrossOrigin(origins = "*") 加在 TripPlanController 与 MapConfigController 上，
FastAPI 没有控制器级 CORS 注解，等价做法是注册全局 CORSMiddleware（allow_origins=["*"]），
差异记在 MIGRATION_NOTES.md。

Spring Boot 默认把 src/main/resources/static/ 挂在 classpath 根路径（含 / 的欢迎页），
FastAPI 的等价做法是在所有 API 路由注册之后再 mount 一个 StaticFiles(html=True)。
mount 的顺序即匹配顺序，"/api/" 与 /docs、/openapi.json 都注册在前，因此不会被静态挂载遮住。
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
from .api.trip_controller import router as trip_router
from .config.runtime_logger import log_travel_runtime
from .config.settings import get_settings

_SPRING_APPLICATION_NAME = "travel-assistant"
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
    app = FastAPI(title=_SPRING_APPLICATION_NAME, lifespan=lifespan)
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
    app.mount("/", StaticFiles(directory=_STATIC_DIR, html=True), name="static")
    return app


app = create_app()


def main() -> None:
    uvicorn.run(app, host="0.0.0.0", port=get_settings().server_port)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
