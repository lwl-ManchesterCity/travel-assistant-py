"""对应 Java exception/GlobalExceptionHandler.java（@RestControllerAdvice）。"""

import logging
from datetime import datetime

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic.alias_generators import to_camel

from ..exceptions import TripPlanningException
from ..models.api_error_response import ApiErrorResponse

log = logging.getLogger(__name__)

_VALUE_ERROR_PREFIX = "Value error, "


def _field_name(loc: tuple) -> str:
    # Spring 的 FieldError.getField() 只有驼峰属性名：FastAPI 的 loc 多了 body 前缀，
    # 且字段缺失时给的是 Python 蛇形名，所以统一转成 Java 侧的驼峰名。
    parts = [to_camel(str(item)) for item in loc if item != "body"]
    return ".".join(parts) if parts else "body"


def _error_message(error: dict) -> str:
    message = str(error.get("msg", ""))
    if message.startswith(_VALUE_ERROR_PREFIX):
        # 自定义校验器里抛出的文案就是 Java 的 message，去掉 pydantic 自己加的前缀
        return message[len(_VALUE_ERROR_PREFIX):]
    return message


def _json_response(status_code: int, error_response: ApiErrorResponse) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=error_response.model_dump(mode="json", by_alias=True),
    )


def register_exception_handlers(app: FastAPI) -> None:
    """注册三类异常处理，与 Java 的三个 @ExceptionHandler 一一对应。"""

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

    @app.exception_handler(TripPlanningException)
    async def handle_trip_planning_exception(request: Request, exc: TripPlanningException) -> JSONResponse:
        log.warning("旅行规划异常: %s", exc, exc_info=True)
        return _json_response(
            400,
            ApiErrorResponse(
                code="TRIP_PLANNING_ERROR",
                message=str(exc),
                details=["请调整输入条件后重试"],
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
