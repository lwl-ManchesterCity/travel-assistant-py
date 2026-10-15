"""异常 → HTTP 响应的统一出口。

所有错误都走同一个 ApiErrorResponse 信封（code / message / details / timestamp），
前端只写一套解析。校验失败的 details 用 `字段: 人话` 的格式。
"""

import logging
from datetime import datetime

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic.alias_generators import to_camel

from ..exceptions import (
    TripPlanningException,
    UnauthorizedException,
    UsernameTakenException,
    is_quota_exhausted,
)
from ..models.api_error_response import ApiErrorResponse

log = logging.getLogger(__name__)

_VALUE_ERROR_PREFIX = "Value error, "
# 原来这里对所有 TripPlanningException 一律回「请调整输入条件后重试」，但高德限流/配额类失败
# 与用户的输入毫无关系，这句话会把人往错的方向带（撞 10021 时用户的第一反应是"我是不是没填全"）。
_PLANNING_ERROR_DETAIL = "请调整输入条件后重试"
_QUOTA_ERROR_DETAIL = "地图数据接口调用过于频繁，请稍后重试"


def _field_name(loc: tuple) -> str:
    # 信封里的字段名一律是驼峰：FastAPI 的 loc 多了 body 前缀，字段缺失时给的又是蛇形名，
    # 所以统一转成驼峰再拼出去。
    parts = [to_camel(str(item)) for item in loc if item != "body"]
    return ".".join(parts) if parts else "body"


def _error_message(error: dict) -> str:
    message = str(error.get("msg", ""))
    if message.startswith(_VALUE_ERROR_PREFIX):
        # 自定义校验器抛出的就是给人看的那句话，去掉 pydantic 自己加上的 `Value error, ` 前缀
        return message[len(_VALUE_ERROR_PREFIX):]
    return message


def _json_response(status_code: int, error_response: ApiErrorResponse) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=error_response.model_dump(mode="json", by_alias=True),
    )


def register_exception_handlers(app: FastAPI) -> None:
    """把全部异常处理挂到 app 上：校验失败、未登录、用户名重名、规划失败，以及兜底 500。"""

    @app.exception_handler(RequestValidationError)
    async def handle_validation_exception(request: Request, exc: RequestValidationError) -> JSONResponse:
        details = [f"{_field_name(error['loc'])}: {_error_message(error)}" for error in exc.errors()]
        log.warning("请求参数校验失败: %s", details)
        return _json_response(
            400,
            ApiErrorResponse(
                code="VALIDATION_ERROR",
                message="请求参数校验失败",
                details=details,
                timestamp=datetime.now(),
            ),
        )

    @app.exception_handler(UnauthorizedException)
    async def handle_unauthorized(request: Request, exc: UnauthorizedException) -> JSONResponse:
        # 故意不打 warning：没登录是最常见的正常情况（页面刚打开时探一下 /me 就是 401），
        # 每次都记一条会把真该看的日志淹掉。
        return _json_response(
            401,
            ApiErrorResponse(
                code="UNAUTHORIZED",
                message=str(exc),
                details=[],
                timestamp=datetime.now(),
            ),
        )

    @app.exception_handler(UsernameTakenException)
    async def handle_username_taken(request: Request, exc: UsernameTakenException) -> JSONResponse:
        # details 的格式跟校验错误那条保持一致（`字段: 人话`），因为前端的错误提示优先读 details。
        # 这里一度写的是 `f"username: {exc.username}"`——那等于把机器字段当人话显示，
        # 用户会看到「username: amy」而不是「这个用户名已经被用了」。
        taken = "这个用户名已经被用了"
        return _json_response(
            409,
            ApiErrorResponse(
                code="USERNAME_TAKEN",
                message=taken,
                details=[f"username: {taken}"],
                timestamp=datetime.now(),
            ),
        )

    @app.exception_handler(TripPlanningException)
    async def handle_trip_planning_exception(request: Request, exc: TripPlanningException) -> JSONResponse:
        log.warning("旅行规划异常: %s", exc, exc_info=True)
        detail = _QUOTA_ERROR_DETAIL if is_quota_exhausted(exc) else _PLANNING_ERROR_DETAIL
        return _json_response(
            400,
            ApiErrorResponse(
                code="TRIP_PLANNING_ERROR",
                message=str(exc),
                details=[detail],
                timestamp=datetime.now(),
            ),
        )

    @app.exception_handler(Exception)
    async def handle_generic_exception(request: Request, exc: Exception) -> JSONResponse:
        log.error("服务内部异常", exc_info=True)
        return _json_response(
            500,
            ApiErrorResponse(
                code="INTERNAL_ERROR",
                message="服务内部出现异常",
                details=[type(exc).__name__],
                timestamp=datetime.now(),
            ),
        )
