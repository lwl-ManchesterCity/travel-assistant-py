from typing import Optional

from .base import CamelModel
from .provider_metadata import ProviderMetadata
from .weather_info import WeatherInfo


class WeatherQueryResult(CamelModel):
    city: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    weather_info: Optional[list[WeatherInfo]] = None
    summary: Optional[str] = None
    provider: Optional[ProviderMetadata] = None
