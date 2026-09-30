from typing import Optional

from .base import CamelModel


class RouteStep(CamelModel):
    order_index: int = 0
    from_name: Optional[str] = None
    to_name: Optional[str] = None
    transportation_mode: Optional[str] = None
    distance_km: float = 0.0
    duration_minutes: int = 0
    instruction: Optional[str] = None
