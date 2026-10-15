"""调整请求体：原始规划请求 + 调整话术 + 追问摘出的住宿锚点与必去景点。

`original_request` 是 `TripPlanRequest`，所以它的内层校验（非空、预算下限等）
会跟着一起触发：改方案时整份原请求被带上来，那套约束就一并生效——
原请求里缺了必填项，改方案这一步照样会被拦下。
"""

from typing import Optional

from pydantic import ConfigDict, field_validator

from .base import CamelModel
from .trip_plan_request import TripPlanRequest


class TripAdjustmentRequest(CamelModel):
    # validate_default：字段缺失也要跑一遍校验器，否则缺字段会被当成"没提供、跳过校验"。
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
