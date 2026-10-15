"""组装 PlannerInput 并产出 planningNotes / agentTraces。"""

from ..agent.data_tools import raise_planning_errors
from ..agent.orchestration import PlanningDataAgent
from ..agent.planner_agent import estimate_trip_spend
from ..agent.planning_context import PlanningContext
from ..exceptions import TripPlanningException
from ..models.agent_trace import AgentTrace
from ..models.attraction import Attraction
from ..models.attraction_search_result import AttractionSearchResult
from ..models.hotel import Hotel
from ..models.hotel_search_result import HotelSearchResult
from ..models.intercity_plan import InterCityPlan
from ..models.planner_input import PlannerInput
from ..models.planning_constraints import PlanningConstraints
from ..models.trip_plan_request import TripPlanRequest
from ..clients.preference_store import SOURCE_FORM
from ..models.weather_query_result import WeatherQueryResult
from .hotel_anchor_service import HotelAnchorService
from .preference_service import render_extracted
from .requirement_analysis_service import RequirementAnalysisService


class PlannerInputBuilder:

    def __init__(self,
                 planning_data_agent: PlanningDataAgent,
                 requirement_analysis_service: RequirementAnalysisService,
                 hotel_anchor_service: HotelAnchorService,
                 attraction_client=None,
                 intercity_service=None,
                 preference_service=None,
                 episode_service=None):
        self._planning_data_agent = planning_data_agent
        self._requirement_analysis_service = requirement_analysis_service
        self._hotel_anchor_service = hotel_anchor_service
        self._attraction_client = attraction_client
        self._intercity_service = intercity_service
        # 不传就整条链不碰长期记忆（老的单测构造它时不带这两个参数）。
        self._preference_service = preference_service
        self._episode_service = episode_service

    def build(self, request: TripPlanRequest, user_id: int | None = None) -> PlannerInput:
        """组装一次规划的全部输入。

        `user_id` 是登录门禁认出来的人。**长期偏好只在这里进进出出**（§2.2.4）：
        读出来注入需求理解、把抽取结果存回去，两个动作都在这一处，因为
        规划与改方案两条路都经过这里（adjust_trip 最后也是 plan_trip）。
        """
        context = PlanningContext(request=request)
        self._planning_data_agent.collect(context)
        raise_planning_errors(context)

        attraction_result = context.attraction_result
        weather_result = context.weather_result
        hotel_result = context.hotel_result
        long_term = self._long_term_preferences(user_id)
        long_term_companions = self._long_term_companions(user_id)
        # 场景**按这一趟的城市**取（§2.4.3）：拿的是 request.city，与下面检索景点用的是同一个。
        long_term_episodes = self._long_term_episodes(user_id, request.city)
        constraints = self._requirement_analysis_service.analyze(
            request, long_term, long_term_episodes, long_term_companions)
        self._record_preferences(constraints, user_id)
        self._record_episodes(constraints, user_id)
        self._validate_planning_inputs(attraction_result, weather_result, hotel_result)
        planning_notes = self._build_planning_notes(
            request, attraction_result, weather_result, hotel_result, constraints,
            long_term, long_term_episodes, long_term_companions)
        agent_traces = self._build_agent_traces(request, attraction_result, weather_result, hotel_result, constraints)
        preference_trace = self._build_preference_trace(long_term, long_term_companions, constraints)
        if preference_trace is not None:
            agent_traces.append(preference_trace)
        episode_trace = self._build_episode_trace(request, user_id, long_term_episodes, constraints)
        if episode_trace is not None:
            agent_traces.append(episode_trace)
        anchors = self._hotel_anchor_service.resolve(request)
        day_candidates = self._hotel_anchor_service.build_day_candidates(
            request, anchors, list(hotel_result.hotels or []))
        anchor_trace = self._build_anchor_trace(request, anchors, day_candidates)
        if anchor_trace is not None:
            agent_traces.append(anchor_trace)

        pinned_by_day, unresolved_pins = self._resolve_pinned_attractions(
            request, list(attraction_result.attractions or []))
        if unresolved_pins:
            planning_notes.append("这些必去景点没在候选里找到、未排入：" + "、".join(unresolved_pins))

        intercity = self._plan_intercity(request, agent_traces, list(hotel_result.hotels or []))
        if intercity.notes:
            planning_notes.extend(intercity.notes)
        last_day = request.calculate_days() - 1

        return PlannerInput(
            request=request,
            attraction_result=attraction_result,
            weather_result=weather_result,
            hotel_result=hotel_result,
            constraints=constraints,
            planning_notes=planning_notes,
            planner_query=self._build_planner_query(request, attraction_result, weather_result, hotel_result, constraints),
            agent_traces=agent_traces,
            hotel_anchor_location=anchors.whole_trip,
            day_hotel_anchor_locations=anchors.by_day,
            day_hotel_candidates=day_candidates,
            pinned_attractions_by_day=pinned_by_day,
            intercity_cost=intercity.cost,
            intercity_mode=intercity.option.mode if intercity.option else None,
            intercity_estimated=bool(intercity.option and intercity.option.estimated),
            arrival_point=intercity.arrival_point,
            day_end_points=({last_day: intercity.departure_point} if intercity.departure_point else None),
        )

    def _long_term_preferences(self, user_id: int | None) -> list[str]:
        """**本人**的长期偏好原文。没接服务 / 没登录 / 读不出来 → 空列表，规划照跑。"""
        if self._preference_service is None or user_id is None:
            return []
        try:
            return self._preference_service.list_belonging(user_id)
        except Exception:  # noqa: BLE001 - 偏好读不出来不该让整次规划挂掉
            return []

    def _long_term_companions(self, user_id: int | None) -> list[str]:
        """**同行的人**的偏好，每条带着是谁（「老婆不能吃辣」，§2.5.4）。

        与本人的分开取、分开传：注入时分两段写，混在一起模型就分不出哪条是谁的——
        而"这条到底是不是他的"正是第五档要修的东西。
        """
        if self._preference_service is None or user_id is None:
            return []
        try:
            return self._preference_service.list_companions(user_id)
        except Exception:  # noqa: BLE001
            return []

    def _long_term_episodes(self, user_id: int | None, city: str | None) -> list[str]:
        """这个人**在这个城市**的场景原文。没接服务 / 没登录 / 读不出来 → 空列表，规划照跑。

        没城市时返回空——**不是**"把全部场景都拿出来"：场景取出来是给模型看
        "上次在这儿发生了什么"，喂别的城市的等于在诱导它认错地方（§2.4.3）。
        """
        if self._episode_service is None or user_id is None or not city:
            return []
        try:
            return self._episode_service.list_for_city(user_id, city)
        except Exception:  # noqa: BLE001 - 场景读不出来不该让整次规划挂掉
            return []

    def _record_episodes(self, constraints: PlanningConstraints, user_id: int | None) -> None:
        """把备注里抽出来的场景落库。整段包在 try 里、失败即不记（§2.2.4）：搭便车的乘客。"""
        if self._episode_service is None or user_id is None:
            return
        try:
            self._episode_service.record(user_id, constraints.extracted_episodes, SOURCE_FORM)
        except Exception:  # noqa: BLE001
            pass

    def _build_episode_trace(self, request: TripPlanRequest, user_id: int | None,
                             long_term_episodes: list[str],
                             constraints: PlanningConstraints) -> AgentTrace | None:
        """场景留痕：这一趟的城市注入了 N 条、本次新记下哪几条（§2.4.7 判据 2/3）。

        **触发条件与 _build_preference_trace 故意不同**：那条在"什么都没有"时不写，
        为的是保住默认链路的留痕逐字一致。这条**登录了就写**，哪怕两样都是 0——
        判据 2 要看的正是「换个城市 → 注入 **0** 条」，不写出来就只剩"没有那条痕"
        这个弱信号，而"没有"和"这条链根本没跑"看起来是一样的。
        """
        if self._episode_service is None or user_id is None:
            return None
        extracted = list(constraints.extracted_episodes or [])
        city = request.city or "未填目的地"
        input_summary = f"{city} 的场景：{len(long_term_episodes)} 条" + (
            "（" + "；".join(long_term_episodes) + "）" if long_term_episodes else "")
        if extracted:
            output_summary = "本次新记下：" + "；".join(
                f"{item['city']}：{item['content']}" for item in extracted)
        else:
            output_summary = "本次没有新的场景"
        return AgentTrace(
            agent_name="EpisodeService",
            action="注入并记录场景记忆",
            input_summary=input_summary,
            output_summary=output_summary,
            status="success",
        )

    def _record_preferences(self, constraints: PlanningConstraints, user_id: int | None) -> None:
        """把备注里抽出来的长期偏好落库。整段包在 try 里、失败即不记（§2.2.4）：
        记偏好是搭便车的乘客，不能让它把车开翻——规划失败和偏好没记住，差着一个量级。"""
        if self._preference_service is None or user_id is None:
            return
        try:
            self._preference_service.record(
                user_id, constraints.extracted_preferences, SOURCE_FORM)
        except Exception:  # noqa: BLE001
            pass

    def _build_preference_trace(self, long_term: list[str], companions: list[str],
                                constraints: PlanningConstraints) -> AgentTrace | None:
        """注入留痕。**一条偏好都没有、也没抽出来新的时不写**：默认链路的留痕要与
        加这一档之前逐字一致，多一条都算差异（照 _build_anchor_trace 的规矩）。

        「注入了 N 条」是本档唯一能自动化证明注入真的发生了的痕迹（§2.2.9 判据 7）——
        不加这条，注入只能靠"答案看起来对"来判断，那不算判据。
        第五档起本人与同行的人**分开报数**：那正是这一档要能一眼看出来的东西。
        """
        extracted = render_extracted(constraints.extracted_preferences)
        if not long_term and not companions and not extracted:
            return None
        input_summary = f"长期偏好：{len(long_term)} 条" + (
            "（" + "、".join(long_term) + "）" if long_term else "")
        if companions:
            input_summary += f"；同行的人：{len(companions)} 条（{'、'.join(companions)}）"
        if extracted:
            output_summary = "本次新记下：" + "、".join(extracted)
        else:
            output_summary = "本次没有新的长期偏好"
        return AgentTrace(
            agent_name="PreferenceService",
            action="注入并记录长期偏好",
            input_summary=input_summary,
            output_summary=output_summary,
            status="success",
        )

    def _plan_intercity(self,
                        request: TripPlanRequest,
                        agent_traces: list[AgentTrace],
                        hotels: list[Hotel]) -> InterCityPlan:
        """城际那一笔钱与到达口岸。没接这个服务（或没填出发城市）时返回空方案，逐天与现状一致。

        `hotels` 是给选档第二道用的：判断"升级之后整趟还超不超支"要在**已经抓到的**
        酒店候选池里重选一次（docs/INTERCITY_TRANSPORT_DESIGN.md 4.2），不再调高德。
        """
        if self._intercity_service is None:
            return InterCityPlan()
        days = request.calculate_days()
        plan = self._intercity_service.plan(
            request,
            spend_estimator=lambda cost: estimate_trip_spend(
                budget=request.budget, days=days, hotels=hotels, intercity_cost=cost),
        )
        agent_traces.append(AgentTrace(
            agent_name="IntercityService",
            action="估算往返大交通",
            input_summary=f"{request.origin_city or '未填出发城市'} → {request.city}",
            output_summary=("未启用" if plan.option is None else
                            f"{plan.option.mode} 往返约 {plan.option.round_trip_cost} 元"
                            f"{'（估算）' if plan.option.estimated else '（高德换乘方案参考价）'}"
                            + (f"，到达 {plan.option.arrival_station}" if plan.arrival_point else "")),
            status="success" if plan.option is not None else "skipped",
        ))
        return plan

    def _resolve_pinned_attractions(self,
                                    request: TripPlanRequest,
                                    attractions: list[Attraction]) -> tuple[dict[int, list[Attraction]], list[str]]:
        """把 day_attraction_pins 里的景点名在已抓到的候选池里按名字对上；对不上再按名补搜一次。

        先池内匹配（省一次请求），命中不了才用 attraction_client.find_attraction_by_name 补搜；
        补搜也没有的才记进 unresolved——宁可不排并写明，也不凭空造一个景点。
        """
        pins_map: dict[int, list[Attraction]] = {}
        unresolved: list[str] = []
        for raw_day, names in (request.day_attraction_pins or {}).items():
            day = int(raw_day)
            resolved: list[Attraction] = []
            for name in names:
                match = next((a for a in attractions if a.name == name), None) \
                    or next((a for a in attractions if a.name and (name in a.name or a.name in name)), None)
                if match is None and self._attraction_client is not None:
                    match = self._attraction_client.find_attraction_by_name(request.city, name)
                if match is not None:
                    if not any(a.name == match.name for a in resolved):
                        resolved.append(match)
                else:
                    unresolved.append(f"第{day + 1}天『{name}』")
            if resolved:
                pins_map[day] = resolved
        return pins_map, unresolved

    def _build_anchor_trace(self,
                            request: TripPlanRequest,
                            anchors,
                            day_candidates: dict[int, list]) -> AgentTrace | None:
        # 用户没指定住宿位置时不留痕：这里要保持默认行为不变，多一条痕迹都算差异。
        if not (request.hotel_anchor or request.day_hotel_anchors):
            return None
        if anchors.unresolved:
            return AgentTrace(
                agent_name="HotelAnchorService",
                action="解析住宿位置锚点",
                input_summary="、".join(anchors.unresolved),
                output_summary="这些地名没搜到坐标，已按未填写处理",
                status="fallback",
            )
        if not anchors.every_location():
            return AgentTrace(
                agent_name="HotelAnchorService",
                action="解析住宿位置锚点",
                input_summary=request.hotel_anchor or "、".join(
                    f"第{day + 1}天 {name}" for day, name in (request.day_hotel_anchors or {}).items()),
                output_summary="没定位到坐标，已按未填写处理",
                status="fallback",
            )
        days = "、".join(f"第{day + 1}天" for day in sorted(day_candidates))
        return AgentTrace(
            agent_name="HotelAnchorService",
            action="解析住宿位置锚点",
            input_summary=request.hotel_anchor or "、".join(
                f"第{day + 1}天 {name}" for day, name in (request.day_hotel_anchors or {}).items()),
            output_summary=(f"已定位 {len(anchors.every_location())} 处，按锚点补检索 {anchors.searched} 次，"
                            f"新增 {anchors.found_nearby} 家附近候选（只用于{days or '没有可用的一天'}）"),
            status="success",
        )

    def _validate_planning_inputs(self,
                                  attraction_result: AttractionSearchResult | None,
                                  weather_result: WeatherQueryResult | None,
                                  hotel_result: HotelSearchResult | None) -> None:
        if attraction_result is None or not attraction_result.attractions:
            raise TripPlanningException("当前条件下没有找到可用景点，建议更换目的地或调整偏好")
        if weather_result is None or not weather_result.weather_info:
            raise TripPlanningException("天气信息暂时不可用")
        if hotel_result is None or not hotel_result.hotels:
            raise TripPlanningException("酒店推荐结果暂时不可用")

    def _build_planning_notes(self,
                              request: TripPlanRequest,
                              attraction_result: AttractionSearchResult,
                              weather_result: WeatherQueryResult,
                              hotel_result: HotelSearchResult,
                              constraints: PlanningConstraints,
                              long_term: list[str] | None = None,
                              long_term_episodes: list[str] | None = None,
                              long_term_companions: list[str] | None = None) -> list[str]:
        """注意这里同时是**总览文案的输入**（planning_notes 会进 LlmNarrationService 的
        总览 prompt），所以长期偏好写到这儿就够了，不用再往文案那一层单独穿一次 user_id。
        """
        notes: list[str] = []
        notes.append(f"本次规划目的地为{request.city}，共{request.calculate_days()}天")
        notes.append(attraction_result.summary)
        notes.append(weather_result.summary)
        notes.append(hotel_result.summary)
        notes.append(f"景点来源：{attraction_result.provider.provider_name}")
        notes.append(f"天气来源：{weather_result.provider.provider_name}")
        notes.append(f"酒店来源：{hotel_result.provider.provider_name}")
        if constraints.analysis_source is not None and constraints.analysis_source.strip():
            notes.append(f"需求理解来源：{constraints.analysis_source}")
        if request.extra_requirements is not None and request.extra_requirements.strip():
            notes.append(f"额外要求：{request.extra_requirements}")
        if constraints.extracted_tags:
            notes.append("识别出的规划约束：" + "、".join(constraints.extracted_tags))
        if long_term:
            # 长期偏好进这里，规划器和总览文案都能看到。**一条都没有时不写**：
            # 默认链路的备注要与加这一档之前逐字一致。
            notes.append("这个人的长期偏好：" + "、".join(long_term))
        if long_term_companions:
            # 第五档：同行的人**单独一行**，不并进上面那条。并进去就等于又混成
            # "都是他的"了——那正是这一档要修的东西。行首写明是谁的，总览文案照这个说。
            notes.append("同行的人的偏好（不是他的）：" + "、".join(long_term_companions))
        if long_term_episodes:
            # 场景同理，同一格（§2.4.5）。**写法上刻意与偏好那条并列**，
            # 让总览文案知道这是"上次发生过的事"，不是"这次要办的事"。
            notes.append(f"上次在{request.city}的事：" + "；".join(long_term_episodes))
        return notes

    def _build_agent_traces(self,
                            request: TripPlanRequest,
                            attraction_result: AttractionSearchResult,
                            weather_result: WeatherQueryResult,
                            hotel_result: HotelSearchResult,
                            constraints: PlanningConstraints) -> list[AgentTrace]:
        traces: list[AgentTrace] = []
        traces.append(AgentTrace(
            agent_name="AttractionAgent",
            action="检索候选景点",
            input_summary=f"城市={request.city}，偏好={'、'.join(request.preferences or [])}",
            output_summary=(
                f"获得 {len(attraction_result.attractions)} 个候选景点，"
                f"来源={attraction_result.provider.provider_name}"
            ),
            status="success",
        ))
        traces.append(AgentTrace(
            agent_name="WeatherAgent",
            action="查询出行天气",
            input_summary=f"城市={request.city}，日期={request.start_date} 至 {request.end_date}",
            output_summary=(
                f"获得 {len(weather_result.weather_info)} 天天气，"
                f"来源={weather_result.provider.provider_name}"
            ),
            status="success",
        ))
        traces.append(AgentTrace(
            agent_name="HotelAgent",
            action="推荐住宿候选",
            input_summary=f"城市={request.city}，住宿偏好={request.accommodation}",
            output_summary=(
                f"获得 {len(hotel_result.hotels)} 个候选酒店，"
                f"来源={hotel_result.provider.provider_name}"
            ),
            status="success",
        ))
        traces.append(AgentTrace(
            agent_name="RequirementAnalysisService",
            action="解析自然语言额外要求",
            input_summary=(
                "用户未填写额外要求"
                if request.extra_requirements is None or not request.extra_requirements.strip()
                else request.extra_requirements
            ),
            output_summary=(
                "未识别到额外约束"
                if not constraints.extracted_tags
                else "识别约束：" + "、".join(constraints.extracted_tags)
            ),
            status="success",
        ))
        return traces

    def _build_planner_query(self,
                             request: TripPlanRequest,
                             attraction_result: AttractionSearchResult,
                             weather_result: WeatherQueryResult,
                             hotel_result: HotelSearchResult,
                             constraints: PlanningConstraints) -> str:
        builder = f"请为 {request.city} 生成 {request.calculate_days()} 天旅行计划。"
        builder += f" 用户偏好：{'、'.join(request.preferences or [])}。"
        transportation = (
            "智能推荐" if request.transportation is None or not request.transportation.strip()
            else request.transportation
        )
        builder += f" 出行方式：{transportation}，住宿偏好：{request.accommodation}。"
        builder += f" 景点候选数：{len(attraction_result.attractions)}，"
        builder += f" 天气天数：{len(weather_result.weather_info)}，"
        builder += f" 酒店候选数：{len(hotel_result.hotels)}。"
        if constraints.extracted_tags:
            builder += " 额外约束：" + "、".join(constraints.extracted_tags) + "。"
        return builder
