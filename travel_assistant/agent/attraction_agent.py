"""景点检索：把命中偏好的候选排到前面，数据本身来自 client 层。"""

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

    def find_by_name(self, city: str, name: str) -> Attraction | None:
        """按名字查**单个**景点，查不到返回 None。

        走 client 的 `find_attraction_by_name`（`clients/base.py` 里的可选能力，
        基类默认返回 None，所以本地/规则那几个兜底 provider 不会因为这条路报错）。
        收在 agent 上而不是让工具直接拿 client：与其余工具一致，都是「工具 → agent → client」。
        """
        return self._attraction_client.find_attraction_by_name(city, name)

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
