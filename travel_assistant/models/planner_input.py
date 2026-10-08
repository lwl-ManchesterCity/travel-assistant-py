from typing import Optional

from .agent_trace import AgentTrace
from .attraction_search_result import AttractionSearchResult
from .base import CamelModel
from .hotel import Hotel
from .hotel_search_result import HotelSearchResult
from .location import Location
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
    # 用户指定住宿位置时的坐标（Java 没有这两格）：地名字符串在请求上，坐标由
    # service/hotel_anchor_service 解析好交进来，规划器只按坐标筛候选、不再发请求。
    hotel_anchor_location: Optional[Location] = None
    day_hotel_anchor_locations: Optional[dict[int, Location]] = None
    # 某天主张了某个位置时，那一天专用的候选池（全城候选 + 那个位置周边的酒店）。
    # 只装"有锚点的那些天"，其余天仍用 hotel_result 里那份全城候选。
    day_hotel_candidates: Optional[dict[int, list[Hotel]]] = None

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
