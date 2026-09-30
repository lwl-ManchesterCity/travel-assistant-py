from typing import Optional

from .base import CamelModel
from .map_point import MapPoint
from .provider_metadata import ProviderMetadata
from .route_step import RouteStep


class RoutePlan(CamelModel):
    city: Optional[str] = None
    day_index: int = 0
    start_point: Optional[MapPoint] = None
    end_point: Optional[MapPoint] = None
    steps: Optional[list[RouteStep]] = None
    total_distance_km: float = 0.0
    total_duration_minutes: int = 0
    transportation_mode: Optional[str] = None
    summary: Optional[str] = None
    provider: Optional[ProviderMetadata] = None
