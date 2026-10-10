"""对应 Java client 层的四个接口：AttractionClient / HotelClient / WeatherClient / RouteClient。"""

from abc import ABC, abstractmethod

from ..models.attraction import Attraction
from ..models.attraction_query import AttractionQuery
from ..models.hotel import Hotel
from ..models.hotel_query import HotelQuery
from ..models.intercity_plan import InterCityOption
from ..models.location import Location
from ..models.provider_metadata import ProviderMetadata
from ..models.route_plan import RoutePlan
from ..models.route_query import RouteQuery
from ..models.weather_info import WeatherInfo
from ..models.weather_query import WeatherQuery


class AttractionClient(ABC):
    @abstractmethod
    def find_attractions(self, query: AttractionQuery) -> list[Attraction]:
        ...

    @abstractmethod
    def get_metadata(self) -> ProviderMetadata:
        ...

    def find_attraction_by_name(self, city: str, name: str) -> "Attraction | None":
        """按名字精确查单个景点（可选能力，供"某天必去某景点"补搜用；默认不支持返回 None）。"""
        return None


class HotelClient(ABC):
    @abstractmethod
    def recommend_hotels(self, query: HotelQuery) -> list[Hotel]:
        ...

    @abstractmethod
    def get_metadata(self) -> ProviderMetadata:
        ...


class WeatherClient(ABC):
    @abstractmethod
    def get_weather(self, query: WeatherQuery) -> list[WeatherInfo]:
        ...

    @abstractmethod
    def get_metadata(self) -> ProviderMetadata:
        ...


class RouteClient(ABC):
    @abstractmethod
    def plan_route(self, query: RouteQuery) -> RoutePlan:
        ...

    def plan_intercity(self,
                       origin_city: str,
                       origin: Location,
                       destination_city: str,
                       destination: Location,
                       straight_km: float) -> list[InterCityOption]:
        """跨城方案（票价＋到达站）。默认一条都不给＝这个 provider 查不到大交通。

        不做成 @abstractmethod：Java 侧没有这一层，本地/规则那几个兜底实现没必要都被迫写一遍，
        由调用方（IntercityService）在拿不到方案时按直线距离估。
        """
        return []

    @abstractmethod
    def get_metadata(self) -> ProviderMetadata:
        ...
