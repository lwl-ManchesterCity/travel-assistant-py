"""酒店推荐：把请求折成 HotelQuery 交给 client，并附一句候选摘要。"""

from ..models.hotel import Hotel
from ..models.hotel_query import HotelQuery
from ..models.hotel_search_result import HotelSearchResult
from ..models.trip_plan_request import TripPlanRequest


class HotelAgent:

    def __init__(self, hotel_client):
        self._hotel_client = hotel_client

    def recommend(self, request: TripPlanRequest) -> HotelSearchResult:
        hotel_query = HotelQuery(
            city=request.city,
            budget=request.budget,
            days=request.calculate_days(),
            accommodation=request.accommodation,
        )
        hotels = self._hotel_client.recommend_hotels(hotel_query)
        return HotelSearchResult(
            city=request.city,
            accommodation=request.accommodation,
            hotels=hotels,
            summary=self._build_summary(request, hotels),
            provider=self._hotel_client.get_metadata(),
        )

    def _build_summary(self, request: TripPlanRequest, hotels: list[Hotel]) -> str:
        return f"已为 {request.city} 推荐 {len(hotels)} 家{request.accommodation}，预算参考 {request.budget} 元"
