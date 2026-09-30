from typing import Optional

from .base import CamelModel


class WeatherQuery(CamelModel):
    city: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
