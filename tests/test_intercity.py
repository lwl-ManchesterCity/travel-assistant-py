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


def test_straight_line_options_cover_three_modes_with_a_flight_floor():
    modes = {option.mode for option in mod._straight_line_options(1200.0)}
    assert modes == {MODE_HIGH_SPEED_RAIL, MODE_ORDINARY_RAIL, MODE_FLIGHT}
    short = mod._straight_line_options(60.0)          # 60 公里：飞机估价低于 400 下限，被抬到下限
    assert min(option.round_trip_cost for option in short) >= 1


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
