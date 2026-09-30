"""对应 Java client/selector/Configurable*Client.java：按 providers 配置选择真实实现或兜底实现。"""

from ..config.settings import ProviderSettings
from ..models.attraction import Attraction
from ..models.attraction_query import AttractionQuery
from ..models.hotel import Hotel
from ..models.hotel_query import HotelQuery
from ..models.provider_metadata import ProviderMetadata
from ..models.route_plan import RoutePlan
from ..models.route_query import RouteQuery
from ..models.weather_info import WeatherInfo
from ..models.weather_query import WeatherQuery
from .base import AttractionClient, HotelClient, RouteClient, WeatherClient


class ConfigurableAttractionClient(AttractionClient):

    def __init__(self, providers: ProviderSettings, mock_client: AttractionClient, amap_client: AttractionClient):
        self._providers = providers
        self._mock_client = mock_client
        self._amap_client = amap_client

    def find_attractions(self, query: AttractionQuery) -> list[Attraction]:
        return self._get_delegate().find_attractions(query)

    def get_metadata(self) -> ProviderMetadata:
        return self._get_delegate().get_metadata()

    def _get_delegate(self) -> AttractionClient:
        if self._providers.attraction.lower() == "amap":
            return self._amap_client
        return self._mock_client


class ConfigurableHotelClient(HotelClient):

    def __init__(self, providers: ProviderSettings, mock_client: HotelClient, amap_client: HotelClient):
        self._providers = providers
        self._mock_client = mock_client
        self._amap_client = amap_client

    def recommend_hotels(self, query: HotelQuery) -> list[Hotel]:
        return self._get_delegate().recommend_hotels(query)

    def get_metadata(self) -> ProviderMetadata:
        return self._get_delegate().get_metadata()

    def _get_delegate(self) -> HotelClient:
        if self._providers.hotel.lower() == "amap":
            return self._amap_client
        return self._mock_client


class ConfigurableWeatherClient(WeatherClient):

    def __init__(self, providers: ProviderSettings, mock_client: WeatherClient, amap_client: WeatherClient):
        self._providers = providers
        self._mock_client = mock_client
        self._amap_client = amap_client

    def get_weather(self, query: WeatherQuery) -> list[WeatherInfo]:
        return self._get_delegate().get_weather(query)

    def get_metadata(self) -> ProviderMetadata:
        return self._get_delegate().get_metadata()

    def _get_delegate(self) -> WeatherClient:
        if self._providers.weather.lower() == "amap":
            return self._amap_client
        return self._mock_client


class ConfigurableRouteClient(RouteClient):

    def __init__(self, providers: ProviderSettings, mock_client: RouteClient, amap_client: RouteClient):
        self._providers = providers
        self._mock_client = mock_client
        self._amap_client = amap_client

    def plan_route(self, query: RouteQuery) -> RoutePlan:
        return self._get_delegate().plan_route(query)

    def get_metadata(self) -> ProviderMetadata:
        return self._get_delegate().get_metadata()

    def _get_delegate(self) -> RouteClient:
        if self._providers.route.lower() == "amap":
            return self._amap_client
        return self._mock_client
