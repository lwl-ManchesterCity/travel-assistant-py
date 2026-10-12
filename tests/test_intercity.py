"""城际大交通：选档、估价、口岸，以及它怎么改第一/最后一天的路线。

判据来自 docs/INTERCITY_TRANSPORT_DESIGN.md 第九节，全部用假数据跑，不发真实请求。
"""

import pytest

from travel_assistant.config.settings import AmapSettings
from travel_assistant.models.intercity_plan import (
    MODE_FLIGHT,
    MODE_HIGH_SPEED_RAIL,
    MODE_ORDINARY_RAIL,
    InterCityOption,
    InterCityPlan,
)
from travel_assistant.models.location import Location
from travel_assistant.models.trip_plan_request import TripPlanRequest
from travel_assistant.service import intercity_service as mod
from travel_assistant.service.intercity_service import IntercityService

SHANGHAI = Location(longitude=121.4737, latitude=31.2304)
GUANGZHOU = Location(longitude=113.2644, latitude=23.1291)


def _request(**overrides) -> TripPlanRequest:
    base = dict(city="广州", start_date="2026-10-10", end_date="2026-10-12", budget=2000,
                preferences=["自然风景"], accommodation="舒适型酒店")
    base.update(overrides)
    return TripPlanRequest(**base)


class _StubRouteClient:
    def __init__(self, options):
        self.options = options
        self.calls = []

    def plan_intercity(self, origin_city, origin, destination_city, destination, straight_km):
        self.calls.append((origin_city, destination_city, round(straight_km, 1)))
        return list(self.options)


@pytest.fixture
def any_city_geocoding(monkeypatch):
    """所有地名都给同一个坐标：只验流程，不验地理。"""
    monkeypatch.setattr(mod, "resolve_anchor_location",
                        lambda amap, city, keyword: SHANGHAI if city == "上海" else GUANGZHOU)


def test_no_origin_city_means_no_intercity_at_all(any_city_geocoding):
    """没填出发城市时这套逻辑一行都不该跑——它是整批改动的回归护栏。"""
    service = IntercityService(AmapSettings(api_key="k"), _StubRouteClient([]))

    plan = service.plan(_request())

    assert plan.option is None and plan.cost == 0 and plan.arrival_point is None
    assert service._route_client.calls == []


def test_same_city_origin_skips_intercity(any_city_geocoding):
    """出发城市写得和目的地一样：没有这段路，不该造一笔票价。"""
    client = _StubRouteClient([])
    service = IntercityService(AmapSettings(api_key="k"), client)

    plan = service.plan(_request(origin_city="广州"))

    assert plan.option is None and client.calls == []


def test_gate_drops_the_pricier_mode_and_keeps_the_cheap_one(any_city_geocoding):
    """2000 预算：高铁往返 1766 超 35% 闸门被淘汰，普速 490 通过。"""
    client = _StubRouteClient([
        InterCityOption(mode=MODE_HIGH_SPEED_RAIL, round_trip_cost=1766),
        InterCityOption(mode=MODE_ORDINARY_RAIL, round_trip_cost=490),
    ])
    service = IntercityService(AmapSettings(api_key="k"), client)

    plan = service.plan(_request(origin_city="上海"))

    assert plan.option.mode == MODE_ORDINARY_RAIL
    assert plan.cost == 490
    assert "预算闸门 700 元内最省的一档" in plan.notes[0]


def test_long_distance_flight_can_win_the_price_comparison(any_city_geocoding):
    """飞机进候选：它估出来最便宜时就会被选中（时间不参与，只有价格）。

    上海↔广州直线约 1211 公里，飞机估价＝1211×0.9×2 ≈ 2180，比这两档都低。
    """
    client = _StubRouteClient([
        InterCityOption(mode=MODE_HIGH_SPEED_RAIL, round_trip_cost=2600),
        InterCityOption(mode=MODE_ORDINARY_RAIL, round_trip_cost=2400),
    ])
    service = IntercityService(AmapSettings(api_key="k"), client)

    plan = service.plan(_request(origin_city="上海", budget=9000))

    assert plan.option.mode == MODE_FLIGHT
    assert plan.option.estimated is True
    assert "估算" in plan.notes[0]


def test_flight_loses_when_a_rail_mode_is_cheaper(any_city_geocoding):
    """反例：飞机不是"因为高级所以选它"，价格输就淘汰——这条钉住"只按价格判"。"""
    client = _StubRouteClient([
        InterCityOption(mode=MODE_ORDINARY_RAIL, round_trip_cost=490),
        InterCityOption(mode=MODE_HIGH_SPEED_RAIL, round_trip_cost=1766),
    ])
    service = IntercityService(AmapSettings(api_key="k"), client)

    plan = service.plan(_request(origin_city="上海", budget=9000))

    assert plan.option.mode == MODE_ORDINARY_RAIL and plan.cost == 490


def test_everything_over_the_gate_still_plans_the_cheapest_and_flags_it(any_city_geocoding):
    """预算太低又太远：不拒绝生成，取最便宜的一条并写明超支。"""
    client = _StubRouteClient([
        InterCityOption(mode=MODE_HIGH_SPEED_RAIL, round_trip_cost=1766),
        InterCityOption(mode=MODE_ORDINARY_RAIL, round_trip_cost=490),
    ])
    service = IntercityService(AmapSettings(api_key="k"), client)

    plan = service.plan(_request(origin_city="上海", budget=1000))

    assert plan.cost == 490
    assert "已超出预算的 35%" in plan.notes[0] and "按超支继续排" in plan.notes[0]


def test_estimated_options_carry_a_derived_duration():
    """判据 5⑤ 的前提：估算档也得有耗时。

    `one_way_minutes` 不填就是 0，而选档第二道是"从最快往慢试"——0 会被当成第一名，
    飞机于是无条件夺冠。这里钉的是**排序关系**，不是具体数字（数字是推算的，会调）。
    """
    minutes = {option.mode: option.one_way_minutes
               for option in mod._straight_line_options(1120.0)}

    assert all(value > 0 for value in minutes.values())
    assert minutes[MODE_FLIGHT] < minutes[MODE_HIGH_SPEED_RAIL] < minutes[MODE_ORDINARY_RAIL]
    assert minutes[MODE_ORDINARY_RAIL] > 600          # 1120 公里普速本来就是十几个小时


def test_a_roomy_budget_upgrades_to_the_faster_mode(any_city_geocoding):
    """判据 5③：杭州→北京那种单子。

    预算 5000、闸门 1750：高铁 1077 只占预算 21%，原来会落到 400 元的普速
    （单程 16 小时，3 天行程实际只剩 1 天可玩）。现在从最快试起，高铁整趟不超支 → 升上去。
    """
    client = _StubRouteClient([
        InterCityOption(mode=MODE_HIGH_SPEED_RAIL, round_trip_cost=1077, one_way_minutes=269),
        InterCityOption(mode=MODE_ORDINARY_RAIL, round_trip_cost=400, one_way_minutes=896),
    ])
    service = IntercityService(AmapSettings(api_key="k"), client)

    plan = service.plan(_request(origin_city="上海", budget=5000),
                        spend_estimator=lambda cost: cost + 2000)

    assert plan.option.mode == MODE_HIGH_SPEED_RAIL and plan.cost == 1077
    assert "已从更便宜的 400 元一档升上来" in plan.notes[0]


def test_a_tight_budget_stays_on_the_cheap_mode(any_city_geocoding):
    """反例：同一批档、预算只够便宜的——升级后整趟超支，退回普速。"""
    client = _StubRouteClient([
        InterCityOption(mode=MODE_HIGH_SPEED_RAIL, round_trip_cost=1077, one_way_minutes=269),
        InterCityOption(mode=MODE_ORDINARY_RAIL, round_trip_cost=400, one_way_minutes=896),
    ])
    service = IntercityService(AmapSettings(api_key="k"), client)

    # 预算 4000、闸门 1400：两档都过闸门，但高铁那档算下来 4077 超了
    plan = service.plan(_request(origin_city="上海", budget=4000),
                        spend_estimator=lambda cost: cost + 3000)

    assert plan.option.mode == MODE_ORDINARY_RAIL and plan.cost == 400
    assert "预算闸门 1400 元内最省的一档" in plan.notes[0]


def test_when_everything_is_over_the_gate_it_still_takes_the_cheapest(any_city_geocoding):
    """判据 5④：全超闸门时**不**按耗时往上挑——已经超支了，雪上加霜没有道理。

    estimator 一律回 0（"都付得起"）是为了让这条最能暴露问题：
    真按耗时挑的话，120 分钟的高铁会赢过 900 分钟的普速。
    """
    client = _StubRouteClient([
        InterCityOption(mode=MODE_HIGH_SPEED_RAIL, round_trip_cost=900, one_way_minutes=120),
        InterCityOption(mode=MODE_ORDINARY_RAIL, round_trip_cost=700, one_way_minutes=900),
    ])
    service = IntercityService(AmapSettings(api_key="k"), client)

    plan = service.plan(_request(origin_city="上海", budget=1000),
                        spend_estimator=lambda cost: 0)

    assert plan.option.mode == MODE_ORDINARY_RAIL and plan.cost == 700


def test_without_an_estimator_it_keeps_the_old_price_only_behaviour(any_city_geocoding):
    """没接规划侧时退回老行为——这条是"没有试算就不升级"的护栏。"""
    client = _StubRouteClient([
        InterCityOption(mode=MODE_HIGH_SPEED_RAIL, round_trip_cost=1077, one_way_minutes=269),
        InterCityOption(mode=MODE_ORDINARY_RAIL, round_trip_cost=400, one_way_minutes=896),
    ])
    service = IntercityService(AmapSettings(api_key="k"), client)

    plan = service.plan(_request(origin_city="上海", budget=5000))

    assert plan.option.mode == MODE_ORDINARY_RAIL and plan.cost == 400


def test_the_builder_hands_the_intercity_service_a_working_spend_estimator():
    """接线检查：选档第二道全靠这个回调。

    它断了**不会报错**，只会悄悄退回"只挑最便宜"——所以要有东西钉住它是真传进去的、
    而且拿着真酒店池子算得出来。这里只验"接上了且算得出数"，算得准不准由
    `test_planner_agent.py` 那条 `never_underestimates` 管。
    """
    from travel_assistant.models.hotel import Hotel
    from travel_assistant.service.planner_input_builder import PlannerInputBuilder

    seen = {}

    class _SpyService:
        def plan(self, request, spend_estimator=None):
            seen["estimator"] = spend_estimator
            return InterCityPlan()

    # 这条路只用到 _intercity_service，前面四个依赖传 None 就够
    builder = PlannerInputBuilder(None, None, None, None, _SpyService())

    builder._plan_intercity(_request(), [], [Hotel(name="杭州酒店", estimated_cost=560)])

    estimator = seen["estimator"]
    assert estimator is not None
    assert estimator(1077) > 1077          # 大交通之外还有住宿吃行，总花费必然大于票价本身


def test_arrival_station_becomes_the_day_one_start_point(any_city_geocoding):
    client = _StubRouteClient([InterCityOption(
        mode=MODE_ORDINARY_RAIL, round_trip_cost=490, arrival_station="广州南",
        arrival_location=Location(longitude=113.2680, latitude=22.9890))])
    service = IntercityService(AmapSettings(api_key="k"), client)

    plan = service.plan(_request(origin_city="上海"))

    assert plan.arrival_point.name == "广州南"
    assert plan.arrival_point.point_type == "station"
    assert plan.departure_point.name == "广州南"      # 回程同城同站，最后一天在这收工


def test_unresolvable_station_falls_back_to_no_start_point(any_city_geocoding):
    """站名解析不出坐标时宁可不画口岸：硬放一个城市中心当"到达站"会把第一天起点带偏。"""
    client = _StubRouteClient([InterCityOption(
        mode=MODE_ORDINARY_RAIL, round_trip_cost=490, arrival_station=None)])
    service = IntercityService(AmapSettings(api_key="k"), client)

    plan = service.plan(_request(origin_city="上海"))

    assert plan.cost == 490 and plan.arrival_point is None


def test_route_client_failure_degrades_to_straight_line_estimates(any_city_geocoding):
    """跨城接口挂了不打断规划：改按直线距离估三档，价格一律标估算。"""
    class _Broken:
        def plan_intercity(self, *args, **kwargs):
            raise RuntimeError("boom")

    service = IntercityService(AmapSettings(api_key="k"), _Broken())

    plan = service.plan(_request(origin_city="上海", budget=6000))

    assert plan.option is not None and plan.option.estimated is True


def test_flight_only_enters_the_pool_far_enough_to_be_real():
    """近程不造飞机这一档：163 公里的上海→杭州给"飞机 800 元"是噪声，不是选择。"""
    near = {option.mode for option in mod._straight_line_options(163.0)}
    assert near == {MODE_HIGH_SPEED_RAIL, MODE_ORDINARY_RAIL}

    far = {option.mode for option in mod._straight_line_options(1200.0)}
    assert far == {MODE_HIGH_SPEED_RAIL, MODE_ORDINARY_RAIL, MODE_FLIGHT}
    assert min(option.round_trip_cost for option in mod._straight_line_options(163.0)) >= 1


# 2026-10-10 真跑 /v3/direction/transit/integrated（上海→广州）拿到的响应，只留解析用到的字段。
_REAL_TRANSIT_BODY = {
    "status": "1",
    "route": {
        "transits": [
            {"cost": "883.000000", "duration": "30600", "segments": [
                {"bus": {"buslines": [{"name": "地铁2号线(浦东1号2号航站楼...)"}]}},
                {"railway": {"name": "G253(上海虹桥-广州南)", "trip": "G253", "time": "23220",
                            "arrival_stop": {"name": "广州南", "location": "113.269100 22.988300"}}},
                {"bus": {"buslines": [{"name": "地铁2号线(广州南站--嘉禾望岗)"}]}},
            ]},
            {"cost": "245.000000", "duration": "104940", "segments": [
                {"railway": {"name": "T34(昆山-深圳)", "trip": "T34", "time": "104940",
                             "arrival_stop": {"name": "广州白云", "location": "113.246341,23.190910"}}},
            ]},
            {"cost": "120.000000", "duration": "9000", "segments": [
                {"railway": {"name": "G7301(上海南-杭州东)", "trip": "G7301"}},
                {"railway": {"name": "G8521(杭州东-广州南)", "trip": "G8521"}},
            ]},
        ]
    }
}


def test_amap_intercity_parses_rail_class_round_trip_and_station(monkeypatch):
    from travel_assistant.clients import amap_route as route_mod
    from travel_assistant.clients.amap_route import AmapRouteClient
    from travel_assistant.clients.rule_route import RuleBasedRouteClient
    monkeypatch.setattr(route_mod, "fetch_json", lambda *args, **kwargs: _REAL_TRANSIT_BODY)
    client = AmapRouteClient(AmapSettings(api_key="k", mock_response=False), RuleBasedRouteClient())

    options = client.plan_intercity("上海", SHANGHAI, "广州", GUANGZHOU, 1211.0)

    by_mode = {option.mode: option for option in options}
    assert set(by_mode) == {MODE_HIGH_SPEED_RAIL, MODE_ORDINARY_RAIL}
    assert by_mode[MODE_HIGH_SPEED_RAIL].round_trip_cost == 1766      # 单程 883 × 2
    assert by_mode[MODE_HIGH_SPEED_RAIL].arrival_station == "广州南"
    assert by_mode[MODE_HIGH_SPEED_RAIL].one_way_minutes == 387       # 铁路段时间，只留档
    assert by_mode[MODE_HIGH_SPEED_RAIL].arrival_location.latitude == 22.9883
    assert by_mode[MODE_ORDINARY_RAIL].round_trip_cost == 490
    assert by_mode[MODE_ORDINARY_RAIL].arrival_station == "广州白云"   # 乘客真正下车的站


def test_multi_railway_transit_is_not_trusted(monkeypatch):
    """两段以上铁路的方案不是"一趟车到"，价格口径不可信，交给直线估兜底。"""
    from travel_assistant.clients import amap_route as route_mod
    from travel_assistant.clients.amap_route import AmapRouteClient
    from travel_assistant.clients.rule_route import RuleBasedRouteClient
    monkeypatch.setattr(route_mod, "fetch_json", lambda *args, **kwargs: _REAL_TRANSIT_BODY)
    client = AmapRouteClient(AmapSettings(api_key="k", mock_response=False), RuleBasedRouteClient())

    options = client.plan_intercity("上海", SHANGHAI, "广州", GUANGZHOU, 1211.0)

    assert all(option.round_trip_cost != 240 for option in options)   # 第三条 120×2 被丢掉


ZHANJIANG = Location(longitude=110.3990, latitude=21.2750)


def test_a_cheaper_plan_landing_in_the_wrong_city_is_rejected(monkeypatch):
    """2026-10-10 实测反例：873 元那条比 883 元便宜 10 元，下车站却在湛江（离广州约 400 公里）。
    只按价格判就会选到它，所以先用接口自带的坐标量一遍。"""

    def fake(amap, city, keyword):
        if keyword == "上海":
            return SHANGHAI
        if keyword == "广州":
            return GUANGZHOU
        return ZHANJIANG if keyword == "湛江北" else GUANGZHOU

    monkeypatch.setattr(mod, "resolve_anchor_location", fake)
    client = _StubRouteClient([
        InterCityOption(mode=MODE_HIGH_SPEED_RAIL, round_trip_cost=1746, arrival_station="湛江北",
                        arrival_location=ZHANJIANG),
        InterCityOption(mode=MODE_HIGH_SPEED_RAIL, round_trip_cost=1766, arrival_station="广州南",
                        arrival_location=Location(longitude=113.2691, latitude=22.9883)),
    ])
    service = IntercityService(AmapSettings(api_key="k"), client)

    plan = service.plan(_request(origin_city="上海", budget=9000))

    assert plan.cost == 1766 and plan.arrival_point.name == "广州南"
    assert any("湛江北" in note and "排除" in note for note in plan.notes)


def test_flight_price_respects_the_floor_when_it_is_offered():
    """够远才给这一档；给了之后 400 元/人是价格下限（不是"估不到就消失"）。"""
    options = mod._straight_line_options(820.0)   # 0.9×820=738 > 400，按里程算
    assert next(o for o in options if o.mode == MODE_FLIGHT).round_trip_cost == 1476
    short_flight = mod._straight_line_options(900.0)
    assert next(o for o in short_flight if o.mode == MODE_FLIGHT).round_trip_cost == 1620


def test_short_hop_with_a_real_rail_plan_does_not_break(monkeypatch):
    """接口只给高铁、距离又近到不该有飞机时：不加飞机这一档，也不能抛（曾经 500）。"""
    HANGZHOU = Location(longitude=120.155, latitude=30.274)

    def fake(amap, city, keyword):
        return SHANGHAI if keyword == "上海" else HANGZHOU

    monkeypatch.setattr(mod, "resolve_anchor_location", fake)
    client = _StubRouteClient([InterCityOption(
        mode=MODE_HIGH_SPEED_RAIL, round_trip_cost=140, arrival_station="杭州东",
        arrival_location=HANGZHOU)])
    service = IntercityService(AmapSettings(api_key="k"), client)

    plan = service.plan(_request(city="杭州", origin_city="上海", budget=2000))

    assert plan.option.mode == MODE_HIGH_SPEED_RAIL and plan.cost == 140
    assert plan.arrival_point.name == "杭州东"


def test_a_dropped_option_never_becomes_the_quoted_cheapest_price(any_city_geocoding):
    """缺陷单 B1：备注里那句「最便宜的一档是 X 元」，X 不能来自刚落地点不对、已被排除的方案。

    构造一条更便宜但下车站不在广州的普速，加一条贵的高铁：
    高铁被选中，那句「最便宜」必须引 490（入围里最便宜的），**不是 380**。
    """
    client = _StubRouteClient([
        InterCityOption(mode=MODE_ORDINARY_RAIL, round_trip_cost=380, one_way_minutes=900,
                        arrival_station="湛江北",
                        arrival_location=Location(longitude=110.36, latitude=21.27)),   # 离广州 400 公里
        InterCityOption(mode=MODE_HIGH_SPEED_RAIL, round_trip_cost=1077, one_way_minutes=269),
        InterCityOption(mode=MODE_ORDINARY_RAIL, round_trip_cost=490, one_way_minutes=896),
    ])
    service = IntercityService(AmapSettings(api_key="k"), client)

    # 预算 5000（闸门 1750）：飞机估 2180 超闸门被淘汰，剩下高铁与普速，
    # "升级"选中最快的高铁。预算再高飞机就会过闸门并夺冠，这里要避开它。
    plan = service.plan(_request(origin_city="上海", budget=5000),
                        spend_estimator=lambda cost: cost + 1000)

    assert plan.option.mode == MODE_HIGH_SPEED_RAIL
    assert "490" in plan.notes[0]          # 入围里最便宜的那条
    assert "380" not in plan.notes[0]      # 被排除的那条，一个字都不许出现


def test_a_missing_high_speed_tier_is_filled_from_the_straight_line_estimate(any_city_geocoding):
    """接口只回普速、没回高铁时，补一档估算高铁——否则"预算够就按耗时升级"没有档可升。

    2026-10-12 实测：上海→北京**同一个路由两次调用返回的档不一样**，一次只给普速 T110、
    一次只给高铁 1308。只给普速那次，5000 预算的闸门 1750 就只能筛出 367 元普速
    （单程 16 小时），用户看到的是"预算充足却坐硬座"。
    """
    client = _StubRouteClient([InterCityOption(
        mode=MODE_ORDINARY_RAIL, round_trip_cost=367, one_way_minutes=960,
        arrival_station="广州", arrival_location=GUANGZHOU)])
    service = IntercityService(AmapSettings(api_key="k"), client)

    plan = service.plan(_request(origin_city="上海", budget=5000),
                        spend_estimator=lambda cost: cost + 1000)

    # 直线上补出来的高铁（约 1211 元）比真实普速贵，但它快得多（291 分 vs 960 分），
    # 预算够就该升上去——这正是"35% 闸门 + 按耗时升级"要的效果。
    assert plan.option.mode == MODE_HIGH_SPEED_RAIL
    assert plan.option.estimated is True


def test_a_missing_ordinary_tier_is_filled_too(any_city_geocoding):
    """反过来也一样：接口只回高铁时补一档估算普速，否则低预算没有便宜档可选。

    这是上一条的镜像，是实测撞出来的——上游按档给谁是随机的，两个方向都得堵。
    """
    client = _StubRouteClient([InterCityOption(
        mode=MODE_HIGH_SPEED_RAIL, round_trip_cost=1308, one_way_minutes=270,
        arrival_station="广州南", arrival_location=GUANGZHOU)])
    service = IntercityService(AmapSettings(api_key="k"), client)

    # 预算 2000（闸门 700）：真实高铁 1308 过不去，只剩补出来的估算普速（约 509）。
    plan = service.plan(_request(origin_city="上海", budget=2000),
                        spend_estimator=lambda cost: cost + 500)

    assert plan.option.mode == MODE_ORDINARY_RAIL
    assert plan.option.estimated is True


def test_a_rail_estimate_never_becomes_the_quoted_cheapest(monkeypatch):
    """"最便宜的一档"那把尺子**只认真实铁路报价**——补出来的估算铁路档不当价格锚。

    上海→杭州这类近程：接口给了真实高铁 140，而按直线补的估算普速只要 69。
    不挡的话，备注里那句「最便宜的一档是 X 元」会引用一个编出来的数，还把真实价压掉。
    （飞机不在此列：换乘接口结构上就没有航班，估算价是它唯一的数据来源。）

    **这是一条护栏，不是回归测试**：它在"按档补齐"之前的代码上也能过
    （那时候根本不补铁路档，估算普速不会出现）。它守的是补齐和高低档规则**合在一起**
    之后不能再退回去——上面两条只验"补得进去"，这一条验"补进来的不当价格锚"。
    """
    HANGZHOU = Location(longitude=120.155, latitude=30.274)

    def fake(amap, city, keyword):
        return SHANGHAI if keyword == "上海" else HANGZHOU

    monkeypatch.setattr(mod, "resolve_anchor_location", fake)
    client = _StubRouteClient([InterCityOption(
        mode=MODE_HIGH_SPEED_RAIL, round_trip_cost=140, one_way_minutes=60,
        arrival_station="杭州东", arrival_location=HANGZHOU)])
    service = IntercityService(AmapSettings(api_key="k"), client)

    plan = service.plan(_request(city="杭州", origin_city="上海", budget=2000))

    assert plan.option.mode == MODE_HIGH_SPEED_RAIL and plan.cost == 140
