"""对应 Java agent/ReflectionAgent.reflectTripPlan 的行为断言。

Java 侧没有 ReflectionAgentTest，这里的期望值全部按源码公式手算：
expectedRouteLimit = 天数 * 180、acceptable 阈值 overallScore <= 15、
clampScore = max(0, min(100, round(...)))，以及 recommendTripAction 的分支优先级。
"""

from travel_assistant.agent.reflection_agent import ReflectionAgent, TripPlanRequestView
from travel_assistant.evaluator.budget_evaluator import BudgetEvaluator
from travel_assistant.evaluator.daily_plan_evaluator import DailyPlanEvaluator
from travel_assistant.evaluator.route_evaluator import RouteEvaluator
from travel_assistant.evaluator.weather_evaluator import WeatherEvaluator
from travel_assistant.models.budget import Budget
from travel_assistant.models.day_plan import DayPlan
from travel_assistant.models.route_plan import RoutePlan
from travel_assistant.models.trip_plan import TripPlan
from travel_assistant.models.weather_info import WeatherInfo

_REFLECTION_AGENT = ReflectionAgent(DailyPlanEvaluator(BudgetEvaluator(), RouteEvaluator(), WeatherEvaluator()))


def _trip_plan(route_minutes_per_day: list[int],
               day_weather: list[str],
               budget: Budget | None) -> TripPlan:
    return TripPlan(
        city="杭州",
        start_date="2026-07-01",
        end_date="2026-07-03",
        days=[
            DayPlan(date=f"2026-07-0{index + 1}", day_index=index,
                    route_plan=RoutePlan(total_duration_minutes=minutes))
            for index, minutes in enumerate(route_minutes_per_day)
        ],
        weather_info=[WeatherInfo(date=f"2026-07-0{index + 1}", day_weather=weather)
                      for index, weather in enumerate(day_weather)],
        budget=budget,
    )


def test_shouldStopWhenPlanIsWithinAllThresholds():
    trip_plan = _trip_plan(
        [60, 60, 60],
        ["晴", "多云", "晴"],
        Budget(total=2976, requested_budget=3000, remaining_budget=24, within_budget=True, status="within_budget"),
    )

    reflection = _REFLECTION_AGENT.reflect_trip_plan(trip_plan, TripPlanRequestView(3000))

    assert reflection.acceptable is True
    assert reflection.overall_score == 0
    assert reflection.recommended_action == "STOP"
    assert reflection.total_route_overflow_minutes == 0
    assert reflection.reason == "当前方案整体评分为 0，已处于可接受范围，无需继续重规划"
    assert reflection.reflection_notes == [
        "当前评分=0（预算=0，路线=0，天气=0，体验=0）",
        "整份方案在预算、路线、天气上都处于可接受范围",
    ]


def test_shouldRecommendReduceBudgetWhenTotalCostOverflows():
    trip_plan = _trip_plan(
        [60, 60, 60],
        ["晴", "晴", "晴"],
        Budget(total=3600, requested_budget=3000, remaining_budget=-600, within_budget=False, status="over_budget"),
    )

    reflection = _REFLECTION_AGENT.reflect_trip_plan(trip_plan, TripPlanRequestView(3000))

    # budgetSeverity = round(20 + 600/3000 * 100) = 40，其余严重度为 0，overallScore 40 > 15
    assert reflection.over_budget is True
    assert reflection.total_budget_overflow_amount == 600
    assert reflection.budget_severity == 40
    assert reflection.overall_score == 40
    assert reflection.acceptable is False
    assert reflection.recommended_action == "REDUCE_BUDGET"
    assert reflection.reason == "当前方案整体评分为 40，其中预算问题最突出，超支 600 元，建议优先压缩预算"
    assert reflection.reflection_notes[0] == "总预算超支 600 元"
    assert reflection.reflection_notes[1] == "当前评分=40（预算=40，路线=0，天气=0，体验=0）"


def test_shouldRecommendCompressRouteWhenTotalCommuteOverflows():
    trip_plan = _trip_plan(
        [300, 300],
        ["晴", "晴"],
        Budget(total=2400, requested_budget=3000, remaining_budget=600, within_budget=True, status="within_budget"),
    )

    reflection = _REFLECTION_AGENT.reflect_trip_plan(trip_plan, TripPlanRequestView(3000))

    # 期望阈值 2 * 180 = 360，实际 600，超出 240；routeSeverity = round(15 + 240/360 * 120) = 95
    assert reflection.route_too_long is True
    assert reflection.total_route_overflow_minutes == 240
    assert reflection.route_severity == 95
    assert reflection.overall_score == 95
    assert reflection.recommended_action == "COMPRESS_ROUTE"
    assert reflection.reason == (
        "当前方案整体评分为 95，其中路线问题最突出，通勤超出建议阈值 240 分钟，建议优先压缩路线"
    )
    assert "总通勤超出建议阈值约 240 分钟" in reflection.reflection_notes


def test_shouldRecommendIndoorReplacementWhenWeatherIsRisky():
    trip_plan = _trip_plan(
        [60, 60, 60],
        ["中雨", "雷阵雨", "晴"],
        Budget(total=2900, requested_budget=3000, remaining_budget=100, within_budget=True, status="within_budget"),
    )

    reflection = _REFLECTION_AGENT.reflect_trip_plan(trip_plan, TripPlanRequestView(3000))

    # 2 个风险天 * 25 = 50，低于该阈值时天气严重度独占最高
    assert reflection.indoor_adjustment_recommended is True
    assert reflection.weather_severity == 50
    assert reflection.overall_score == 50
    assert reflection.recommended_action == "REPLACE_OUTDOOR_WITH_INDOOR"
    assert reflection.reason == "当前方案整体评分为 50，天气存在风险，建议增加室内备选方案"
    assert "出行期存在雨雪雷天气，建议增加室内备选" in reflection.reflection_notes

    # 雨雪雷之外不触发室内建议
    sunny = _trip_plan([60, 60, 60], ["晴", "多云", "阴"],
                       Budget(total=2900, requested_budget=3000, remaining_budget=100, within_budget=True))
    assert sunny.weather_info is not None
    assert _REFLECTION_AGENT.reflect_trip_plan(sunny, TripPlanRequestView(3000)).indoor_adjustment_recommended is False


def test_shouldRecommendUpgradeExperienceWhenLargeBudgetStaysUnused():
    trip_plan = _trip_plan(
        [60, 60, 60],
        ["晴", "晴", "晴"],
        Budget(total=5000, requested_budget=9000, remaining_budget=4000, within_budget=True, status="within_budget"),
    )

    reflection = _REFLECTION_AGENT.reflect_trip_plan(trip_plan, TripPlanRequestView(9000))

    # 阈值 max(1800, 9000/3) = 3000，结余 4000 超出 1000；experienceSeverity = round(10 + 1000/9000 * 80) = 19
    assert reflection.experience_severity == 19
    assert reflection.overall_score == 19
    assert reflection.acceptable is False
    assert reflection.recommended_action == "UPGRADE_EXPERIENCE"
    assert reflection.reason == "当前方案整体评分为 19，预算利用偏保守，可适度提升住宿或体验强度"
    assert "预算结余较多，当前方案体验利用偏保守" in reflection.reflection_notes


def test_shouldClampSeverityAtOneHundred():
    trip_plan = _trip_plan(
        [60, 60, 60],
        ["晴", "晴", "晴"],
        Budget(total=9000, requested_budget=3000, remaining_budget=-6000, within_budget=False, status="over_budget"),
    )

    reflection = _REFLECTION_AGENT.reflect_trip_plan(trip_plan, TripPlanRequestView(3000))

    # 20 + 6000/3000 * 100 = 220，clampScore 压回 100
    assert reflection.budget_severity == 100
    assert reflection.overall_score == 100


def test_reflectDailyPlanShouldDelegateToDailyPlanEvaluator():
    reflection = _REFLECTION_AGENT.reflect_daily_plan(
        estimated_cost=400,
        daily_budget_limit=1000,
        route_plan=RoutePlan(total_duration_minutes=30),
        constraints=None,
        current_weather=WeatherInfo(date="2026-07-01", day_weather="晴"),
    )

    assert reflection.recommended_action == "accept"
    assert reflection.reason == "预算和路线均处于可接受范围"
    assert reflection.reflection_notes == ["ReflectionAgent建议：接受当前方案"]
