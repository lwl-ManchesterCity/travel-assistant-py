from typing import Optional

from .base import CamelModel
from .location import Location


class Attraction(CamelModel):
    name: Optional[str] = None
    address: Optional[str] = None
    location: Optional[Location] = None
    visit_duration: int = 0
    description: Optional[str] = None
    category: Optional[str] = None
    rating: Optional[float] = None
    image_url: Optional[str] = None
    ticket_price: int = 0
