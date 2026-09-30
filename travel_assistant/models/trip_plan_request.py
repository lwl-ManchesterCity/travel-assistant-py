"""对应 Java model/TripPlanRequest.java，含 Jakarta Validation 的等价校验。"""

import re
from datetime import date
from typing import Optional

from pydantic import ConfigDict, field_validator

from .base import CamelModel

_DATE_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}")


class TripPlanRequest(CamelModel):
    # validate_default：JSON 里缺字段等价于 Jackson 留出 null，Java 的 @NotBlank/@Min 同样会触发。
    model_config = ConfigDict(validate_default=True)

    city: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    budget: int = 0
    preferences: Optional[list[str]] = None
    transportation: Optional[str] = None
    accommodation: Optional[str] = None
    extra_requirements: Optional[str] = None

    @field_validator("city", "accommodation")
    @classmethod
    def _not_blank(cls, value: Optional[str]) -> Optional[str]:
        if value is None or not value.strip():
            raise ValueError("must not be blank")
        return value

    @field_validator("start_date")
    @classmethod
    def _start_date_format(cls, value: Optional[str]) -> Optional[str]:
        if value is None or not value.strip():
            raise ValueError("must not be blank")
        if not _DATE_PATTERN.fullmatch(value):
            raise ValueError("开始日期必须是 yyyy-MM-dd 格式")
        return value

    @field_validator("end_date")
    @classmethod
    def _end_date_format(cls, value: Optional[str]) -> Optional[str]:
        if value is None or not value.strip():
            raise ValueError("must not be blank")
        if not _DATE_PATTERN.fullmatch(value):
            raise ValueError("结束日期必须是 yyyy-MM-dd 格式")
        return value

    @field_validator("budget")
    @classmethod
    def _min_budget(cls, value: int) -> int:
        if value < 1:
            raise ValueError("must be greater than or equal to 1")
        return value

    @field_validator("preferences")
    @classmethod
    def _not_empty(cls, value: Optional[list[str]]) -> Optional[list[str]]:
        if value is None:
            raise ValueError("must not be null")
        if not value:
            raise ValueError("must not be empty")
        return value

    def calculate_days(self) -> int:
        start = date.fromisoformat(self.start_date)
        end = date.fromisoformat(self.end_date)
        return (end - start).days + 1
