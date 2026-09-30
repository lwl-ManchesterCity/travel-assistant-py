from typing import Optional

from .base import CamelModel


class WeatherInfo(CamelModel):
    date: Optional[str] = None
    day_weather: Optional[str] = None
    night_weather: Optional[str] = None
    day_temp: int = 0
    night_temp: int = 0
    wind_direction: Optional[str] = None
    wind_power: Optional[str] = None
    source: Optional[str] = None
    source_note: Optional[str] = None
