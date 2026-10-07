"""方案 A 的六个追问工具。

分成两类，边界不一样：

- 读方案的三个（`get_plan_overview` / `get_day_plan` / `get_attraction_detail`）可以带定位参数，
  但参数只用来在**已生成的方案内部**找东西，找不到就返回"方案里没有"，绝不外发请求；
- 联网的三个（`search_attractions` / `query_weather` / `recommend_hotels`）零参数，
  城市、日期、预算、住宿偏好一律从上下文里的原始请求取——这几个值让模型自己填就会被编造，
  与 agent/data_tools.py 里那三个取数工具是同一条约定。

每次调用都往 AskContext 记一条 AgentTrace，成功失败都记。
"""

from langchain_core.tools import BaseTool, tool

from ..models.trip_plan import TripPlan
from .ask_context import current_ask_context
from .attraction_agent import AttractionAgent
from .hotel_agent import HotelAgent
from .weather_agent import WeatherAgent

_LIST_LIMIT = 10


def _join(values: list[str], separator: str = "，") -> str:
    return separator.join(value for value in values if value)


def _weather_of(trip_plan: TripPlan, day_date: str | None) -> str:
    for weather in trip_plan.weather_info or []:
        if weather.date and weather.date == day_date:
            return f"{weather.day_weather} {weather.day_temp}℃/{weather.night_temp}℃"
    return "无天气数据"


def _overview_text(trip_plan: TripPlan) -> str:
    budget = trip_plan.budget
    days = trip_plan.days or []
    head = f"{trip_plan.city} {trip_plan.start_date} 至 {trip_plan.end_date}，共 {len(days)} 天"
    if budget is not None:
        head += f"；总花费 {budget.total} 元，预算 {budget.requested_budget} 元，结余 {budget.remaining_budget} 元，状态 {budget.status}"
    lines = [head]
    for index, day in enumerate(days):
        attractions = day.attractions or []
        route = day.route_plan
        lines.append(
            f"第{index + 1}天（{day.date}）：{len(attractions)} 个景点"
            f"（{'、'.join(attraction.name or '' for attraction in attractions) or '无'}），"
            f"{(route.total_duration_minutes if route else 0)} 分钟通勤/"
            f"{round(route.total_distance_km, 1) if route else 0} 公里，"
            f"预估 {day.estimated_cost} 元，天气 {_weather_of(trip_plan, day.date)}"
        )
    return "\n".join(lines)


def _day_text(trip_plan: TripPlan, index: int) -> str:
    day = (trip_plan.days or [])[index]
    attractions = "\n".join(
        f"  - {attraction.name}｜{attraction.category or '未分类'}｜门票 {attraction.ticket_price} 元"
        f"｜建议停留 {attraction.visit_duration} 分钟｜{attraction.address or '无地址'}"
        for attraction in (day.attractions or [])
    ) or "  - 无"
    meals = "\n".join(
        f"  - {meal.type or ''} {meal.name}｜预估 {meal.estimated_cost} 元"
        for meal in (day.meals or [])
    ) or "  - 无"
    route = day.route_plan
    hotel = day.hotel
    return _join([
        f"第{index + 1}天（{day.date}）",
        f"交通方式：{day.transportation or '未指定'}",
        f"住宿：{hotel.name if hotel else '无'}｜{(hotel.price_range if hotel else '') or '价位未给出'}"
        f"｜预估 {(hotel.estimated_cost if hotel else 0)} 元",
        f"景点：\n{attractions}",
        f"用餐：\n{meals}",
        f"路线：{(route.summary if route else None) or '无路线数据'}",
        f"当天预估花费：{day.estimated_cost} 元",
        f"天气：{_weather_of(trip_plan, day.date)}",
        f"优化备注：{'；'.join(day.optimization_notes or []) or '无'}",
    ], separator="\n")


def _attractions_text(result) -> str:
    names = "、".join(
        f"{attraction.name}（{attraction.category or '未分类'}，{attraction.address or '地址未给出'}）"
        for attraction in (result.attractions or [])[:_LIST_LIMIT]
    )
    return f"{result.summary}\n候选：{names or '无'}"


def _hotels_text(result) -> str:
    names = "、".join(
        f"{hotel.name}（{hotel.type or '类型未给出'}，{hotel.price_range or '价位未给出'}）"
        for hotel in (result.hotels or [])[:_LIST_LIMIT]
    )
    return f"{result.summary}\n候选：{names or '无'}"


def _weather_text(result) -> str:
    lines = [result.summary]
    for weather in (result.weather_info or [])[:_LIST_LIMIT]:
        lines.append(
            f"{weather.date}：白天 {weather.day_weather} {weather.day_temp}℃，"
            f"夜间 {weather.night_weather} {weather.night_temp}℃，"
            f"{weather.wind_direction or ''}{weather.wind_power or ''}，来源 {weather.source}"
        )
    return "\n".join(lines)


def build_ask_tools(attraction_agent: AttractionAgent,
                    weather_agent: WeatherAgent,
                    hotel_agent: HotelAgent) -> list[BaseTool]:

    @tool
    def get_plan_overview() -> str:
        """总览这份方案：城市、日期、天数、预算与结余，外加每天一行的概要（景点名、通勤、花费、天气）。
        问整体花费、哪天最赶、几天分别去了哪一类问题先调它。无需参数。"""
        context = current_ask_context()
        text = _overview_text(context.trip_plan)
        context.record("get_plan_overview", "读方案总览", "整份方案", text)
        return text

    @tool
    def get_day_plan(day_index: int = -1) -> str:
        """取某一天的完整安排：景点（含票价与停留时长）、用餐、住宿、路线摘要、当天花费与天气。
        day_index 从 0 开始数（0 表示第 1 天）；不传就按顺序给下一次。方案里没有这一天时会返回天数。"""
        context = current_ask_context()
        days = context.trip_plan.days or []
        if not days:
            context.record("get_day_plan", "读某天安排", f"day_index={day_index}",
                           "这份方案里没有每日安排", status="not_found")
            return "这份方案里没有每日安排"
        index = day_index
        if index < 0:
            index = context.next_day_index(len(days))
        if index < 0 or index >= len(days):
            miss = f"方案里没有第 {index + 1} 天，共 {len(days)} 天，day_index 可取 0 到 {len(days) - 1}"
            context.record("get_day_plan", "读某天安排", f"day_index={day_index}", miss, status="not_found")
            return miss
        text = _day_text(context.trip_plan, index)
        context.record("get_day_plan", "读某天安排", f"day_index={index}", text)
        return text

    @tool
    def get_attraction_detail(attraction_name: str = "") -> str:
        """按名字取方案里某个景点的明细：类别、地址、门票价、建议停留时长、有无坐标。
        名字只要包含即可匹配；方案里没有这个名字时会回可选景点清单，不会去联网查。"""
        context = current_ask_context()
        planned = [
            attraction
            for day in (context.trip_plan.days or [])
            for attraction in (day.attractions or [])
        ]
        needle = (attraction_name or "").replace(" ", "").strip()
        matches = [
            attraction for attraction in planned
            if needle and needle in (attraction.name or "").replace(" ", "")
        ] or ([planned[0]] if needle == "" and planned else [])
        if not matches:
            names = "、".join(attraction.name or "" for attraction in planned) or "无"
            miss = f"方案里没有叫「{attraction_name}」的景点，方案内的景点：{names}"
            context.record("get_attraction_detail", "读景点明细", f"attraction_name={attraction_name}",
                           miss, status="not_found")
            return miss
        text = "\n".join(
            f"{attraction.name}｜{attraction.category or '未分类'}｜门票 {attraction.ticket_price} 元"
            f"｜建议停留 {attraction.visit_duration} 分钟｜{attraction.address or '无地址'}"
            f"｜坐标{'有' if attraction.location else '无'}"
            f"｜{(attraction.description or '').strip() or '无简介'}"
            for attraction in matches
        )
        context.record("get_attraction_detail", "读景点明细", f"attraction_name={attraction_name}", text)
        return text

    @tool
    def search_attractions() -> str:
        """按原始请求的城市与偏好重新检索候选景点（含名称、类别、地址）。
        只在方案里查不到需要的信息时才用；无需参数，查询条件取自本次追问携带的原始请求。"""
        context = current_ask_context()
        summary = f"城市={context.request.city}，偏好={'、'.join(context.request.preferences or [])}"
        try:
            result = attraction_agent.search(context.request)
        except Exception as exception:  # noqa: BLE001 - 联网失败要留在轨迹里，让模型改用方案内数据作答
            context.record("search_attractions", "检索候选景点", summary,
                           f"景点检索失败：{exception}", status="failed")
            return f"景点检索失败：{exception}"
        text = _attractions_text(result)
        context.record("search_attractions", "检索候选景点", summary, text)
        return text

    @tool
    def query_weather() -> str:
        """按原始请求的城市与日期区间重新查询出行天气（逐日：白天/夜间天气、气温、风）。
        只在方案里的天气不够用时才用；无需参数。"""
        context = current_ask_context()
        summary = f"城市={context.request.city}，日期={context.request.start_date} 至 {context.request.end_date}"
        try:
            result = weather_agent.query(context.request)
        except Exception as exception:  # noqa: BLE001
            context.record("query_weather", "查询出行天气", summary,
                           f"天气查询失败：{exception}", status="failed")
            return f"天气查询失败：{exception}"
        text = _weather_text(result)
        context.record("query_weather", "查询出行天气", summary, text)
        return text

    @tool
    def recommend_hotels() -> str:
        """按原始请求的城市、预算与住宿偏好重新检索候选酒店（含名称、类型、价位）。
        只在方案里的酒店不满足问题时才用；无需参数。"""
        context = current_ask_context()
        summary = f"城市={context.request.city}，住宿偏好={context.request.accommodation}，预算={context.request.budget}"
        try:
            result = hotel_agent.recommend(context.request)
        except Exception as exception:  # noqa: BLE001
            context.record("recommend_hotels", "推荐候选酒店", summary,
                           f"酒店推荐失败：{exception}", status="failed")
            return f"酒店推荐失败：{exception}"
        text = _hotels_text(result)
        context.record("recommend_hotels", "推荐候选酒店", summary, text)
        return text

    return [
        get_plan_overview,
        get_day_plan,
        get_attraction_detail,
        search_attractions,
        query_weather,
        recommend_hotels,
    ]
