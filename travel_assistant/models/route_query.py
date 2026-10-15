from typing import Optional

from .attraction import Attraction
from .base import CamelModel
from .hotel import Hotel
from .map_point import MapPoint


class RouteQuery(CamelModel):
    city: Optional[str] = None
    day_index: int = 0
    transportation: Optional[str] = None
    hotel: Optional[Hotel] = None
    attractions: Optional[list[Attraction]] = None
    # 当天的真实起终点（本项目新增的这两格，见 docs/INTERCITY_TRANSPORT_DESIGN.md 4.4）：
    # 起点＝前一晚住的酒店（第一天是到达口岸），终点＝当晚酒店（最后一天是返程口岸）。
    # 都不填时保持原行为：首个景点起步、末站回当晚酒店。
    start_point: Optional[MapPoint] = None
    end_point: Optional[MapPoint] = None
