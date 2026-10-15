from typing import Optional

from .agent_trace import AgentTrace
from .attraction import Attraction
from .attraction_search_result import AttractionSearchResult
from .base import CamelModel
from .hotel import Hotel
from .hotel_search_result import HotelSearchResult
from .location import Location
from .map_point import MapPoint
from .planning_constraints import PlanningConstraints
from .trip_plan_request import TripPlanRequest
from .weather_query_result import WeatherQueryResult


class PlannerInput(CamelModel):
    request: Optional[TripPlanRequest] = None
    attraction_result: Optional[AttractionSearchResult] = None
    weather_result: Optional[WeatherQueryResult] = None
    hotel_result: Optional[HotelSearchResult] = None
    constraints: Optional[PlanningConstraints] = None
    planning_notes: Optional[list[str]] = None
    planner_query: Optional[str] = None
    agent_traces: Optional[list[AgentTrace]] = None
    # 用户指定住宿位置时的坐标（本项目新增的这两格）：地名字符串在请求上，坐标由
    # service/hotel_anchor_service 解析好交进来，规划器只按坐标筛候选、不再发请求。
    hotel_anchor_location: Optional[Location] = None
    day_hotel_anchor_locations: Optional[dict[int, Location]] = None
    # 某天主张了某个位置时，那一天专用的候选池（全城候选 + 那个位置周边的酒店）。
    # 只装"有锚点的那些天"，其余天仍用 hotel_result 里那份全城候选。
    day_hotel_candidates: Optional[dict[int, list[Hotel]]] = None
    # 某天用户点名必去、且已在候选池里解析出来的景点（本项目新增，见 ATTRACTION_PIN_DESIGN.md）。
    # 规划器把这天其余景点照常选好后再把它们强制塞进去，所以必去景点不会被预算/路线反思裁掉。
    pinned_attractions_by_day: Optional[dict[int, list[Attraction]]] = None
    # 城际往返票价（本项目新增，见 INTERCITY_TRANSPORT_DESIGN.md 4.3）：先从总预算里扣掉，
    # 剩下的"可玩预算"才按天分。0 ＝ 未启用（没填出发城市），逐天与现状一字不变。
    intercity_cost: int = 0
    # 各天路线的终点（第几天→点）：最后一天是返程口岸，其余天不填＝当晚那家酒店。
    day_end_points: Optional[dict[int, MapPoint]] = None
    # 第一天的起点＝到达口岸（广州南／白云机场）。没填出发城市或站名解析不出坐标时为 None，
    # 第一天仍按"第一个景点起步"。
    arrival_point: Optional[MapPoint] = None
    # 选中的城际方式与是否估算，只为把这两个数带进 Budget，不参与任何判定。
    intercity_mode: Optional[str] = None
    intercity_estimated: bool = False

    def pinned_for_day(self, day_index: int) -> list[Attraction]:
        if not self.pinned_attractions_by_day:
            return []
        return self.pinned_attractions_by_day.get(day_index) or []

    def hotel_anchor_for_day(self, day_index: int) -> Optional[Location]:
        if self.day_hotel_anchor_locations:
            location = self.day_hotel_anchor_locations.get(day_index)
            if location is not None:
                return location
        return self.hotel_anchor_location

    def hotel_candidates_for_day(self, day_index: int, all_hotels: list[Hotel]) -> list[Hotel]:
        if self.day_hotel_candidates:
            candidates = self.day_hotel_candidates.get(day_index)
            if candidates:
                return candidates
        return all_hotels
