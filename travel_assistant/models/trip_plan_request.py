"""对应 Java model/TripPlanRequest.java，含 Jakarta Validation 的等价校验。

`hotel_anchor` / `day_hotel_anchors` / `hotel_continuity` 三个字段是 Java 源项目没有的扩展
（见 MIGRATION_NOTES.md「主动偏离源项目」一节）：用户想指定住宿位置或要求整趟住同一家时，
这句话在现有链路里没有任何格子承接，只能落进 `extra_requirements` 的自由文本而没人读。
三个都可空，不填时行为与 Java 完全一致。
"""

import re
from datetime import date
from typing import Optional

from pydantic import ConfigDict, field_validator

from .base import CamelModel

_DATE_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}")
_CONTINUITY_VALUES = ("strict", "optimal")


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
    hotel_anchor: Optional[str] = None
    day_hotel_anchors: Optional[dict[int, str]] = None
    hotel_continuity: Optional[str] = None
    # 某天必去的景点名（Java 没有这格）：{第几天(0起) → 景点名列表}，一天可多个。
    # 与住宿锚点同类——是"这句话没有格子承接"补出来的输入格子，见 ATTRACTION_PIN_DESIGN.md。
    day_attraction_pins: Optional[dict[int, list[str]]] = None
    # 出发城市（Java 没有这格）：前端那个"出发城市"输入框以前只做预览展示、不进请求体。
    # 填了才会算城际往返票价、才会把第一天的起点换成到达口岸；空＝整套大交通逻辑不启用。
    origin_city: Optional[str] = None

    @field_validator("origin_city")
    @classmethod
    def _clean_origin(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        text = value.strip()
        return text or None

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

    @field_validator("hotel_anchor")
    @classmethod
    def _blank_anchor_is_unset(cls, value: Optional[str]) -> Optional[str]:
        return value.strip() if value is not None and value.strip() else None

    @field_validator("day_hotel_anchors")
    @classmethod
    def _blank_day_anchors_are_unset(cls, value: Optional[dict[int, str]]) -> Optional[dict[int, str]]:
        if not value:
            return None
        cleaned = {int(day): name.strip()
                   for day, name in value.items() if name is not None and name.strip()}
        return cleaned or None

    @field_validator("hotel_continuity")
    @classmethod
    def _known_continuity(cls, value: Optional[str]) -> Optional[str]:
        if value is None or not value.strip():
            return None
        normalized = value.strip().lower()
        if normalized not in _CONTINUITY_VALUES:
            raise ValueError("must be one of: strict, optimal")
        return normalized

    @field_validator("day_attraction_pins")
    @classmethod
    def _clean_day_pins(cls, value: Optional[dict[int, list[str]]]) -> Optional[dict[int, list[str]]]:
        if not value:
            return None
        cleaned: dict[int, list[str]] = {}
        for day, names in value.items():
            kept = [name.strip() for name in (names or []) if name is not None and name.strip()]
            if kept:
                cleaned[int(day)] = kept
        return cleaned or None

    def is_strict_hotel_continuity(self) -> bool:
        return self.hotel_continuity == "strict"

    def hotel_anchor_label_for_day(self, day_index: int) -> str | None:
        """某天用户指定的住宿地名：当天指定的优先，否则用整趟的，都没有就是 None。"""
        if self.day_hotel_anchors and self.day_hotel_anchors.get(day_index):
            return self.day_hotel_anchors[day_index]
        return self.hotel_anchor

    def distinct_hotel_anchors(self) -> list[str]:
        anchors: list[str] = []
        for name in [self.hotel_anchor, *(self.day_hotel_anchors or {}).values()]:
            if name and name not in anchors:
                anchors.append(name)
        return anchors

    def pinned_attractions_for_day(self, day_index: int) -> list[str]:
        """某天用户点名必去的景点名，没有就空列表。"""
        if not self.day_attraction_pins:
            return []
        return self.day_attraction_pins.get(day_index) or []

    def distinct_pinned_attractions(self) -> list[str]:
        names: list[str] = []
        for day_names in (self.day_attraction_pins or {}).values():
            for name in day_names:
                if name and name not in names:
                    names.append(name)
        return names

    def calculate_days(self) -> int:
        start = date.fromisoformat(self.start_date)
        end = date.fromisoformat(self.end_date)
        return (end - start).days + 1
