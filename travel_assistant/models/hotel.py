from typing import Optional

from .base import CamelModel
from .location import Location


class Hotel(CamelModel):
    name: Optional[str] = None
    address: Optional[str] = None
    location: Optional[Location] = None
    price_range: Optional[str] = None
    rating: Optional[str] = None
    distance: Optional[str] = None
    type: Optional[str] = None
    estimated_cost: int = 0
