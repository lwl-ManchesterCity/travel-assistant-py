"""对应 src/test/java/com/lwl/travelassistant/agent/PlannerAgentTest.java。"""

from travel_assistant.agent.planner_agent import (
    _ARRIVAL_DAY,
    _DAY_ROUTE_CAP_KM,
    _DEPARTURE_DAY,
    PlannerAgent,
    _meal_cost_for_day,
    estimate_trip_spend,
)
from travel_assistant.agent.route_agent import RouteAgent
from travel_assistant.clients.llm import LlmClient
from travel_assistant.clients.rule_route import RuleBasedRouteClient
from travel_assistant.evaluator.budget_evaluator import BudgetEvaluator
from travel_assistant.evaluator.daily_plan_evaluator import DailyPlanEvaluator
from travel_assistant.evaluator.route_evaluator import RouteEvaluator
from travel_assistant.evaluator.weather_evaluator import WeatherEvaluator
from travel_assistant.models.attraction import Attraction
from travel_assistant.models.attraction_search_result import AttractionSearchResult
from travel_assistant.models.hotel import Hotel
from travel_assistant.models.hotel_search_result import HotelSearchResult
from travel_assistant.models.location import Location
from travel_assistant.models.map_point import MapPoint
from travel_assistant.models.planner_input import PlannerInput
from travel_assistant.models.planning_constraints import PlanningConstraints
from travel_assistant.models.provider_metadata import ProviderMetadata
from travel_assistant.models.trip_plan_request import TripPlanRequest
from travel_assistant.models.weather_info import WeatherInfo
from travel_assistant.models.weather_query_result import WeatherQueryResult
from travel_assistant.service.llm_narration_service import LlmNarrationService


class DisabledLlmClient(LlmClient):
    """对应测试里的 DisabledLlmClient。"""

    def is_available(self) -> bool:
        return False

    def chat(self, system_prompt: str, user_prompt: str) -> str:
        return ""


def build_planner_agent() -> PlannerAgent:
    return PlannerAgent(
        RouteAgent(RuleBasedRouteClient()),
        # Java 这里注入的是 ReflectionAgent(DailyPlanEvaluator)，本轮 ReflectionAgent 未搬，
        # reflect_daily_plan 直接落在 DailyPlanEvaluator 上。
        DailyPlanEvaluator(BudgetEvaluator(), RouteEvaluator(), WeatherEvaluator()),
        LlmNarrationService(DisabledLlmClient()),
    )


def build_planner_input() -> PlannerInput:
    request = TripPlanRequest(
        city="杭州",
        start_date="2026-07-01",
        end_date="2026-07-03",
        budget=3000,
        preferences=["美食", "自然风景"],
        transportation="公共交通",
        accommodation="舒适型酒店",
        extra_requirements="希望整体节奏轻松一些，对海鲜过敏",
    )
    attraction_result = AttractionSearchResult(
        city="杭州",
        preferences=request.preferences,
        attractions=[
            Attraction(name="西湖", address="杭州西湖景区", location=Location(longitude=120.1551, latitude=30.2741),
                       visit_duration=180, description="经典必去", category="自然风景", rating=4.8, ticket_price=60),
            Attraction(name="河坊街", address="杭州上城区", location=Location(longitude=120.1712, latitude=30.2429),
                       visit_duration=90, description="适合夜游", category="美食购物", rating=4.5, ticket_price=0),
            Attraction(name="灵隐寺", address="杭州西湖区", location=Location(longitude=120.1045, latitude=30.2417),
                       visit_duration=120, description="适合安静体验", category="人文历史", rating=4.7, ticket_price=45),
        ],
        summary="已匹配景点",
        provider=ProviderMetadata(provider_code="local-knowledge-base", provider_name="本地知识库景点源", mock=True),
    )
    weather_result = WeatherQueryResult(
        city="杭州",
        start_date=request.start_date,
        end_date=request.end_date,
        weather_info=[
            WeatherInfo(date="2026-07-01", day_weather="多云到晴", night_weather="晴", day_temp=28,
                        night_temp=22, wind_direction="东北风", wind_power="2级"),
            WeatherInfo(date="2026-07-02", day_weather="多云", night_weather="晴", day_temp=27,
                        night_temp=21, wind_direction="东风", wind_power="2级"),
            WeatherInfo(date="2026-07-03", day_weather="晴", night_weather="晴", day_temp=29,
                        night_temp=22, wind_direction="东南风", wind_power="3级"),
        ],
        summary="已获取天气",
        provider=ProviderMetadata(provider_code="rule-weather-engine", provider_name="规则天气引擎", mock=True),
    )
    hotel_result = HotelSearchResult(
        city="杭州",
        accommodation=request.accommodation,
        hotels=[
            Hotel(name="舒适型精选酒店", address="杭州中心城区", location=Location(longitude=120.18, latitude=30.25),
                  price_range="500-800元", rating="4.5", distance="距离景点1.5公里",
                  type="舒适型酒店", estimated_cost=560),
        ],
        summary="已推荐酒店",
        provider=ProviderMetadata(provider_code="rule-hotel-engine", provider_name="规则酒店推荐引擎", mock=True),
    )
    return PlannerInput(
        request=request,
        attraction_result=attraction_result,
        weather_result=weather_result,
        hotel_result=hotel_result,
        constraints=PlanningConstraints(
            relaxed_pace=True,
            avoid_seafood=True,
            wants_landmark_ceremony=False,
            prefers_indoor_backup=False,
            extracted_tags=["轻松节奏", "餐饮避开海鲜"],
        ),
        planning_notes=["优先考虑用户偏好：美食、自然风景"],
        planner_query="请生成杭州旅行计划",
        agent_traces=[],
    )


def test_should_build_structured_trip_plan_from_planner_input():
    trip_plan = build_planner_agent().build_plan(build_planner_input())

    assert len(trip_plan.days) == 3
    assert trip_plan.days[0].transportation == "公共交通"
    assert len(trip_plan.weather_info) == 3
    assert "杭州" in trip_plan.overall_suggestions
    assert trip_plan.budget.total > 0
    assert trip_plan.budget.requested_budget > 0
    assert trip_plan.days[0].meals[2].type == "dinner"
    assert "避开海鲜" in trip_plan.days[0].meals[2].description
    route_plan = trip_plan.days[0].route_plan
    assert len(route_plan.steps) >= 1
    assert route_plan.total_distance_km > 0
    assert trip_plan.days[0].estimated_cost > 0
    assert "ReflectionAgent 已对每日方案完成预算、路线、天气反思评估" in trip_plan.planning_notes
    assert any(trace.agent_name == "ReflectionAgent" for trace in trip_plan.agent_traces)


def _attraction(name, lng, lat, category, rating, price=0, duration=120):
    return Attraction(name=name, address="测试地址", location=Location(longitude=lng, latitude=lat),
                      visit_duration=duration, description="测试", category=category,
                      rating=rating, ticket_price=price)


def _hotel_at(lng, lat):
    return Hotel(name="测试酒店", address="测试", location=Location(longitude=lng, latitude=lat),
                 price_range="500-800元", rating="4.5", distance="x", type="舒适型酒店", estimated_cost=500)


def test_prioritize_is_pure_score_ordering_again():
    """离酒店分档那层撤了：这里回到 Java 原样，只按分数降序、票价升序。

    远近不在这步管，改由 `_compose_day_route` 的"插入增量"在选的时候管——
    顺序若由酒店推出来，就没法再拿顺序去定酒店（docs/PLANNER_SELECTION_DESIGN.md 3.1）。
    """
    agent = build_planner_agent()
    near = _attraction("近处", 120.0, 30.03, "自然风景", 3.0)   # 分低但在酒店旁边
    far = _attraction("远处", 120.0, 30.12, "人文历史", 4.9)     # 分高但在 13 公里外
    ranked = agent._prioritize_candidate_attractions([far, near], ["人文历史"], None, None, 1000)
    assert [a.name for a in ranked] == ["远处", "近处"]


# ---------- 3.5：判重按 POI id，不按名字 ----------

def test_two_attractions_sharing_a_name_are_not_collapsed():
    """老毛病一：按名字判重，同名的第二个永远选不上。现在按 id，它是另一个景点。"""
    agent = build_planner_agent()
    first = _attraction("老字号", 120.15, 30.27, "美食购物", 4.9)
    second = _attraction("老字号", 120.20, 30.27, "美食购物", 4.8)
    first.id, second.id = "B001", "B002"

    route = agent._compose_day_route(
        Location(longitude=120.15, latitude=30.27), [first, second], 2, set(), 9999)

    assert [a.id for a in route] == ["B001", "B002"]


def test_the_same_attraction_under_a_slightly_different_name_is_still_one():
    """老毛病二：名字差一个括号被当成两个，可能两天各去一遍。同 id 就是同一个。"""
    agent = build_planner_agent()
    full = _attraction("西湖景区", 120.15, 30.27, "自然风景", 4.8)
    gate = _attraction("西湖景区（南门）", 120.15, 30.27, "自然风景", 4.8)
    full.id = gate.id = "B001"

    assert agent._contains_attraction([full], agent._attraction_key(gate))


def test_without_a_poi_id_identity_falls_back_to_the_name():
    """本地知识库那个源造不出 id，退回比名字——与加这一格之前一致，不会因此漏判。"""
    agent = build_planner_agent()
    one = _attraction("西湖", 120.15, 30.27, "自然风景", 4.8)
    other = _attraction("西湖", 120.16, 30.27, "自然风景", 4.6)

    assert agent._attraction_key(one) == agent._attraction_key(other) == "西湖"
    assert agent._contains_attraction([one], agent._attraction_key(other))


# 经度每 0.1 度 ≈ 9.6 公里（北纬 30 度），下面几条按这个尺度摆坐标。

def test_ordinary_day_inserts_the_stop_that_adds_the_least_detour():
    """4.4 的推演：起点 119.9，候选 A 120.0／B 120.1／C 120.2，要 2 个。

    分数是 C 最高，但第一步选 A——它离起点最近；第二步 B 只多绕 9.6 公里，C 要多 19.3
    且插完 28.9 公里超上限。**顺序由插入位置定，不由分数或离酒店远近定。**
    """
    agent = build_planner_agent()
    start = Location(longitude=119.9, latitude=30.0)
    a = _attraction("A", 120.0, 30.0, "自然风景", 4.0)
    b = _attraction("B", 120.1, 30.0, "自然风景", 4.0)
    c = _attraction("C", 120.2, 30.0, "自然风景", 5.0)

    route = agent._compose_day_route(start, [c, a, b], 2, set(), 9999)

    assert [x.name for x in route] == ["A", "B"]


def test_ordinary_day_never_exceeds_the_route_cap():
    """判据 1：排出来的动线总长不超上限。"""
    agent = build_planner_agent()
    start = Location(longitude=119.9, latitude=30.0)
    pool = [_attraction(f"景{i}", 119.9 + i * 0.05, 30.0, "自然风景", 4.5) for i in range(8)]

    route = agent._compose_day_route(start, pool, 4, set(), 9999)

    assert len(route) == 4
    assert agent._route_length_km(route, start, None) <= _DAY_ROUTE_CAP_KM


def test_widening_the_cap_picks_up_a_second_stop():
    """25 排不出就放宽到 50。放宽的好处是**还能多接一站**，不是只把最近的那个硬塞进来。

    起点 →33km→ A →3km→ B：25 这一级两个都进不来，50 这一级 A、B 一起进。
    """
    agent = build_planner_agent()
    start = Location(longitude=120.0, latitude=30.0)
    a = _attraction("A", 120.35, 30.0, "自然风景", 4.0)      # 离起点约 33.7 公里
    b = _attraction("B", 120.383, 30.0, "自然风景", 4.0)     # 离 A 约 3.2 公里

    route = agent._compose_day_route(start, [a, b], 2, set(), 9999)

    assert [x.name for x in route] == ["A", "B"]


def test_ordinary_day_would_rather_plan_fewer_than_a_rushed_route():
    """放到 50 还是排不满：不硬凑到 target_count——但也不能一个都不排（防选空）。"""
    agent = build_planner_agent()
    start = Location(longitude=120.0, latitude=30.0)
    far = [_attraction(f"远{i}", 120.5 + i * 0.1, 30.0, "自然风景", 4.0) for i in range(3)]

    route = agent._compose_day_route(start, far, 2, set(), 9999)

    # 第一个不受门票限制、也不受"已选"限制的那条兜底路径接手，只留一个。
    assert len(route) == 1


def test_best_insertion_picks_the_cheapest_slot_not_just_the_end():
    """插的位置本身决定顺序：B 在起点和 A 之间，就该插到 A 前面。"""
    agent = build_planner_agent()
    start = Location(longitude=120.0, latitude=30.0)
    a = _attraction("A", 120.3, 30.0, "自然风景", 4.0)
    b = _attraction("B", 120.1, 30.0, "自然风景", 4.0)

    index, inserted_km = agent._best_insertion(start, [a], b)

    via_b = agent._route_length_km([b, a], start, None)   # 起点→B→A
    via_a = agent._route_length_km([a, b], start, None)   # 起点→A→B

    assert index == 0
    assert abs(inserted_km - via_b) < 1e-6   # 挑中的确实是最省的那个位置
    assert inserted_km < via_a               # 而且它真的更省，不是并列


def test_arrival_day_picks_the_best_within_the_port_circle():
    """判据 3：到达日在到达口岸的半径圈里取分数最高的那个，圈外的最高分也不许越圈。"""
    agent = build_planner_agent()
    port = MapPoint(name="杭州东站", address="杭州", point_type="station",
                    location=Location(longitude=120.2, latitude=30.29))
    low = _attraction("圈内低分", 120.21, 30.29, "自然风景", 3.0)
    high = _attraction("圈内高分", 120.22, 30.29, "人文历史", 4.9)
    outside = _attraction("圈外最高分", 120.9, 30.29, "人文历史", 5.0)

    picked = agent._pick_day_attractions(
        [low, high, outside], _ARRIVAL_DAY, port, None, 1000, 1, set(), None, None, None)

    assert [a.name for a in picked] == ["圈内高分"]


def test_arrival_day_widens_the_circle_until_something_is_there():
    """机场都在远郊（萧山机场离西湖 25 公里+），10 公里圈里一个景点都没有是常态。

    圈里不够挑就逐级放宽，最后一级"不限范围"纯按分数——没有这一级到达日会选空。
    """
    agent = build_planner_agent()
    airport = MapPoint(name="萧山机场", address="杭州", point_type="airport",
                       location=Location(longitude=120.43, latitude=30.23))
    only = _attraction("离机场 41 公里", 120.0, 30.25, "自然风景", 4.0)

    picked = agent._pick_day_attractions(
        [only], _ARRIVAL_DAY, airport, None, 1000, 1, set(), None, None, None)

    assert [a.name for a in picked] == ["离机场 41 公里"]


def test_departure_day_takes_the_union_of_the_hotel_and_the_port():
    """判据 4 的"或"是**并集**：要求和酒店、口岸同时近，等于要求住机场旁边，一个都选不出来。"""
    agent = build_planner_agent()
    hotel = _hotel_at(120.15, 30.27)
    airport = MapPoint(name="萧山机场", address="杭州", point_type="airport",
                       location=Location(longitude=120.43, latitude=30.23))
    near_hotel = _attraction("酒店旁", 120.16, 30.27, "自然风景", 4.0)
    near_airport = _attraction("机场旁", 120.42, 30.23, "人文历史", 4.9)   # 离酒店 26 公里

    picked = agent._pick_day_attractions(
        [near_hotel, near_airport], _DEPARTURE_DAY, None, airport, 1000, 1, set(), None, None, None,
        hotel=hotel)

    assert [a.name for a in picked] == ["机场旁"]


def test_hotel_lands_on_the_days_last_stop_not_the_average():
    """判据 5：酒店贴当天动线的**末站**。两家一样贵、一样好，旧的算术平均法分不出来。"""
    agent = build_planner_agent()
    first = _attraction("第一站", 120.10, 30.30, "自然风景", 4.0)
    last = _attraction("末站", 120.30, 30.30, "自然风景", 4.0)
    hotels = [_hotel("贴第一站", 120.10, 30.30, 500), _hotel("贴末站", 120.30, 30.30, 500)]

    chosen = agent._select_hotel(hotels, [first, last])

    assert chosen.name == "贴末站"


def test_a_roomy_budget_targets_the_cap_instead_of_the_cheapest_room():
    """预算宽时"贵"不该永远是减分——否则系统永远挑最便宜的那家，钱花不掉。

    5000 预算那单实测：日预算 1533、住宿上限 690，却只选到 400 出头的房，三天剩 1899。
    上限在那条路上只是"资格线"，没有一处照预算办事。

    三家同位置、同评分，只有价钱不同。理想价 ＝ 上限 × 0.78。
    """
    agent = build_planner_agent()
    last = _attraction("末站", 120.30, 30.30, "自然风景", 4.0)
    hotels = [
        _hotel("最便宜", 120.30, 30.30, 400),
        _hotel("贴着理想价", 120.30, 30.30, 540),
        _hotel("顶到上限", 120.30, 30.30, 680),
    ]
    cap = 690                                    # 理想价 ≈ 538

    assert agent._select_hotel(hotels, [last], cap).name == "贴着理想价"
    # 不给上限（必去景点那条路）时保持老行为：贵是减分，挑最便宜的
    assert agent._select_hotel(hotels, [last]).name == "最便宜"


def test_apply_day_pins_puts_pin_last_and_anchors_hotel_near_it():
    from travel_assistant.agent.planner_agent import _DailyPlanDecision
    agent = build_planner_agent()
    fixture = build_planner_input()
    pool = fixture.attraction_result.attractions
    hotels = fixture.hotel_result.hotels
    lingyin = next(a for a in pool if a.name == "灵隐寺")
    west_lake = next(a for a in pool if a.name == "西湖")
    decision = _DailyPlanDecision(None, [west_lake], "公共交通", [], None, 100, [])

    out = agent._apply_day_pins(decision, [lingyin], "杭州", 1, 50, hotels, None, "灵隐寺", None)

    assert out.attractions[-1].name == "灵隐寺"   # 必去放当天最后一站
    assert out.hotel is not None                    # 同意挪酒店 → 酒店锚到最后一站附近
    note = next(n for n in out.notes if "灵隐寺" in n)
    assert "最后一站" in note and "超日预算" in note   # 代价用具体数字写进备注


def test_apply_day_pins_two_pins_replaces_day_with_only_them():
    from travel_assistant.agent.planner_agent import _DailyPlanDecision
    agent = build_planner_agent()
    fixture = build_planner_input()
    pool = fixture.attraction_result.attractions
    hotels = fixture.hotel_result.hotels
    a = next(x for x in pool if x.name == "西湖")
    b = next(x for x in pool if x.name == "灵隐寺")
    other = next(x for x in pool if x.name == "河坊街")
    decision = _DailyPlanDecision(None, [other], "公共交通", [], None, 100, [])

    out = agent._apply_day_pins(decision, [a, b], "杭州", 1, 5000, hotels, None, None, None)

    assert {x.name for x in out.attractions} == {"西湖", "灵隐寺"}   # 只留点名的两个，不补别的
    assert any("按你点名的必去景点安排" in n for n in out.notes)


def test_apply_day_pins_keeps_hotel_when_move_not_allowed():
    from travel_assistant.agent.planner_agent import _DailyPlanDecision
    agent = build_planner_agent()
    fixture = build_planner_input()
    pool = fixture.attraction_result.attractions
    hotels = fixture.hotel_result.hotels
    lingyin = next(a for a in pool if a.name == "灵隐寺")
    west_lake = next(a for a in pool if a.name == "西湖")
    keep = hotels[0]
    decision = _DailyPlanDecision(keep, [west_lake], "公共交通", [], None, 100, [])

    out = agent._apply_day_pins(decision, [lingyin], "杭州", 1, 5000, hotels, None, None, None)

    assert out.hotel is keep                        # 用户选"不挪"→酒店保持不动
    assert out.attractions[-1].name == "灵隐寺"


def test_resolve_pinned_attractions_uses_name_search_when_not_in_pool():
    from travel_assistant.models.attraction import Attraction
    from travel_assistant.models.location import Location
    from travel_assistant.service.planner_input_builder import PlannerInputBuilder

    class _FakeAttractionClient:
        def find_attraction_by_name(self, city, name):
            return Attraction(name=name, address="补搜来的", location=Location(longitude=120.1, latitude=30.1),
                              visit_duration=120, description="d", category="自然风景", rating=4.6, ticket_price=0)

    builder = PlannerInputBuilder(None, None, None, _FakeAttractionClient())
    pool = build_planner_input().attraction_result.attractions  # 池里没有"白云山"
    request = TripPlanRequest(
        city="广州", start_date="2026-07-01", end_date="2026-07-03", budget=3000,
        preferences=["自然风景"], accommodation="舒适型酒店", day_attraction_pins={1: ["白云山"]},
    )

    pins_map, unresolved = builder._resolve_pinned_attractions(request, pool)

    assert [a.name for a in pins_map[1]] == ["白云山"]
    assert unresolved == []


def test_pinned_attraction_is_forced_into_its_day():
    planner_input = build_planner_input()
    pool = planner_input.attraction_result.attractions
    hefang = next(a for a in pool if a.name == "河坊街")
    planner_input.request.day_attraction_pins = {0: ["河坊街"]}
    planner_input.pinned_attractions_by_day = {0: [hefang]}

    trip_plan = build_planner_agent().build_plan(planner_input)

    assert any(a.name == "河坊街" for a in trip_plan.days[0].attractions)


def test_resolve_pinned_attractions_matches_by_name_and_reports_misses():
    from travel_assistant.service.planner_input_builder import PlannerInputBuilder
    builder = PlannerInputBuilder(None, None, None)
    pool = build_planner_input().attraction_result.attractions
    request = TripPlanRequest(
        city="杭州", start_date="2026-07-01", end_date="2026-07-03", budget=3000,
        preferences=["自然风景"], accommodation="舒适型酒店",
        day_attraction_pins={1: ["河坊街", "不存在的景点"]},
    )

    pins_map, unresolved = builder._resolve_pinned_attractions(request, pool)

    assert [a.name for a in pins_map[1]] == ["河坊街"]
    assert unresolved == ["第2天『不存在的景点』"]


def _input_with_two_hotels():
    """同城两家酒店：一家贴着"用户另指的位置"，一家贴着灵隐寺。"""
    planner_input = build_planner_input()
    near_station = Hotel(name="杭州北站旁酒店", address="杭州拱墅区", location=Location(longitude=120.102, latitude=30.296),
                         price_range="400-600元", rating="4.3", type="舒适型酒店", estimated_cost=500)
    near_lingyin = Hotel(name="灵隐寺旁酒店", address="杭州西湖区", location=Location(longitude=120.1045, latitude=30.2417),
                         price_range="600-800元", rating="4.3", type="舒适型酒店", estimated_cost=600)
    planner_input.hotel_result.hotels = [near_station, near_lingyin]
    return planner_input


def test_pinned_day_keeps_the_hotel_place_the_user_named():
    """同一句里既点必去景点、又另指住宿位置：位置听人的，不跟着当天最后一站挪。"""
    planner_input = _input_with_two_hotels()
    lingyin = next(a for a in planner_input.attraction_result.attractions if a.name == "灵隐寺")
    planner_input.request.day_attraction_pins = {0: ["灵隐寺"]}
    planner_input.request.day_hotel_anchors = {0: "杭州北站"}
    planner_input.pinned_attractions_by_day = {0: [lingyin]}
    planner_input.day_hotel_anchor_locations = {0: Location(longitude=120.100, latitude=30.299)}
    planner_input.day_hotel_candidates = {0: list(planner_input.hotel_result.hotels)}

    trip_plan = build_planner_agent().build_plan(planner_input)

    day = trip_plan.days[0]
    assert day.hotel.name == "杭州北站旁酒店"          # 不是离当天末站更近的那家
    assert any("灵隐寺" in note for note in day.optimization_notes)
    assert any("杭州北站" in note and "没有跟着挪" in note for note in day.optimization_notes)


def test_pinned_day_reanchor_sees_hotels_found_by_the_anchor_search():
    """确认卡上点了"把酒店挪过去"时，重挑用的是那一天那份名单——按位置补搜回来的酒店只在里面。"""
    planner_input = _input_with_two_hotels()
    lingyin = next(a for a in planner_input.attraction_result.attractions if a.name == "灵隐寺")
    city_only = [h for h in planner_input.hotel_result.hotels if h.name == "杭州北站旁酒店"]
    planner_input.hotel_result.hotels = city_only            # 全城那份没有灵隐寺旁那家
    planner_input.request.day_attraction_pins = {0: ["灵隐寺"]}
    planner_input.request.day_hotel_anchors = {0: "灵隐寺"}   # 代填：位置名＝必去景点名
    planner_input.pinned_attractions_by_day = {0: [lingyin]}
    planner_input.day_hotel_anchor_locations = {0: lingyin.location}
    planner_input.day_hotel_candidates = {0: list(city_only) + [
        Hotel(name="灵隐寺旁酒店", address="杭州西湖区", location=lingyin.location,
              price_range="600-800元", rating="4.3", type="舒适型酒店", estimated_cost=500)]}

    trip_plan = build_planner_agent().build_plan(planner_input)

    assert trip_plan.days[0].hotel.name == "灵隐寺旁酒店"
    assert trip_plan.days[0].attractions[-1].name == "灵隐寺"


class _OrderStub:
    """替身：记录 planner 交给模型的候选，并按脚本返回编号。"""

    def __init__(self, answer=None):
        self.answer = answer
        self.calls: list[tuple] = []

    def choose_attraction_order(self, candidates, costs_km, weather, endpoint_label):
        self.calls.append((candidates, costs_km, endpoint_label))
        if self.answer is None:
            return None
        order, reason = self.answer
        return order, reason

    def is_available(self):
        return True


def _guangzhou_pins():
    """真实坐标：白云山在北、广州塔在中、番禺广场在南。"""
    tower = Attraction(name="广州塔", address="广州海珠区", category="城市漫游", ticket_price=150,
                       visit_duration=120, location=Location(longitude=113.322, latitude=23.105))
    mountain = Attraction(name="白云山", address="广州白云区", category="自然风景", ticket_price=5,
                          visit_duration=180, location=Location(longitude=113.29, latitude=23.18))
    plaza = Location(longitude=113.387, latitude=22.938)
    return tower, mountain, plaza


def test_day_pin_order_prefers_the_route_that_saves_miles():
    """给了收工地点：多绕 9 公里的那条不配进并列，直接按少绕路排，也不问模型。"""
    tower, mountain, plaza = _guangzhou_pins()
    agent = build_planner_agent()
    stub = _OrderStub()
    agent._llm_narration_service = stub

    order, reason = agent._order_day_pins([tower, mountain], None, None, plaza, "番禺广场")

    assert [a.name for a in order] == ["白云山", "广州塔"]   # 原话顺序是反的，被里程纠正
    assert stub.calls == []                                   # 差得远，没到让时段挑的那一步
    assert "少绕路" in reason


def test_day_pin_order_uses_time_slot_when_miles_are_equal():
    """没有收工地点（酒店要跟着末站走）：两序里程一样，交给时段常识——上午爬山、傍晚看塔。"""
    tower, mountain, _ = _guangzhou_pins()
    agent = build_planner_agent()
    agent._llm_narration_service = _OrderStub()             # 返回 None＝模型没给结论，走常识表

    order, reason = agent._order_day_pins([tower, mountain], None, None, None, None)

    assert [a.name for a in order] == ["白云山", "广州塔"]
    assert "常识" in reason


def test_day_pin_order_lets_model_pick_inside_the_tolerance():
    """并列时模型才有权选，且只能在给出的候选里选。"""
    tower, mountain, _ = _guangzhou_pins()
    agent = build_planner_agent()
    stub = _OrderStub(answer=([mountain, tower], "白云山适合上午，广州塔傍晚看夜景"))

    agent._llm_narration_service = stub
    order, reason = agent._order_day_pins([tower, mountain], None, None, None, None)

    assert len(stub.calls) == 1
    assert [a.name for a in order] == ["白云山", "广州塔"]
    assert "时段常识调整" in reason


def test_day_pin_order_ignores_a_choice_outside_the_candidates():
    """模型给的编号超出候选范围＝不合规，回落常识表，不跟着乱排。"""
    tower, mountain, _ = _guangzhou_pins()
    agent = build_planner_agent()

    class _BadStub(_OrderStub):
        def choose_attraction_order(self, candidates, costs_km, weather, endpoint_label):
            self.calls.append((candidates, costs_km, endpoint_label))
            return None

    stub = _BadStub()
    agent._llm_narration_service = stub
    order, _ = agent._order_day_pins([tower, mountain], None, None, None, None)

    assert [a.name for a in order] == ["白云山", "广州塔"]


def _hotel(name, lng, lat, cost, rating="4.2"):
    return Hotel(name=name, address="测试", location=Location(longitude=lng, latitude=lat),
                 rating=rating, type="舒适型酒店", estimated_cost=cost)


def test_head_and_tail_days_plan_one_attraction_and_middle_days_two():
    """头尾两天各一个景点（到达和返程各占半天），中间天两个。"""
    planner_input = build_planner_input()
    planner_input.request.end_date = "2026-07-04"
    planner_input.request.budget = 3000
    planner_input.weather_result.weather_info.append(
        WeatherInfo(date="2026-07-04", day_weather="晴", night_weather="晴", day_temp=28,
                    night_temp=22, wind_direction="东风", wind_power="2级"))
    planner_input.attraction_result.attractions = [
        _attraction(f"景点{i}", 120.15 + i * 0.004, 30.27, "自然风景", 4.5, price=60) for i in range(8)
    ]
    planner_input.hotel_result.hotels = [_hotel("经济型酒店", 120.15, 30.27, 250)]

    trip_plan = build_planner_agent().build_plan(planner_input)

    assert [len(day.attractions) for day in trip_plan.days] == [1, 2, 2, 1]


def test_hotel_budget_cap_keeps_the_pricier_hotel_out_of_scoring():
    """住宿上限是资格过滤：超上限的那家连打分都进不去，不是被扣分扣掉。"""
    agent = build_planner_agent()
    cap = agent._hotel_budget_cap(503, 2)
    assert cap == 226                                   # min(503×0.45, 503−120−155)

    kept = agent._cap_hotel_candidates(
        [_hotel("便宜店", 120.15, 30.27, 200), _hotel("贵店", 120.15, 30.27, 400, rating="4.8")], cap, [])

    assert [h.name for h in kept] == ["便宜店"]          # 贵 0.6 星也救不回来


def test_hotel_cap_falls_back_with_a_note_when_nothing_fits():
    agent = build_planner_agent()
    notes: list[str] = []
    hotels = [_hotel("只有这家", 120.15, 30.27, 560, rating="4.5")]

    kept = agent._cap_hotel_candidates(hotels, 450, notes)

    assert [h.name for h in kept] == ["只有这家"]
    assert notes and "住宿上限 450" in notes[0]


def test_local_transport_cost_is_flat_for_public_transit_and_mileage_based_otherwise():
    from travel_assistant.models.route_plan import RoutePlan
    agent = build_planner_agent()
    route = lambda km, mode: RoutePlan(total_distance_km=km, transportation_mode=mode)

    assert agent._estimate_transportation_cost(route(40, "公共交通")) == 20
    assert agent._estimate_transportation_cost(route(40, "公共交通 + 短途打车")) == 360
    assert agent._estimate_transportation_cost(route(40, "自驾")) == 48


def test_ticket_reserve_is_planned_before_the_hotel_takes_its_share():
    """门票是按"目标数 × 60"预留的，不再跟酒店抢同一笔日预算。

    老公式给门票只剩 666−500−180＝负数，一个景点都装不下；改成先预留之后能装下两个，
    第三个（累计 180）才被挡。酒店在这条路上根本不参与，这正是当初换公式要达到的效果。
    """
    agent = build_planner_agent()
    pool = [_attraction(f"景{i}", 120.15 + i * 0.003, 30.27, "自然风景", 4.5, price=60) for i in range(4)]

    picked = agent._compose_day_route(
        Location(longitude=120.15, latitude=30.27), pool, 3, set(), 2 * 60)

    assert len(picked) == 2


def test_each_day_starts_where_the_previous_night_ended():
    """真实动线：第 2 天从前一晚那家酒店起步；没填出发城市时第 1 天仍从首景点起步。"""
    trip_plan = build_planner_agent().build_plan(build_planner_input())

    first = trip_plan.days[0].route_plan
    second = trip_plan.days[1].route_plan
    assert first.steps[0].from_name == trip_plan.days[0].attractions[0].name
    assert second.steps[0].from_name == trip_plan.days[0].hotel.name
    assert second.start_point.name == trip_plan.days[0].hotel.name
    assert second.start_point.point_type == "hotel"


def test_known_start_makes_the_order_a_calculation_not_a_guess():
    """起点已知、收工点未知时，"谁先"由里程定：先去离出发酒店近的那站，常识退到兜底。"""
    tower, mountain, plaza = _guangzhou_pins()
    agent = build_planner_agent()
    agent._llm_narration_service = _OrderStub()          # 返回 None＝不采纳模型

    order, reason = agent._order_day_pins([mountain, tower], None, plaza, None, None)

    assert [a.name for a in order] == ["广州塔", "白云山"]   # 广州塔离番禺广场近得多
    assert "少绕路" in reason


def test_intercity_endpoints_reach_the_daily_routes():
    """填了出发城市之后：第一天从到达口岸起步，最后一天在返程站收工，票价进预算那一桶。"""
    from travel_assistant.models.map_point import MapPoint
    planner_input = build_planner_input()
    planner_input.request.origin_city = "上海"
    planner_input.intercity_cost = 490
    planner_input.intercity_mode = "普速火车"
    planner_input.intercity_estimated = False
    arrival = MapPoint(name="广州南", address="广州番禺区",
                       location=Location(longitude=113.268, latitude=22.989), point_type="station")
    planner_input.arrival_point = arrival
    planner_input.day_end_points = {2: arrival}

    trip_plan = build_planner_agent().build_plan(planner_input)

    assert trip_plan.days[0].route_plan.start_point.name == "广州南"
    assert trip_plan.days[0].route_plan.steps[0].from_name == "广州南"
    assert trip_plan.days[2].route_plan.end_point.name == "广州南"
    assert trip_plan.days[2].route_plan.steps[-1].to_name == "广州南"
    assert trip_plan.budget.total_intercity == 490
    assert trip_plan.budget.intercity_mode == "普速火车"
    # 490 先扣掉，日预算从 1000 降到 836 —— 头尾天仍 1 个、中间天按新钱排
    assert trip_plan.budget.total >= 490


def test_the_meal_ladder_matches_what_the_meal_builder_actually_charges():
    """大交通试算里的餐饮阶梯是从 `_build_meals` 抄下来的**和**——抄的东西必须有人钉着。

    两处一旦分家，试算就会低估或高估，而它的结论是"升不升级"。
    """
    agent = build_planner_agent()
    attraction = _attraction("吃饭的地方", 120.15, 30.27, "自然风景", 4.0)

    for daily_budget in (300, 400, 600, 900, 1700, 2600):
        real = sum(meal.estimated_cost for meal in agent._build_meals(
            "杭州", ["自然风景"], None, [attraction], daily_budget))
        assert _meal_cost_for_day(daily_budget) == real, f"日预算 {daily_budget} 那档对不上"


def test_the_intercity_estimate_never_underestimates_a_real_plan():
    """试算只回答"升不升级"，但它**不能低估**。

    低估就意味着"试算说升得起、真跑却超支了"——那是风险 9 里最坏的方向。
    不需要估得准，只需要偏大：宁可少升一级，不可超支。
    """
    planner_input = build_planner_input()
    planner_input.intercity_cost = 0
    trip_plan = build_planner_agent().build_plan(planner_input)

    estimated = estimate_trip_spend(
        budget=planner_input.request.budget,
        days=planner_input.request.calculate_days(),
        hotels=planner_input.hotel_result.hotels,
        intercity_cost=0)

    assert estimated >= trip_plan.budget.total, (estimated, trip_plan.budget.total)
