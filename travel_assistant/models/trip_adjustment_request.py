"""对应 Java model/TripAdjustmentRequest.java。

Java 侧只有 @NotNull + @NotBlank 两个约束，且 originalRequest 上没有 @Valid，
所以 Spring 不会级联校验内层 TripPlanRequest 的 @NotBlank/@Min；Python 侧字段类型是
TripPlanRequest，内层约束会一并触发，差异记在 MIGRATION_NOTES.md。
"""

from typing import Optional

from pydantic import ConfigDict, field_validator

from .base import CamelModel
from .trip_plan_request import TripPlanRequest


class TripAdjustmentRequest(CamelModel):
    # validate_default：JSON 里缺字段等价于 Jackson 留出 null，Java 的 @NotNull/@NotBlank 同样会触发。
    model_config = ConfigDict(validate_default=True)

    original_request: Optional[TripPlanRequest] = None
    adjustment_prompt: Optional[str] = None
    # 追问 agent 从用户原话里摘出的住宿地名与天数（天数从 0 起，null=整趟）。
    # 由后端写回请求的锚点格子，前端只转达、不再自己判断"整趟 vs 按天"。
    anchor_hint: Optional[str] = None
    anchor_day: Optional[int] = None
    # 追问 agent 摘出的"某天必去景点名（可多个）"与天数（从 0 起）。
    # pin_move_hotel：那张"要不要把酒店也挪过去"确认卡的选择——真=复用住宿锚点把酒店挪到景点旁。
    pin_hints: Optional[list[str]] = None
    pin_day: Optional[int] = None
    pin_move_hotel: bool = False

    @field_validator("original_request")
    @classmethod
    def _not_null(cls, value: Optional[TripPlanRequest]) -> Optional[TripPlanRequest]:
        if value is None:
            raise ValueError("must not be null")
        return value

    @field_validator("adjustment_prompt")
    @classmethod
    def _not_blank(cls, value: Optional[str]) -> Optional[str]:
        if value is None or not value.strip():
            raise ValueError("must not be blank")
        return value
