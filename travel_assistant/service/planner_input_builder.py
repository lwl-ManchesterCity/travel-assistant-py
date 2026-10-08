"""对应 Java service/PlannerInputBuilder.java：组装 PlannerInput 并产出 planningNotes / agentTraces。"""

from ..agent.data_tools import raise_planning_errors
from ..agent.orchestration import PlanningDataAgent
from ..agent.planning_context import PlanningContext
from ..exceptions import TripPlanningException
from ..models.agent_trace import AgentTrace
from ..models.attraction_search_result import AttractionSearchResult
from ..models.hotel_search_result import HotelSearchResult
from ..models.planner_input import PlannerInput
from ..models.planning_constraints import PlanningConstraints
from ..models.trip_plan_request import TripPlanRequest
from ..models.weather_query_result import WeatherQueryResult
from .hotel_anchor_service import HotelAnchorService
from .requirement_analysis_service import RequirementAnalysisService


class PlannerInputBuilder:

    def __init__(self,
                 planning_data_agent: PlanningDataAgent,
                 requirement_analysis_service: RequirementAnalysisService,
                 hotel_anchor_service: HotelAnchorService):
        self._planning_data_agent = planning_data_agent
        self._requirement_analysis_service = requirement_analysis_service
        self._hotel_anchor_service = hotel_anchor_service

    def build(self, request: TripPlanRequest) -> PlannerInput:
        context = PlanningContext(request=request)
        self._planning_data_agent.collect(context)
        raise_planning_errors(context)

        attraction_result = context.attraction_result
        weather_result = context.weather_result
        hotel_result = context.hotel_result
        constraints = self._requirement_analysis_service.analyze(request)
        self._validate_planning_inputs(attraction_result, weather_result, hotel_result)
        planning_notes = self._build_planning_notes(request, attraction_result, weather_result, hotel_result, constraints)
        agent_traces = self._build_agent_traces(request, attraction_result, weather_result, hotel_result, constraints)
        anchors = self._hotel_anchor_service.resolve(request)
        nearby = self._hotel_anchor_service.search_nearby(request, anchors)
        if nearby:
            hotel_result = self._merge_hotel_candidates(hotel_result, nearby)
        anchor_trace = self._build_anchor_trace(request, anchors)
        if anchor_trace is not None:
            agent_traces.append(anchor_trace)

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
        )

    def _merge_hotel_candidates(self, hotel_result: HotelSearchResult, nearby: list) -> HotelSearchResult:
        merged = list(hotel_result.hotels or [])
        for hotel in nearby:
            if not any(existing.name == hotel.name for existing in merged):
                merged.append(hotel)
        return HotelSearchResult(
            city=hotel_result.city,
            hotels=merged,
            summary=hotel_result.summary,
            provider=hotel_result.provider,
        )

    def _build_anchor_trace(self, request: TripPlanRequest, anchors) -> AgentTrace | None:
        # 用户没指定住宿位置时不留痕：这条链路的默认行为要与 Java 逐字一致，多一条痕迹都算差异。
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
        return AgentTrace(
            agent_name="HotelAnchorService",
            action="解析住宿位置锚点",
            input_summary=request.hotel_anchor or "、".join(
                f"第{day + 1}天 {name}" for day, name in (request.day_hotel_anchors or {}).items()),
            output_summary=(f"已定位 {len(anchors.every_location())} 处，按锚点补检索 {anchors.searched} 次，"
                             f"新增 {anchors.found_nearby} 家附近候选"),
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
                              constraints: PlanningConstraints) -> list[str]:
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
