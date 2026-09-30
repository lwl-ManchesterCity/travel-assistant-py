from typing import Optional

from .agent_trace import AgentTrace
from .attraction_search_result import AttractionSearchResult
from .base import CamelModel
from .hotel_search_result import HotelSearchResult
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
