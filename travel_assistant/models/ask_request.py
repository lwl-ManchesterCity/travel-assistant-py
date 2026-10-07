from typing import Optional

from pydantic import field_validator

from .base import CamelModel
from .trip_plan_request import TripPlanRequest
from .trip_plan import TripPlan


class AskRequest(CamelModel):
    """一次方案追问：问题 + 被追问的那份方案 + 生成它时用的原始请求。

    `trip_plan` 与 `request` 都由前端原样回传（服务端不存方案）。两者都要：
    方案里只有排好的行程，偏好、住宿档位、预算上限这些取数参数只在原始请求里，
    追问涉及外部信息（周边景点、实时天气）时要用它们再去打数据源。
    """

    question: Optional[str] = None
    trip_plan: Optional[TripPlan] = None
    request: Optional[TripPlanRequest] = None
    thread_id: Optional[str] = None

    @field_validator("question")
    @classmethod
    def _question_not_blank(cls, value: Optional[str]) -> Optional[str]:
        if value is None or not value.strip():
            raise ValueError("must not be blank")
        return value

    @field_validator("trip_plan", "request")
    @classmethod
    def _not_null(cls, value):
        if value is None:
            raise ValueError("must not be null")
        return value
