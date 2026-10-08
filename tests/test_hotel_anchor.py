"""选酒店补格子（docs/ADJUST_AGENT_DESIGN.md 第 9 节 9.7 的十条判据）的行为断言。

这一组用例钉的是"资格"而不是"分数"：填了住宿位置后，进入打分的候选集合怎么变、
整趟住同一家时两处换店入口是否都被堵住、以及不填时是否与 Java 逐字一致。
"""

from travel_assistant.agent.planner_agent import PlannerAgent
from travel_assistant.agent.route_agent import RouteAgent
from travel_assistant.clients.llm import LlmClient
from travel_assistant.clients.rule_route import RuleBasedRouteClient
from travel_assistant.config.settings import AmapSettings, AnchorSettings
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
from travel_assistant.models.provider_metadata import ProviderMetadata
from travel_assistant.models.trip_plan_request import TripPlanRequest
from travel_assistant.models.weather_info import WeatherInfo
from travel_assistant.models.weather_query_result import WeatherQueryResult
from travel_assistant.service.hotel_anchor_service import HotelAnchorService, HotelAnchors
from travel_assistant.service.llm_narration_service import LlmNarrationService
from travel_assistant.service.request_optimization_strategy_service import RequestOptimizationStrategyService

# 锚点定在西湖，三家候选按"贴湖／圈内边缘／圈外"摆开，价格故意反着来：
# 贴湖那家最贵（会吃到预算惩罚），原打分本来选不到它——这才看得出筛与档真的在起作用。
ANCHOR = Location(longitude=120.1551, latitude=30.2741)
NEAR_HOTEL = "贴湖精品酒店"
MID_HOTEL = "城内快捷酒店"
FAR_HOTEL = "远郊会议中心酒店"

_HOTELS = [
    Hotel(name=NEAR_HOTEL, type="精品", price_range="800-1000", estimated_cost=900,
          location=Location(longitude=120.1580, latitude=30.2770)),
    Hotel(name=MID_HOTEL, type="经济型", price_range="200-300", estimated_cost=260,
          location=Location(longitude=120.1950, latitude=30.2750)),
    Hotel(name=FAR_HOTEL, type="商务", price_range="150-250", estimated_cost=200,
          location=Location(longitude=120.3500, latitude=30.3000)),
]


class DisabledLlmClient(LlmClient):
    def is_available(self) -> bool:
        return False

    def chat(self, system_prompt: str, user_prompt: str) -> str:
        return ""


def _planner(anchor_settings: AnchorSettings | None = None) -> PlannerAgent:
    return PlannerAgent(
        RouteAgent(RuleBasedRouteClient()),
        DailyPlanEvaluator(BudgetEvaluator(), RouteEvaluator(), WeatherEvaluator()),
        LlmNarrationService(DisabledLlmClient()),
        anchor_settings or AnchorSettings(),
    )


def _request(**overrides) -> TripPlanRequest:
    values = dict(
        city="杭州",
        start_date="2026-07-01",
        end_date="2026-07-03",
        budget=3000,
        preferences=["自然风景"],
        transportation="公共交通",
        accommodation="舒适型酒店",
    )
    values.update(overrides)
    return TripPlanRequest(**values)


def _input(request: TripPlanRequest, anchor: Location | None = None) -> PlannerInput:
    attractions = [
        Attraction(name="西湖", address="杭州西湖景区", location=ANCHOR, visit_duration=180,
                   category="自然风景", rating=4.8, ticket_price=60),
        Attraction(name="河坊街", address="杭州上城区",
                   location=Location(longitude=120.1712, latitude=30.2429), visit_duration=90,
                   category="美食购物", rating=4.5, ticket_price=0),
        Attraction(name="灵隐寺", address="杭州西湖区",
                   location=Location(longitude=120.1045, latitude=30.2417), visit_duration=120,
                   category="人文历史", rating=4.7, ticket_price=45),
    ]
    return PlannerInput(
        request=request,
        attraction_result=AttractionSearchResult(
            city="杭州", preferences=request.preferences, attractions=attractions, summary="已匹配景点",
            provider=ProviderMetadata(provider_code="local-knowledge-base", provider_name="本地知识库景点源"),
        ),
        weather_result=WeatherQueryResult(
            city="杭州", start_date=request.start_date, end_date=request.end_date,
            weather_info=[WeatherInfo(date=f"2026-07-0{day}", day_weather="多云", night_weather="晴",
                                      day_temp=28, night_temp=22) for day in (1, 2, 3)],
        ),
        hotel_result=HotelSearchResult(
            city="杭州", hotels=[hotel.model_copy(deep=True) for hotel in _HOTELS], summary="已匹配住宿",
            provider=ProviderMetadata(provider_code="rule", provider_name="规则住宿候选"),
        ),
        constraints=None,
        planning_notes=[],
        agent_traces=[],
        hotel_anchor_location=anchor,
    )


def _narrow(planner: PlannerAgent, hotels: list[Hotel], anchor: Location | None, label: str = "西湖"):
    notes: list[str] = []
    return planner._narrow_hotel_candidates(hotels, anchor, label, notes), notes


def _day_hotels(plan) -> list[str]:
    return [day.hotel.name for day in plan.days]


def _notes(plan) -> list[str]:
    return [note for day in plan.days for note in (day.optimization_notes or [])]


def test_shouldKeepWholeCityCandidatesWhenAnchorIsNotFilled():
    """判据 1：没填格子时，候选集合原样交给打分，连列表对象都不换。"""
    planner = _planner()
    hotels = [hotel.model_copy(deep=True) for hotel in _HOTELS]

    narrowed, notes = _narrow(planner, hotels, None)

    assert narrowed is hotels
    assert notes == []


def test_shouldOnlyKeepHotelsInsideTheRadius():
    planner = _planner()
    hotels = [hotel.model_copy(deep=True) for hotel in _HOTELS]

    narrowed, notes = _narrow(planner, hotels, ANCHOR)

    assert FAR_HOTEL not in [hotel.name for hotel in narrowed]
    assert notes == []


def test_shouldPreferTheNearestTierEvenWhenTheScoringWouldPickAnother():
    """判据 9：跨两档时选低档那家——贴湖那家有预算惩罚，原打分本来不会选它。"""
    planner = _planner()
    hotels = [hotel.model_copy(deep=True) for hotel in _HOTELS]

    narrowed, _ = _narrow(planner, hotels, ANCHOR)

    assert [hotel.name for hotel in narrowed] == [NEAR_HOTEL]
    plan = planner.build_plan(_input(_request(hotel_anchor="西湖"), anchor=ANCHOR))
    assert _day_hotels(plan) == [NEAR_HOTEL] * 3


def test_shouldFallToNextTierWhenNearestTierIsEmpty():
    """判据 10：一档空、二档有 → 退二档，不回落成全城。"""
    planner = _planner()
    hotels = [hotel.model_copy(deep=True) for hotel in _HOTELS if hotel.name != NEAR_HOTEL]

    narrowed, notes = _narrow(planner, hotels, ANCHOR)

    assert [hotel.name for hotel in narrowed] == [MID_HOTEL]
    assert notes == []


def test_shouldKeepHotelsWithoutCoordinatesInsideTheNearestTier():
    """没有坐标的酒店不参与远近判断，跟最低档一起进打分，不因为锚点存在就被踢掉。"""
    planner = _planner()
    coordinateless = Hotel(name="无坐标民宿", type="民宿", price_range="300-400", estimated_cost=350)
    hotels = [coordinateless] + [hotel.model_copy(deep=True) for hotel in _HOTELS]

    narrowed, _ = _narrow(planner, hotels, ANCHOR)

    assert [hotel.name for hotel in narrowed] == ["无坐标民宿", NEAR_HOTEL]


def test_shouldReturnToWholeCityCandidatesWhenNothingIsInsideRadius():
    """判据 7：半径收到 0 → 圈内一家不剩，回落原候选并留一句说明，方案照常产出。"""
    planner = _planner(AnchorSettings(radius_km=0.0, near_km=0.0, mid_km=0.0))
    hotels = [hotel.model_copy(deep=True) for hotel in _HOTELS]

    narrowed, notes = _narrow(planner, hotels, ANCHOR)

    assert [hotel.name for hotel in narrowed] == [hotel.name for hotel in _HOTELS]
    assert "没有候选酒店" in notes[0]

    plan = planner.build_plan(_input(_request(hotel_anchor="西湖"), anchor=ANCHOR))
    assert len(plan.days) == 3
    assert "没有候选酒店" in " ".join(_notes(plan))


def test_shouldBehaveAsBeforeWhenNoCandidateHasCoordinates():
    """判据 6：候选全无坐标时，填了锚点也不报错，结果与不填一致。"""
    planner = _planner()
    plain = [
        Hotel(name="甲", type="经济型", price_range="200-300", estimated_cost=260),
        Hotel(name="乙", type="舒适型", price_range="300-400", estimated_cost=350),
    ]
    request = _request(hotel_anchor="西湖")
    filled_input = _input(request, anchor=ANCHOR)
    filled_input.hotel_result.hotels = [hotel.model_copy(deep=True) for hotel in plain]
    unfilled_input = _input(request)
    unfilled_input.hotel_result.hotels = [hotel.model_copy(deep=True) for hotel in plain]

    narrowed, notes = _narrow(planner, plain, ANCHOR)

    assert narrowed is plain
    assert "都没有坐标" in notes[0]
    assert _day_hotels(planner.build_plan(filled_input)) == _day_hotels(planner.build_plan(unfilled_input))


def test_shouldReuseFirstDayHotelAndSaySoForEveryLaterDay():
    """判据 3：strict 三天同名，且每天有一句明写的沿用备注。"""
    plan = _planner().build_plan(_input(_request(hotel_anchor="西湖", hotel_continuity="strict"), anchor=ANCHOR))

    assert _day_hotels(plan) == [NEAR_HOTEL] * 3
    notes = " ".join(_notes(plan))
    assert "第 2 天沿用第 1 天的酒店" in notes and "第 3 天沿用第 1 天的酒店" in notes
    assert "整趟住同一家" in notes


def test_shouldStateHowMuchFartherTheLockedHotelIs():
    """锁的这家离当天景点明显更远时，代价必须写成数字，不能只说"沿用"。"""
    planner = _planner()
    attraction = Attraction(name="西湖", address="杭州西湖景区", location=ANCHOR,
                            visit_duration=180, category="自然风景", ticket_price=60)

    note = planner._locked_hotel_note(
        _HOTELS[2], [hotel.model_copy(deep=True) for hotel in _HOTELS], [attraction], None, 1)

    assert "已锁定整趟住同一家「远郊会议中心酒店」" in note
    assert "比最近的一家远" in note


def test_shouldNotSwapHotelsOnLaterDaysEvenWhenBudgetBranchFires():
    """判据 3 补：把预算压到必然触发 reduce_budget，strict 仍不许换店——两处写点都守着。"""
    request = _request(hotel_anchor="西湖", hotel_continuity="strict", budget=900)
    plan = _planner().build_plan(_input(request, anchor=ANCHOR))

    assert len(set(_day_hotels(plan))) == 1


def test_shouldOnlyChangeTheDayThatCarriesAnAnchor():
    """判据 4：dayHotelAnchors 只影响第 2 天，其余两天不受影响。"""
    planner = _planner()
    without_anchor = planner.build_plan(_input(_request()))
    plan = planner.build_plan(_input(
        _request(day_hotel_anchors={1: "西湖"}),
    ).model_copy(update={"day_hotel_anchor_locations": {1: ANCHOR}}))

    days = _day_hotels(plan)
    baseline = _day_hotels(without_anchor)
    assert days[1] == NEAR_HOTEL
    assert days[0] == baseline[0] and days[2] == baseline[2]


def test_shouldLetStrictWinAndStateThatTheDayAnchorWasDropped():
    """判据 8：strict 与按天锚点矛盾时 strict 赢，但必须明写那句未生效，不静默吞掉。"""
    plan = _planner().build_plan(_input(
        _request(hotel_continuity="strict", day_hotel_anchors={1: "西湖"}),
    ).model_copy(update={"day_hotel_anchor_locations": {1: ANCHOR}}))

    assert len(set(_day_hotels(plan))) == 1
    assert "第 2 天指定的住宿位置「西湖」未生效" in " ".join(_notes(plan))


def test_shouldTreatBlankAnchorsAsUnsetAndRejectUnknownContinuity():
    request = _request(hotel_anchor="   ", day_hotel_anchors={0: "  ", 1: ""}, hotel_continuity="  STRICT ")
    assert request.hotel_anchor is None
    assert request.day_hotel_anchors is None
    assert request.hotel_continuity == "strict"

    try:
        _request(hotel_continuity="as-usual")
    except ValueError as error:
        assert "strict" in str(error)
    else:
        raise AssertionError("非法的住宿连续性取值应被校验挡掉")


def test_shouldCarryAnchorsAcrossAdjustCopy():
    """改一次行程不能把上次填的住宿位置弄丢。"""
    request = _request(hotel_anchor="西湖", day_hotel_anchors={1: "广州塔"}, hotel_continuity="strict")

    copied = RequestOptimizationStrategyService().copy_request(request)

    assert copied.hotel_anchor == "西湖"
    assert copied.day_hotel_anchors == {1: "广州塔"}
    assert copied.hotel_continuity == "strict"


def test_shouldCarryAnchorsAcrossLoopRoundCopy():
    """闭环每轮都自己拷一份请求，这一处漏掉的话锚点在第一轮就没了（真实踩过）。"""
    from travel_assistant.agent.loop_planning_agent import LoopPlanningAgent

    request = _request(hotel_anchor="西湖", day_hotel_anchors={2: "广州塔"}, hotel_continuity="strict")

    copied = LoopPlanningAgent._copy_request(object.__new__(LoopPlanningAgent), request)

    assert copied.hotel_anchor == "西湖"
    assert copied.day_hotel_anchors == {2: "广州塔"}
    assert copied.hotel_continuity == "strict"


def test_shouldGiveTheNearbyHotelsOnlyToTheDayThatAsked():
    """周边检索补来的酒店只进"那天"的池子：并回全局会把另外两天一起改掉（端到端实测踩到）。"""
    from travel_assistant.models.hotel import Hotel as _Hotel

    class _NearbyClient:
        def search_near_location(self, query, location, radius_km):
            return [_Hotel(name="湖边客栈", type="客栈", price_range="300-400", estimated_cost=320,
                           location=location)]

    service = HotelAnchorService(AmapSettings(api_key=""), _NearbyClient(), AnchorSettings())
    base = [Hotel(name="城里酒店", type="经济型", price_range="200-300", estimated_cost=260, location=ANCHOR)]
    request = _request(day_hotel_anchors={2: "西湖"})
    anchors = HotelAnchors(by_day={2: ANCHOR})

    pools = service.build_day_candidates(request, anchors, base)

    assert sorted(pools) == [2]
    assert [hotel.name for hotel in pools[2]] == ["城里酒店", "湖边客栈"]

    whole_trip = service.build_day_candidates(
        _request(hotel_anchor="西湖"), HotelAnchors(whole_trip=ANCHOR), base)
    assert sorted(whole_trip) == [0, 1, 2]


def test_shouldReportUnresolvedPlaceNamesWithoutTouchingTheNetwork():
    """判据 5：地名搜不到（这里用未配 key 模拟）→ 按未填写处理，不抛错、不发第二趟检索。"""
    service = HotelAnchorService(AmapSettings(api_key=""), _amap_hotel_client(), AnchorSettings())
    request = _request(hotel_anchor="不存在的地点xyz", day_hotel_anchors={1: "另一个地名"})

    anchors = service.resolve(request)
    pools = service.build_day_candidates(request, anchors, [])

    assert anchors.is_empty()
    assert sorted(anchors.unresolved) == sorted(["不存在的地点xyz", "另一个地名"])
    assert pools == {}


def _amap_hotel_client():
    from travel_assistant.clients.amap_hotel import AmapHotelClient
    from travel_assistant.clients.rule_hotel import RuleBasedHotelClient
    return AmapHotelClient(AmapSettings(api_key=""), RuleBasedHotelClient())
