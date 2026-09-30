from typing import Optional

from .base import CamelModel
from .location import Location


class MapPoint(CamelModel):
    name: Optional[str] = None
    address: Optional[str] = None
    location: Optional[Location] = None
    point_type: Optional[str] = None
