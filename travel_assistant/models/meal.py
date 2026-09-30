from typing import Optional

from .base import CamelModel
from .location import Location


class Meal(CamelModel):
    type: Optional[str] = None
    name: Optional[str] = None
    address: Optional[str] = None
    location: Optional[Location] = None
    description: Optional[str] = None
    estimated_cost: int = 0
