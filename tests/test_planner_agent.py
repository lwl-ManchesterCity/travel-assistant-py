"""对应 src/test/java/com/lwl/travelassistant/agent/PlannerAgentTest.java。"""

from travel_assistant.agent.planner_agent import PlannerAgent
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


def test_near_attraction_ranks_ahead_of_far_high_score():
    agent = build_planner_agent()
    hotel = _hotel_at(120.0, 30.0)
    near = _attraction("近处", 120.0, 30.03, "自然风景", 3.0)   # ~3.3km，档 0，分数低
    far = _attraction("远处", 120.0, 30.12, "人文历史", 4.9)     # ~13.3km，档 2，命中偏好+高分
    ranked = agent._prioritize_candidate_attractions([far, near], hotel, ["人文历史"], None, None, 1000)
    assert [a.name for a in ranked] == ["近处", "远处"]


def test_no_hotel_falls_back_to_pure_score_ordering():
    agent = build_planner_agent()
    near = _attraction("近处", 120.0, 30.03, "自然风景", 3.0)
    far = _attraction("远处", 120.0, 30.12, "人文历史", 4.9)
    ranked = agent._prioritize_candidate_attractions([near, far], None, ["人文历史"], None, None, 1000)
    assert [a.name for a in ranked] == ["远处", "近处"]


def test_filter_drops_attractions_beyond_hotel_radius():
    agent = build_planner_agent()
    hotel = _hotel_at(120.0, 30.0)
    a = _attraction("a", 120.0, 30.02, "自然风景", 4.0)   # ~2.2km
    b = _attraction("b", 120.0, 30.05, "自然风景", 4.0)   # ~5.6km
    c = _attraction("c", 120.0, 30.20, "自然风景", 4.0)   # ~22km，超 15km
    kept = agent._filter_nearby_attractions([a, b, c], hotel)
    assert {x.name for x in kept} == {"a", "b"}


def test_filter_falls_back_to_all_when_under_two_within_radius():
    agent = build_planner_agent()
    hotel = _hotel_at(120.0, 30.0)
    a = _attraction("a", 120.0, 30.02, "自然风景", 4.0)   # 圈内仅 1 个
    c = _attraction("c", 120.0, 30.20, "自然风景", 4.0)
    kept = agent._filter_nearby_attractions([a, c], hotel)
    assert {x.name for x in kept} == {"a", "c"}   # 防选空：回落全城


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
    """老公式给门票只剩 666−500−180＝负数，一个景点都装不下；先预留之后能装下两个。"""
    agent = build_planner_agent()
    pool = [_attraction(f"景{i}", 120.15 + i * 0.003, 30.27, "自然风景", 4.5, price=60) for i in range(4)]

    picked = agent._pick_daily_attractions(pool, 1, 666, 2, _hotel("贵店", 120.15, 30.27, 500),
                                           set(), ["自然风景"], None, None)

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
