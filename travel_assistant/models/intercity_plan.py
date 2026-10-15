"""城际大交通的结果模型（本项目新增的一层，见 docs/INTERCITY_TRANSPORT_DESIGN.md）。

只装"这一天之前和之后那段路"的信息：花多少钱、从哪个站落地、这价格是查来的还是估来的。
耗时字段留着只做留档——本版按"早上出发、中午前后到、回程当天完成"的统一假设走，
不拿真实小时数去排每天能玩几个景点（那会变成排班，牵动整个日结构）。
"""

from typing import Optional

from .base import CamelModel
from .location import Location
from .map_point import MapPoint

MODE_HIGH_SPEED_RAIL = "高铁"
MODE_ORDINARY_RAIL = "普速火车"
MODE_FLIGHT = "飞机"


class InterCityOption(CamelModel):
    mode: str
    round_trip_cost: int
    one_way_minutes: int = 0
    # 乘客真正下车的那一站（不是车次名里的终到站），以及它自带的坐标
    arrival_station: Optional[str] = None
    arrival_location: Optional[Location] = None
    estimated: bool = False


class InterCityPlan(CamelModel):
    # 选中的那档；None ＝ 没填出发城市或两城太近，整套大交通不启用
    option: Optional[InterCityOption] = None
    # 第一天从哪个站开始走、最后一天在哪个站收工
    arrival_point: Optional[MapPoint] = None
    departure_point: Optional[MapPoint] = None
    notes: list[str] = []

    @property
    def cost(self) -> int:
        return 0 if self.option is None else self.option.round_trip_cost
