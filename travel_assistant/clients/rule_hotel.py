"""对应 Java client/impl/RuleBasedHotelClient.java（mock 酒店源）。"""

from ..models.hotel import Hotel
from ..models.hotel_query import HotelQuery
from ..models.location import Location
from ..models.provider_metadata import ProviderMetadata
from .base import HotelClient

_PROVIDER = ProviderMetadata(
    provider_code="rule-hotel-engine",
    provider_name="规则酒店推荐引擎",
    mock=True,
)


class RuleBasedHotelClient(HotelClient):
    def recommend_hotels(self, query: HotelQuery) -> list[Hotel]:
        city = query.city
        budget_per_day = query.budget // query.days
        accommodation = query.accommodation
        if budget_per_day < 500:
            return [
                Hotel(
                    name="高性价比便捷酒店",
                    address=f"{city}地铁沿线",
                    location=Location(longitude=116.3970, latitude=39.9180),
                    price_range="300-500元",
                    rating="4.2",
                    distance="距离景点 2 公里",
                    type=accommodation,
                    estimated_cost=320,
                )
            ]

        if budget_per_day < 1000:
            return [
                Hotel(
                    name="舒适型精选酒店",
                    address=f"{city}中心城区",
                    location=Location(longitude=116.4030, latitude=39.9200),
                    price_range="500-800元",
                    rating="4.5",
                    distance="距离景点 1.5 公里",
                    type=accommodation,
                    estimated_cost=560,
                ),
                Hotel(
                    name="城市轻奢酒店",
                    address=f"{city}商圈附近",
                    location=Location(longitude=116.4100, latitude=39.9250),
                    price_range="700-900元",
                    rating="4.6",
                    distance="距离景点 2.2 公里",
                    type=accommodation,
                    estimated_cost=760,
                ),
            ]

        return [
            Hotel(
                name="品质度假酒店",
                address=f"{city}核心景区附近",
                location=Location(longitude=116.4200, latitude=39.9300),
                price_range="900-1400元",
                rating="4.8",
                distance="距离景点 1 公里",
                type=accommodation,
                estimated_cost=980,
            )
        ]

    def get_metadata(self) -> ProviderMetadata:
        return _PROVIDER
