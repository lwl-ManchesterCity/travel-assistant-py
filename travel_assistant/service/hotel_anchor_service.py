"""把请求里的住宿地名字符串换成坐标，并补一趟周边酒店检索（docs/ADJUST_AGENT_DESIGN.md 9.2）。

放在这里而不是规划器里，是因为这两步都会发请求、都会失败；而"坐标 → 筛掉圈外酒店"是纯计算。
切成两格之后，规划器只吃坐标与候选清单，回落与留痕都在这格处理干净。

第二趟周边检索是实测逼出来的：默认的酒店检索按关键词相关性只给 10 家，实测杭州"舒适型酒店"
最近的离西湖 6.1 公里，在这种池子里画圈必然圈空。

按天锚点优先于全程锚点，跟文档 9.3 的三档回落一致；两个都没填时一次请求都不发。
"""

from dataclasses import dataclass, field
from typing import Optional

from ..clients.amap_hotel import AmapHotelClient
from ..clients.amap_place import resolve_anchor_location
from ..config.settings import AmapSettings, AnchorSettings
from ..models.hotel import Hotel
from ..models.hotel_query import HotelQuery
from ..models.location import Location
from ..models.trip_plan_request import TripPlanRequest


@dataclass
class HotelAnchors:
    whole_trip: Optional[Location] = None
    by_day: dict[int, Location] = field(default_factory=dict)
    unresolved: list[str] = field(default_factory=list)
    searched: int = 0
    found_nearby: int = 0

    def is_empty(self) -> bool:
        return self.whole_trip is None and not self.by_day

    def every_location(self) -> list[Location]:
        locations = [location for location in [self.whole_trip, *self.by_day.values()] if location is not None]
        unique: list[Location] = []
        for location in locations:
            if not any(existing.longitude == location.longitude and existing.latitude == location.latitude
                       for existing in unique):
                unique.append(location)
        return unique


class HotelAnchorService:

    def __init__(self, amap: AmapSettings, hotel_client: AmapHotelClient, anchor_settings: AnchorSettings):
        self._amap = amap
        self._hotel_client = hotel_client
        self._anchor_settings = anchor_settings

    def resolve(self, request: TripPlanRequest) -> HotelAnchors:
        anchors = HotelAnchors()
        # 同一个地名在整趟里只搜一次，按天重复写"西湖"不会多花配额。
        resolved: dict[str, Optional[Location]] = {}

        for keyword in request.distinct_hotel_anchors():
            if keyword not in resolved:
                resolved[keyword] = resolve_anchor_location(self._amap, request.city, keyword)
                if resolved[keyword] is None:
                    anchors.unresolved.append(keyword)

        if request.hotel_anchor:
            anchors.whole_trip = resolved.get(request.hotel_anchor)
        for day, keyword in (request.day_hotel_anchors or {}).items():
            location = resolved.get(keyword)
            if location is not None:
                anchors.by_day[int(day)] = location
        return anchors

    def search_nearby(self, request: TripPlanRequest, anchors: HotelAnchors) -> list[Hotel]:
        """按每个锚点补一趟周边酒店检索，返回去重后的候选清单（可能为空）。"""
        if anchors.is_empty():
            return []
        query = HotelQuery(
            city=request.city,
            budget=request.budget,
            days=request.calculate_days(),
            accommodation=request.accommodation,
        )
        merged: list[Hotel] = []
        for location in anchors.every_location():
            anchors.searched += 1
            for hotel in self._hotel_client.search_near_location(
                    query, location, self._anchor_settings.radius_km):
                if not any(existing.name == hotel.name for existing in merged):
                    merged.append(hotel)
        anchors.found_nearby = len(merged)
        return merged
