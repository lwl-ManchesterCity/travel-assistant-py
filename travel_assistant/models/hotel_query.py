from typing import Optional

from .base import CamelModel


class HotelQuery(CamelModel):
    city: Optional[str] = None
    budget: int = 0
    days: int = 0
    accommodation: Optional[str] = None
