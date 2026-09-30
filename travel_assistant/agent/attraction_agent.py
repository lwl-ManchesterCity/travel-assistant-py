"""对应 Java agent/AttractionAgent.java：按偏好排序候选景点，数据本身来自 client 层。"""

from ..models.attraction import Attraction
from ..models.attraction_query import AttractionQuery
from ..models.attraction_search_result import AttractionSearchResult
from ..models.trip_plan_request import TripPlanRequest


class AttractionAgent:

    def __init__(self, attraction_client):
        self._attraction_client = attraction_client

    def search(self, request: TripPlanRequest) -> AttractionSearchResult:
        matched_attractions = self._search_attractions(request)
        return AttractionSearchResult(
            city=request.city,
            preferences=request.preferences,
            attractions=matched_attractions,
            summary=self._build_summary(request, matched_attractions),
            provider=self._attraction_client.get_metadata(),
        )

    def _search_attractions(self, request: TripPlanRequest) -> list[Attraction]:
        query = AttractionQuery(city=request.city, preferences=request.preferences)
        candidate_attractions = self._attraction_client.find_attractions(query)
        matched_attractions: list[Attraction] = []

        for preference in request.preferences or []:
            normalized_preference = preference.lower()
            for attraction in candidate_attractions:
                if self._matches_preference(attraction, normalized_preference) \
                        and not self._contains_by_name(matched_attractions, attraction.name):
                    matched_attractions.append(attraction)

        if not matched_attractions:
            return candidate_attractions

        for attraction in candidate_attractions:
            if not self._contains_by_name(matched_attractions, attraction.name):
                matched_attractions.append(attraction)

        return matched_attractions

    def _build_summary(self, request: TripPlanRequest, attractions: list[Attraction]) -> str:
        return (
            f"已为 {request.city} 匹配到 {len(attractions)} 个候选景点，"
            f"优先偏好为：{'、'.join(request.preferences or [])}"
        )

    def _matches_preference(self, attraction: Attraction, preference: str) -> bool:
        category = (attraction.category or "").lower()
        description = (attraction.description or "").lower()
        return preference in category or preference in description

    def _contains_by_name(self, attractions: list[Attraction], attraction_name: str) -> bool:
        return any(attraction.name == attraction_name for attraction in attractions)
