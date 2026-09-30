from typing import Optional

from .attraction import Attraction
from .base import CamelModel
from .hotel import Hotel


class RouteQuery(CamelModel):
    city: Optional[str] = None
    day_index: int = 0
    transportation: Optional[str] = None
    hotel: Optional[Hotel] = None
    attractions: Optional[list[Attraction]] = None
