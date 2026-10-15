"""城际大交通：算出"从哪座城来、花多少钱、在哪个站落地"（docs/INTERCITY_TRANSPORT_DESIGN.md）。

三条口径要记住：

- **只按价格选档**。时间这版不按方式区分（统一假设早上出发、中午前后到、回程当天完成），
  所以三档在计算里只剩价格一个维度，谁过闸门且最便宜就选谁。
- **价格分两种来源**：跨城接口给的（含两端地铁，是参考口径）和直线距离估的，后者一律标 `estimated`。
- **拿不到就当没填**。出发城市解析不出坐标、接口失败、两城太近，都返回空方案，
  让整单按"没填出发城市"继续——为可选增强打断规划是最糟的取舍。
"""

import math
from typing import Optional

from ..clients.amap_place import resolve_anchor_location
from ..clients.base import RouteClient
from ..config.settings import AmapSettings
from ..models.intercity_plan import (
    MODE_FLIGHT,
    MODE_HIGH_SPEED_RAIL,
    MODE_ORDINARY_RAIL,
    InterCityOption,
    InterCityPlan,
)
from ..models.location import Location
from ..models.map_point import MapPoint
from ..models.trip_plan_request import TripPlanRequest
from ..utils import round_half_up

# 直线估价格用的系数（元/公里），只在跨城接口拿不到时兜底。
_STRAIGHT_COST_PER_KM = {
    MODE_HIGH_SPEED_RAIL: 0.5,
    MODE_ORDINARY_RAIL: 0.21,
    MODE_FLIGHT: 0.9,
}
_FLIGHT_MIN_COST = 400
# 估算档的耗时按"公里 ÷ 速度"推（公里/小时）。**接口给了真实 duration 时用真实的**，
# 这几个数只在估算时用，包括永远没有真实数据的飞机。
#
# 为什么要推：选档第二道是"从最快往慢试"（见 `_pick_option`），而 `one_way_minutes`
# 不填就是 0——0 会被排序当成"最快"，飞机于是无条件夺冠。宁可推一个也不留 0。
_STRAIGHT_SPEED_KMH = {
    MODE_HIGH_SPEED_RAIL: 250.0,
    MODE_ORDINARY_RAIL: 75.0,
    MODE_FLIGHT: 700.0,
}
# 飞机那两小时是候机＋两端接驳，跟距离无关，单加而不是折进速度里。
_FLIGHT_FIXED_MINUTES = 120
# 直线不到这个公里数就不给"飞机"这一档：近程高铁本来就更快更省，造一条飞机只是噪声。
_FLIGHT_MIN_KM = 800.0
# 两城直线不到这个数就不算大交通：同城或近郊，硬造一笔"往返票价"只会污染预算。
_MIN_INTERCITY_KM = 150.0
# 往返总花费超过总预算这个比例就淘汰该档；全被淘汰时取最便宜的一条并让方案标红。
_INTERCITY_BUDGET_RATIO = 0.35
# 到达站离目的地市中心超过这个距离，就不算"到了这座城"——见 `_pick_option` 里那条实测反例。
_MAX_STATION_KM = 60.0


class IntercityService:

    def __init__(self, amap: AmapSettings, route_client: RouteClient):
        self._amap = amap
        self._route_client = route_client

    def plan(self,
             request: TripPlanRequest,
             destination_center: Optional[Location] = None,
             spend_estimator=None) -> InterCityPlan:
        """`spend_estimator(大交通费用) -> 整趟总花费` 是选档第二道的判据，见 `_pick_option`。

        不传就退回"过闸门里挑最便宜"的老行为——单测和没接规划侧的调用方都走那一条。
        """
        origin_city = (request.origin_city or "").strip()
        if not origin_city or origin_city == (request.city or "").strip():
            return InterCityPlan()
        origin_location = resolve_anchor_location(self._amap, origin_city, origin_city)
        destination_location = destination_center or resolve_anchor_location(self._amap, request.city, request.city)
        if origin_location is None or destination_location is None:
            return InterCityPlan(notes=[f"出发地「{origin_city}」或目的地没解析出坐标，本次未计城际交通"])

        straight_km = _straight_km(origin_location, destination_location)
        if straight_km < _MIN_INTERCITY_KM:
            return InterCityPlan()

        try:
            options = self._route_client.plan_intercity(
                origin_city, origin_location, request.city, destination_location, straight_km)
        except Exception as exception:  # noqa: BLE001 - 城际是可选增强，任何异常都不该打断整单规划
            options = []
            fallback_note = f"跨城查询异常（{type(exception).__name__}），大交通改按直线距离估算"
        else:
            fallback_note = None
        estimated_options = _straight_line_options(straight_km)
        if not options:
            options = estimated_options
        else:
            # **接口缺哪一档就补哪一档。** 原来只补飞机（"航班接口本来就没有"），
            # 隐含假设"铁路档接口一定给" —— 2026-10-12 实测上海→北京，同一个路由两次调用
            # 返回的档都不一样：一次只有普速 T110（于是 5000 预算的闸门 1750 只筛得出
            # 367 元普速，用户看着"预算充足却坐 16 小时硬座"），另一次只有高铁 1308
            # （于是 2000 预算没有便宜档可选）。**上游按档给谁是随机的**，
            # 所以按档补齐，两个方向的缺口一起堵。飞机同理（换乘方案里结构上就没有航班）。
            present = {option.mode for option in options}
            options = [*options, *(option for option in estimated_options
                                   if option.mode not in present)]

        chosen, dropped, considered = self._pick_option(
            options, request, destination_location, spend_estimator)
        if chosen is None:
            return InterCityPlan(notes=[f"{request.city}市内没有可落地的跨城方案" + (
                f"（排除了 {len(dropped)} 条到达站不在本市的方案）" if dropped else "")])
        arrival = self._station_point(request.city, chosen)
        notes = [n for n in (
            fallback_note,
            # 传 `considered`（过了「下车站」那道的）而不是 `options`：备注里那句「最便宜」
            # 引用的必须是真候选，不能是一条刚落地点不对、已被排除的方案（缺陷单 B1）。
            self._note_for(origin_city, request.city, chosen, considered, request.budget),
            (f"排除了 {len(dropped)} 条到达站不在{request.city}市内的方案（如『{dropped[0]}』），"
             "它们更便宜但落地点不对" if dropped else None),
        ) if n]
        return InterCityPlan(option=chosen, arrival_point=arrival, departure_point=arrival, notes=notes)

    def _station_point(self, city: str, option: InterCityOption) -> Optional[MapPoint]:
        """到达口岸直接用地接口响应里带的坐标，不再按站名去搜。

        按名搜会被 citylimit 模糊匹配到同名公交站（实测"湛江北"在广州市内命中一个 11 公里外的
        公交点），既慢又不可信；`arrival_stop.location` 才是这趟车真正落地的位置。
        """
        if option.arrival_location is None or not option.arrival_station:
            return None
        return MapPoint(name=option.arrival_station, address=f"{city}·{option.arrival_station}",
                        location=option.arrival_location, point_type="station")

    def _pick_option(self, options: list[InterCityOption], request: TripPlanRequest,
                     destination: Location,
                     spend_estimator=None,
                     ) -> tuple[Optional[InterCityOption], list[str], list[InterCityOption]]:
        """两道：先按"下车站在不在目的城市里"筛，再按"过闸门 + 够不够钱升级"选。

        2026-10-10 实测：上海→广州同一批方案里有一条 873 元的高铁，比 883 元那条便宜 10 元，
        车次名写的是"上海虹桥-湛江北"——乘客在湛江北之前下车（arrival_stop 是广州白云）才合理。
        只看价格会选到落地在别的城市的方案，所以先用自带坐标量一遍。

        **第二道（2026-10-12）**：闸门从"淘汰线"升格成"买得起"——过闸门的档里
        从**最快**往慢试，取第一个"整趟算下来不超支"的。
        原来只挑最便宜，实测杭州→北京（预算 5000）落到 400 元的普速、单程 16 小时，
        3 天行程实际只剩 1 天可玩，而高铁往返 1077 只占预算 21%。
        **全被闸门淘汰时仍取最便宜的**：已经超支了，不再按耗时往上挑——
        而"都超支"也意味着没得升，直接给最便宜的。
        `spend_estimator` 没给就退回老行为（只挑最便宜）。
        """
        dropped: list[str] = []
        eligible: list[InterCityOption] = []
        for option in options:
            location = option.arrival_location
            if location is not None and _straight_km(location, destination) > _MAX_STATION_KM:
                dropped.append(option.arrival_station or option.mode)
                continue
            eligible.append(option)
        if not eligible:
            return None, dropped, eligible
        gate = request.budget * _INTERCITY_BUDGET_RATIO
        passing = [option for option in eligible if option.round_trip_cost <= gate]
        if not passing:
            return min(eligible, key=lambda option: option.round_trip_cost), dropped, eligible
        cheapest = _cheapest_priced(passing)
        if spend_estimator is None:
            return cheapest, dropped, eligible
        for option in sorted(passing, key=lambda option: option.one_way_minutes):
            if spend_estimator(option.round_trip_cost) <= request.budget:
                return option, dropped, eligible
        return cheapest, dropped, eligible

    def _note_for(self, origin_city: str, city: str, chosen: InterCityOption,
                  options: list[InterCityOption], budget: int) -> str:
        """`options` 必须是**过了「下车站」那道的候选**（`_pick_option` 的第三个返回值）。

        传全量的话，那句「最便宜的一档是 X 元」里的 X 可能来自一条落地点不在目的城市、
        刚被排除掉的方案——等于报一个不该出现在候选里的价（缺陷单 B1）。
        """
        gate = round_half_up(budget * _INTERCITY_BUDGET_RATIO)
        cheapest = _cheapest_priced(options).round_trip_cost
        prefix = (f"城际按{origin_city}→{city}的{chosen.mode}估：往返约 {chosen.round_trip_cost} 元"
                  f"{'（按直线距离估算，非票价）' if chosen.estimated else '（高德换乘方案参考价，含两端接驳）'}")
        if chosen.round_trip_cost > gate:
            return prefix + f"；已超出预算的 {int(_INTERCITY_BUDGET_RATIO * 100)}%（{gate} 元），" \
                            f"但最便宜的一档也要 {cheapest} 元，按超支继续排"
        # 三种来历要分得清：超支硬排 / 预算够升上来的 / 只有它过闸门。
        # 原来只写"闸门内最省的一档"——有了升级之后这句会自相矛盾。
        if chosen.round_trip_cost > cheapest:
            return prefix + f"（预算够，已从更便宜的 {cheapest} 元一档升上来，省下路上时间）"
        return prefix + f"（预算闸门 {gate} 元内最省的一档）"


def _cheapest_priced(options: list[InterCityOption]) -> InterCityOption:
    """入围里最便宜的那一档。

    **铁路的估算档不当价格锚**，飞机可以 —— 因为两者的估算性质不同：

    - 飞机：换乘接口**结构上永远不给**，估算价是它唯一的数据来源，不认它就等于这一档
      永远选不出来（`test_long_distance_flight_can_win_the_price_comparison` 钉的就是这条）。
    - 铁路：接口**会给**真实票价，只是某一次没返回（见 `plan` 里那段：上海→北京两次调用
      一次只给普速、一次只给高铁）。这时补出来的估算只是**顶替**，有真实报价就该以真实为准。
      实测上海→杭州接口给了真实高铁 140，按 165 公里补出的估算普速只要 69 ——
      不挡的话，那句「最便宜的一档是 X 元」会引用一个编出来的数，还把真实价压掉。

    铁路估算档**照常参与选档**（升级那一轮按耗时排序，它跟真实档一起挑），只是不当价格锚。
    """
    priced = [option for option in options
              if not option.estimated or option.mode == MODE_FLIGHT]
    return min(priced or options, key=lambda option: option.round_trip_cost)


def _straight_km(first: Location, second: Location) -> float:
    lat_diff = first.latitude - second.latitude
    lng_diff = (first.longitude - second.longitude) * math.cos(math.radians((first.latitude + second.latitude) / 2))
    return math.sqrt(lat_diff * lat_diff + lng_diff * lng_diff) * 111.0


def _straight_line_options(straight_km: float) -> list[InterCityOption]:
    """按直线距离估各档价格；**飞机只在够远的段才给这一档**。

    163 公里的上海→杭州不该出现"飞机 800 元"这种选项：近程本来就是火车高铁的事，
    硬凑三档只会多一条没人会坐的推荐。早先这里"估不到 400 元就跳过这一档"配的是
    上面一句找不到飞机就抛 StopIteration 的 `next(...)`，两者叠在一起把 150~444 公里
    这一段打成了 500——抛错那半改成"找不到就不加"，这一半按"近程不给飞机"保留。
    """
    options = [
        InterCityOption(
            mode=mode,
            round_trip_cost=max(2, round_half_up(straight_km * factor * 2)),
            one_way_minutes=_estimated_minutes(mode, straight_km),
            estimated=True,
        )
        for mode, factor in _STRAIGHT_COST_PER_KM.items() if mode != MODE_FLIGHT
    ]
    if straight_km >= _FLIGHT_MIN_KM:
        options.append(InterCityOption(
            mode=MODE_FLIGHT,
            round_trip_cost=max(2, round_half_up(
                max(_FLIGHT_MIN_COST, straight_km * _STRAIGHT_COST_PER_KM[MODE_FLIGHT]) * 2)),
            one_way_minutes=_estimated_minutes(MODE_FLIGHT, straight_km),
            estimated=True,
        ))
    return options


def _estimated_minutes(mode: str, straight_km: float) -> int:
    """估算档的耗时。飞机多算两小时接驳，见 `_FLIGHT_FIXED_MINUTES`。"""
    minutes = straight_km / _STRAIGHT_SPEED_KMH[mode] * 60.0
    if mode == MODE_FLIGHT:
        minutes += _FLIGHT_FIXED_MINUTES
    return max(1, round_half_up(minutes))
