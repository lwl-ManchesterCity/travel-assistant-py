"""方案 A 的七个追问工具。

分成三类，边界不一样：

- 读方案的三个（`get_plan_overview` / `get_day_plan` / `get_attraction_detail`）可以带定位参数，
  但参数只用来在**已生成的方案内部**找东西，找不到就返回"方案里没有"，绝不外发请求；
- 联网的三个里，`query_weather` / `recommend_hotels` 零参数：城市、日期、预算、住宿偏好
  一律从上下文里的原始请求取——这几个值让模型自己填就会被编造，
  与 agent/data_tools.py 里那三个取数工具是同一条约定。
  `search_attractions` **有意破例多一格 `attraction_name`**（见下一条同一条理由）：
  城市、偏好那些编不得，而"问的是哪个景点"是模型才知道、从上下文取不出来的判断。
  **不填时行为一字不变**，照旧按原始请求做发现式检索；
- 查本地库的一个（`search_attractions_by_tag`）**有意破例让模型填参数**：`tags`、`attraction_name`
  与 `city`，因为"筛哪个词""问的是哪个景点""问的是哪个城市"正是要模型做的判断，从上下文取不出来。
  `city` 不填就退回方案城市（老行为一字不变）——**它是"查别的城市的景点"那条路的口子**，
  后三个工具的城市仍然锁死在本次行程上，那不是限制，是它们的定义。
  护栏不是参数来源，是**词表**——`tags` 只能从 data/tags.json 那 10 个词里挑，
  挑不出来就别筛。词表会变，所以写进工具说明时是现读的，不是抄一份常量。

每次调用都往 AskContext 记一条 AgentTrace，成功失败都记。
"""

from langchain_core.tools import BaseTool, tool

from ..clients.amap_opentime import ALL_DAY
from ..kb import (
    KB_CITIES,
    IndexNotBuilt,
    describe_vocabulary,
    indexed_cities,
    search_attractions as search_kb_attractions,
    vocabulary_words,
)
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
        f"  - {attraction.name}｜{attraction.category or '未分类'}｜门票 {attraction.ticket_price} 元（按类别估算）"
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


def _attraction_hours_text(attraction) -> str:
    """按名字联网查到的那**一条**：名称｜类别｜地址｜开放｜闭馆。

    只在按名字查那条路上打——发现式检索一次 10 条，每条挂开放时间是噪声。

    **不打票价**：这条路回来的 `ticket_price` 是 `_infer_ticket_price` 按类别估的，
    不是高德的真实门票（高德的 `cost` 混着餐饮人均与住宅房价，见 docs/ASK_LIVE_HOURS.md §五）。
    写进给模型看的文本就会被当成事实转述出去。
    """
    closed = _join([str(day) for day in (attraction.closed_days or [])])
    # 「全天开放」本身就是一句完整的话，别再拼成「开放 全天开放」。
    opening = attraction.hours if attraction.hours == ALL_DAY else f"开放 {attraction.hours or '未给出'}"
    return (
        f"{attraction.name}｜{attraction.category or '未分类'}｜{attraction.address or '地址未给出'}"
        f"｜{opening}"
        f"｜闭馆 {closed or '无'}"
    )


def _kb_list_text(value, empty: str = "未收录") -> str:
    """本地库里的数组字段过了一遍 Chroma：非空存数组，空的存空串。

    所以取出来可能是 list、也可能是 ""，两种都要接得住。
    """
    if isinstance(value, list):
        return "、".join(str(item) for item in value) or empty
    return str(value) if value else empty


def _kb_price_text(price) -> str:
    """本地库的门票口径**与方案里那套不一样**，不能混着说。

    这里是 KB 自己查证的：`0` = 确定免费，`null` = 未知（KB 设计 4.1）。
    方案里的「按类别估算」是另一回事，所以这里**不写「估算」**，写了反而是错的。
    """
    if price is None:
        return "门票未知"
    if price == 0:
        return "免费"
    return f"门票 {price} 元"


def _kb_attractions_text(rows: list[dict], city: str, tags: list[str]) -> str:
    condition = f"筛「{'、'.join(tags)}」" if tags else "没加筛选条件"
    # city 现在可能是空串（原始请求里没城市、模型也没指定），那就别提城市，
    # 否则会拼出一句"本地景点库（…）"，读起来像少了半句。
    scope = f"{city}本地景点库" if city else "本地景点库"
    if not rows:
        return f"{scope}（{condition}）没有命中的景点"
    lines = [f"{scope}（{condition}）命中 {len(rows)} 条："]
    for row in rows:
        lines.append(
            f"{row.get('name')}"
            f"｜标签 {_kb_list_text(row.get('tags'), '无')}"
            f"｜{row.get('category') or '未分类'}"
            f"｜{_kb_price_text(row.get('ticket_price'))}"
            f"｜开放 {_kb_list_text(row.get('hours'))}"
            f"｜闭馆 {_kb_list_text(row.get('closed_days'))}"
            f"｜{(row.get('description') or '无简介').strip()}"
        )
    return "\n".join(lines)


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
        """按名字取**本方案里**某个景点的明细：类别、地址、门票价、建议停留时长、有无坐标。
        名字只要包含即可匹配；方案里没有这个名字时会回可选景点清单。
        **它只读本方案**——要查方案外的景点（门票、开放时间、闭馆日、要不要预约），
        改用 search_attractions_by_tag。"""
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
            f"{attraction.name}｜{attraction.category or '未分类'}｜门票 {attraction.ticket_price} 元（按类别估算）"
            f"｜建议停留 {attraction.visit_duration} 分钟｜{attraction.address or '无地址'}"
            f"｜坐标{'有' if attraction.location else '无'}"
            f"｜{(attraction.description or '').strip() or '无简介'}"
            for attraction in matches
        )
        context.record("get_attraction_detail", "读景点明细", f"attraction_name={attraction_name}", text)
        return text

    @tool
    def search_attractions(attraction_name: str = "", city: str = "") -> str:
        """联网检索景点，两种用法。
        ① 不填景点名：按原始请求的城市与偏好检索**一批**候选景点（含名称、类别、地址）。
        ② 填了景点名：只查**这一个**景点，返回它的地址与**开放时间、闭馆日**——
        问「某个景点几点关门」而本地库没给到这一格（或这个城市不收录）时走这条。
        名字填「越秀公园」这样的景点名，不要连问句一起填。
        city 只在填了景点名、且问的是**别的城市**时才填（填「成都」）；不填就是本次行程的城市。
        不填景点名时 city 不起作用。"""
        context = current_ask_context()
        plan_city = context.request.city or ""
        named = (attraction_name or "").strip()
        if named:
            # 模型没指定就用方案城市（老行为一字不变）；指定了就用它——
            # 与 search_attractions_by_tag 那条同一条口径：问别的城市要填 city。
            # 少了这一格的话，方案在上海、问成都的景点时，第二跳会拿**上海**去查高德，
            # 返回一条上海的"最接近"，用户问成都却答出一个上海景点。
            city = (city or "").strip() or plan_city
            summary = f"城市={city}，景点名={named}"
            try:
                attraction = attraction_agent.find_by_name(city, named)
            except Exception as exception:  # noqa: BLE001 - 联网失败要留在轨迹里，让模型改用方案内数据作答
                text = f"联网查「{named}」失败：{exception}"
                context.record("search_attractions", "按名字联网查景点", summary, text, status="failed")
                return text
            if attraction is None:
                # 与本地库那条路的措辞要能区分开，否则模型分不清是库没有还是联网也没有。
                text = f"联网也没查到「{named}」这个景点"
                context.record("search_attractions", "按名字联网查景点", summary, text, status="not_found")
                return text
            text = _attraction_hours_text(attraction)
            if (attraction.name or "").strip() != named:
                # 高德在没有同名时返回"最接近的那个"（amap_attraction.find_attraction_by_name
                # 里 `exact = [...] or candidates`），问「广州塔旋转餐厅」可能返回「广州塔」。
                # **不做硬过滤**：`用户原词 in POI名` 会把「陈家祠」vs「陈家祠堂」这类简称误杀。
                # 把判断交给模型，与规则 12（返回了不等于对得上，自己要核）同一条思路。
                text = f"高德只搜到名字最接近的「{attraction.name}」，不确定是不是你要找的。\n{text}"
            context.record("search_attractions", "按名字联网查景点", summary, text)
            return text
        # 发现式那条路不看 city：它取的就是本次行程的城市，行为一字未变。
        summary = f"城市={plan_city}，偏好={'、'.join(context.request.preferences or [])}"
        try:
            result = attraction_agent.search(context.request)
        except Exception as exception:  # noqa: BLE001 - 联网失败要留在轨迹里，让模型改用方案内数据作答
            context.record("search_attractions", "检索候选景点", summary,
                           f"景点检索失败：{exception}", status="failed")
            return f"景点检索失败：{exception}"
        text = _attractions_text(result)
        context.record("search_attractions", "检索候选景点", summary, text)
        return text

    # 说明是现拼的：f-string 当不了 docstring（CPython 只把常量字符串存进 __doc__，
    # JoinedStr 不行，__doc__ 会是 None），所以走 @tool(description=...)。
    # 词表现读不抄常量——抄一份就会在改词表时变成一句过期的话。
    @tool(description=(
        "查**本地景点资料库**。两种用法都走它："
        "① 按标签筛出一批候选景点（「下雨天去哪」「有什么免费的」「哪里适合带孩子」）；"
        "② 查**某一个具体景点**的门票、开放时间、闭馆日、要不要预约。"
        "**② 不限于本方案里的景点**——方案里没有的照样能查（「广州塔门票多少钱」走这里），"
        "这时**把景点名填进 attraction_name**（填「广州塔」，不是整句问话），不用传 tags。"
        "名字是这条路上最确定的条件：整句送去检索会被问句里多出来的字稀释，"
        "相似度掉到线下就查不到了。\n"
        "本地库与联网检索是两套数据，别混着说。\n"
        f"本地库目前收录{'、'.join(KB_CITIES)}的景点。\n"
        "**查哪个城市由 city 参数定**：不填就是本次行程的城市（大多数提问都是这样）；"
        "用户问的是**别的城市**的景点（「广州塔门票多少」），把城市名填进 city（填「广州」）。"
        "只有问景点时才填——问天气、酒店、候选景点不填。\n"
        f"tags 只能从这几个词里挑，一个都不许自创：{describe_vocabulary()}。"
        "挑不出来就别传 tags，那样会走纯向量。\n"
        "**不许硬凑**：命中的景点里如果没有一条真的对得上用户问的类别"
        "（比如用户问爬山，返回的却全是溶洞、博物馆），就照实说库里没有，"
        "不要把沾边的说成是。"
    ))
    def search_attractions_by_tag(tags: list[str] | None = None,
                                  attraction_name: str = "",
                                  city: str = "") -> str:
        """查本地景点资料库。"""
        context = current_ask_context()
        # 模型没指定就用方案城市——这是改动前的老行为，一个字没变。
        # 指定了就用它，这是"问别的城市"那条路（见 docs/ASK_SCOPE_OPENING.md §2.1）。
        city = (city or "").strip() or (context.request.city or "")
        picked = [str(tag).strip() for tag in (tags or []) if tag and str(tag).strip()]
        # 问「某一个具体景点」那条路上，模型会把名字填进来，检索词就用它——
        # 整句去撞阈值会被问句里多出来的字稀释（陈家祠堂 0.585 → 「陈家祠堂 周一闭馆吗」<0.545）。
        # 没填就退回整句：最坏情况等于没改，不会更差。
        named = (attraction_name or "").strip()
        summary = f"城市={city}"
        if named:
            summary += f"，景点名={named}"
        summary += f"，标签={'、'.join(picked) or '无（纯向量）'}"

        def missed(text: str, status: str = "not_found") -> str:
            context.record("search_attractions_by_tag", "查本地景点库", summary, text, status=status)
            return text

        # 以索引里**实际**有什么城市为准，不以常量 KB_CITIES 为准——常量只是写在说明里给模型看的。
        try:
            cities = indexed_cities()
        except IndexNotBuilt as exception:
            return missed(f"本地景点资料库还没建好（{exception}），这次查不了，"
                          "请只用方案内已有的信息作答", "failed")

        if city and city not in cities:
            # 「库没收录」≠「答案不存在」：**只报本地库这一层的结果**，并指出还有下一跳。
            # 末句若只说"本地库答不了"，模型会把它读成最终结论、不再走联网那次
            # （见 docs/ASK_LIVE_HOURS.md §3.7.1）。下一跳走不走由规则 11 定，
            # 不在工具里替模型下结论。
            return missed(f"本地景点资料库目前只有{'、'.join(cities)}，没有{city}的景点，"
                          "本地库这边答不了——可以再用 search_attractions 联网查一次")

        unknown = [tag for tag in picked if tag not in vocabulary_words()]
        if unknown:
            return missed(f"「{'、'.join(unknown)}」不在可选词表里，不能拿它筛。"
                          f"可选的是：{describe_vocabulary()}", "failed")

        try:
            rows = search_kb_attractions(
                city=city or None, tags=picked or None,
                query=named or context.question,
            )
        except Exception as exception:  # noqa: BLE001 - 检索失败要留在轨迹里，让模型改用方案内数据作答
            return missed(f"本地景点资料库检索失败：{exception}", "failed")

        text = _kb_attractions_text(rows, city, picked)
        context.record("search_attractions_by_tag", "查本地景点库", summary, text)
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
        search_attractions_by_tag,
        query_weather,
        recommend_hotels,
    ]
