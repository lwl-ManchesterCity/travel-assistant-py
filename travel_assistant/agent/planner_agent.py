"""对应 Java agent/PlannerAgent.java：单轮行程生成的规则主体。

判定与选点全部在这里按 Java 规则复现；模型只负责需求理解与文案润色，不产出 POI、坐标、价格或天气。

偏离源项目的一处（详见 MIGRATION_NOTES.md 与 docs/ADJUST_AGENT_DESIGN.md 第 9 节）：用户填了
住宿位置或要求整趟住同一家时，这里会先按锚点筛掉圈外候选、再按远近分档，同档内仍跑 Java 原样的
三项打分；打分公式与既有常数一个字没改。Java 没有这三格，因为它的请求里没有对应字段。

第二处偏离（2026-10-09）：选景点时同样让它贴酒店——候选先按离酒店 5/10km 分档、近档整档先选，
同档内仍跑 Java 原样的 `_score_attraction`；离酒店超过 15km 的景点被剔掉（不足 2 个则回落全城，
防某天选空）。Java 里景点对距离既不分档也不设这 15km 顶，只按分数排。
"""

from datetime import date, timedelta
import math

from ..config.settings import AnchorSettings
from ..evaluator.daily_plan_evaluator import DailyPlanEvaluator
from ..models.agent_trace import AgentTrace
from ..models.attraction import Attraction
from ..models.budget import Budget
from ..models.daily_plan_reflection import DailyPlanReflection
from ..models.day_plan import DayPlan
from ..models.hotel import Hotel
from ..models.location import Location
from ..models.meal import Meal
from ..models.planner_input import PlannerInput
from ..models.planning_constraints import PlanningConstraints
from ..models.route_plan import RoutePlan
from ..models.route_query import RouteQuery
from ..models.trip_plan import TripPlan
from ..models.trip_plan_request import TripPlanRequest
from ..models.weather_info import WeatherInfo
from ..utils import jround
from .route_agent import RouteAgent

# 景点贴酒店（2026-10-09 第二次主动偏离 Java，见 MIGRATION_NOTES）：
# 硬顶把离酒店太远的候选剔掉，分档让"近"排在"分数"前面——同档内仍跑 Java 原样打分。
# 初步景点趟没有酒店（hotel=None），分档恒为 0，退化成纯分数，酒店选取因此不受影响。
_ATTRACTION_HOTEL_RADIUS_KM = 15.0
_ATTRACTION_NEAR_KM = 5.0
_ATTRACTION_MID_KM = 10.0


class _DailyPlanDecision:
    """对应 Java PlannerAgent.DailyPlanDecision。"""

    def __init__(self, hotel: Hotel | None,
                 attractions: list[Attraction],
                 transportation: str,
                 meals: list[Meal],
                 route_plan: RoutePlan | None,
                 estimated_cost: int,
                 notes: list[str]):
        self.hotel = hotel
        self.attractions = attractions
        self.transportation = transportation
        self.meals = meals
        self.route_plan = route_plan
        self.estimated_cost = estimated_cost
        self.notes = notes


class PlannerAgent:

    def __init__(self, route_agent: RouteAgent, daily_plan_evaluator: DailyPlanEvaluator, llm_narration_service,
                 anchor_settings: AnchorSettings | None = None):
        self._route_agent = route_agent
        self._daily_plan_evaluator = daily_plan_evaluator
        self._llm_narration_service = llm_narration_service
        self._anchor_settings = anchor_settings or AnchorSettings()

    def build_plan(self, planner_input: PlannerInput) -> TripPlan:
        request = planner_input.request
        attractions = self._sanitize_attractions(planner_input.attraction_result.attractions or [])
        weather_info = planner_input.weather_result.weather_info or []
        hotels = planner_input.hotel_result.hotels or []
        constraints = planner_input.constraints
        day_plans: list[DayPlan] = []
        agent_traces: list[AgentTrace] = list(planner_input.agent_traces or [])
        used_attraction_names: set[str] = set()
        # 预留：被点名的必去景点先占住，任何一天的常规挑选都不许拿走（必去那天在 _refine 后强制塞回）。
        for pinned in (planner_input.pinned_attractions_by_day or {}).values():
            for attraction in pinned:
                used_attraction_names.add(attraction.name)
        start_date = date.fromisoformat(request.start_date)
        daily_budget_limit = max(200, request.budget // request.calculate_days())
        days = request.calculate_days()
        strict_continuity = request.is_strict_hotel_continuity()
        locked_hotel: Hotel | None = None

        for day_index in range(days):
            current_date = (start_date + timedelta(days=day_index)).isoformat()
            current_weather = self._find_weather_for_date(weather_info, current_date)
            preliminary_attractions = self._pick_daily_attractions(
                attractions,
                day_index,
                daily_budget_limit,
                None,
                used_attraction_names,
                request.preferences,
                constraints,
                current_weather,
            )
            anchor_label = request.hotel_anchor_label_for_day(day_index)
            anchor_notes: list[str] = []
            if strict_continuity and day_index > 0 and locked_hotel is not None:
                # 只留锁定的这一家：另一处换店入口（reduce_budget 分支）也拿不到别的选择
                candidate_hotels = [locked_hotel]
                selected_hotel = locked_hotel
                # 只有"某一天单独指定的位置"才算被 strict 顶掉；整趟那个锚点在第 1 天已经生效过
                day_anchor_label = (request.day_hotel_anchors or {}).get(day_index)
                anchor_notes.append(self._locked_hotel_note(
                    locked_hotel, hotels, preliminary_attractions, day_anchor_label, day_index))
            else:
                day_pool = planner_input.hotel_candidates_for_day(day_index, hotels)
                candidate_hotels = self._narrow_hotel_candidates(
                    day_pool, planner_input.hotel_anchor_for_day(day_index), anchor_label, anchor_notes)
                selected_hotel = self._select_hotel(candidate_hotels, preliminary_attractions, daily_budget_limit)
                if strict_continuity:
                    locked_hotel = selected_hotel
            daily_attractions = self._pick_daily_attractions(
                attractions,
                day_index,
                daily_budget_limit,
                selected_hotel,
                used_attraction_names,
                request.preferences,
                constraints,
                current_weather,
            )
            decision = self._refine_daily_plan(
                request,
                constraints,
                candidate_hotels,
                attractions,
                used_attraction_names,
                agent_traces,
                day_index,
                daily_budget_limit,
                current_weather,
                selected_hotel,
                daily_attractions,
            )
            day_pins = planner_input.pinned_for_day(day_index)
            if day_pins:
                decision = self._apply_day_pins(
                    decision, day_pins, request.city, day_index, daily_budget_limit,
                    hotels, current_weather,
                    planner_input.hotel_anchor_for_day(day_index) is not None)
            for note in anchor_notes:
                if note not in decision.notes:
                    decision.notes.append(note)
            self._mark_attractions_as_used(used_attraction_names, decision.attractions)
            lead_attraction = decision.attractions[0]
            day_plan = DayPlan(
                date=current_date,
                day_index=day_index,
                description=self._build_description(
                    lead_attraction, constraints, decision.route_plan, decision.estimated_cost, day_index + 1, decision.notes
                ),
                transportation=decision.transportation,
                accommodation=request.accommodation,
                hotel=decision.hotel,
                attractions=decision.attractions,
                meals=decision.meals,
                route_plan=decision.route_plan,
                estimated_cost=decision.estimated_cost,
                optimization_notes=decision.notes,
            )
            day_plan.description = self._llm_narration_service.polish_day_description(request, day_plan)
            day_plans.append(day_plan)

        planning_notes: list[str] = list(planner_input.planning_notes or [])
        if self._llm_narration_service.is_available():
            planning_notes.append("结果文案来源：LLM润色")
        else:
            planning_notes.append("结果文案来源：规则文案")
        planning_notes.append("ReflectionAgent 已对每日方案完成预算、路线、天气反思评估")

        overall_suggestions = self._build_overall_suggestions(request, weather_info, planning_notes, day_plans)
        overall_suggestions = self._llm_narration_service.polish_overall_suggestions(
            request,
            weather_info,
            day_plans,
            planning_notes,
            overall_suggestions,
        )

        return TripPlan(
            city=request.city,
            start_date=request.start_date,
            end_date=request.end_date,
            days=day_plans,
            weather_info=weather_info,
            overall_suggestions=overall_suggestions,
            budget=self._build_budget(day_plans, request.budget),
            planning_notes=planning_notes,
            agent_traces=agent_traces,
        )

    def _pick_daily_attractions(self,
                                attractions: list[Attraction],
                                day_index: int,
                                daily_budget_limit: int,
                                hotel: Hotel | None,
                                used_attraction_names: set[str],
                                preferences: list[str] | None,
                                constraints: PlanningConstraints | None,
                                current_weather: WeatherInfo | None) -> list[Attraction]:
        selected: list[Attraction] = []
        nearby_attractions = self._filter_nearby_attractions(attractions, hotel)
        remaining_ticket_budget = max(0, daily_budget_limit - self._estimate_hotel_cost(hotel) - 180)
        target_count = self._determine_target_count(day_index, daily_budget_limit, constraints, current_weather)
        candidate_attractions = self._prioritize_candidate_attractions(
            nearby_attractions or attractions,
            hotel,
            preferences,
            constraints,
            current_weather,
            daily_budget_limit,
        )
        self._try_select_attractions(selected, candidate_attractions, day_index, target_count, remaining_ticket_budget, used_attraction_names, True)
        if len(selected) < target_count:
            self._try_select_attractions(selected, candidate_attractions, day_index, target_count, remaining_ticket_budget, used_attraction_names, False)
        if not selected:
            fallback = self._find_fallback_attraction(candidate_attractions, used_attraction_names)
            if fallback is not None:
                selected.append(fallback)
        return selected

    def _find_fallback_attraction(self, candidate_attractions: list[Attraction], used_attraction_names: set[str]) -> Attraction | None:
        for candidate_attraction in candidate_attractions:
            if candidate_attraction.name not in used_attraction_names:
                return candidate_attraction
        return candidate_attractions[0] if candidate_attractions else None

    def _determine_target_count(self,
                                day_index: int,
                                daily_budget_limit: int,
                                constraints: PlanningConstraints | None,
                                current_weather: WeatherInfo | None) -> int:
        target_count = 3 if day_index % 2 == 0 else 2
        if daily_budget_limit >= 2500:
            target_count = 4
        elif daily_budget_limit >= 1600:
            target_count = max(target_count, 3)
        if daily_budget_limit < 550:
            target_count = min(target_count, 2)
        if daily_budget_limit < 380:
            target_count = 1
        if constraints is not None and constraints.relaxed_pace:
            target_count = min(target_count, 2)
        if self._prefers_indoor_day(constraints, current_weather):
            target_count = min(target_count, 2)
        return max(1, target_count)

    def _prioritize_candidate_attractions(self,
                                          attractions: list[Attraction],
                                          hotel: Hotel | None,
                                          preferences: list[str] | None,
                                          constraints: PlanningConstraints | None,
                                          current_weather: WeatherInfo | None,
                                          daily_budget_limit: int) -> list[Attraction]:
        # Java: Comparator.comparingDouble(score).reversed().thenComparingInt(ticketPrice)
        # 偏离：先按离酒店距离分档（近的整档先选），同档内仍是 Java 的"分数降序、票价升序"。
        return sorted(
            attractions,
            key=lambda attraction: (
                self._distance_band_to_hotel(hotel, attraction),
                -self._score_attraction(attraction, preferences, constraints, current_weather, daily_budget_limit),
                attraction.ticket_price,
            ),
        )

    def _distance_band_to_hotel(self, hotel: Hotel | None, attraction: Attraction) -> int:
        if hotel is None or hotel.location is None or attraction.location is None:
            return 0
        distance_km = self._calculate_distance_km(hotel.location, attraction.location)
        if distance_km <= _ATTRACTION_NEAR_KM:
            return 0
        if distance_km <= _ATTRACTION_MID_KM:
            return 1
        return 2

    def _score_attraction(self,
                          attraction: Attraction,
                          preferences: list[str] | None,
                          constraints: PlanningConstraints | None,
                          current_weather: WeatherInfo | None,
                          daily_budget_limit: int) -> float:
        score = 0.0
        if self._matches_preference(attraction, preferences):
            score += 40
        if self._prefers_indoor_day(constraints, current_weather):
            score += 18 if self._is_indoor_friendly(attraction) else -20
        if constraints is not None and constraints.relaxed_pace and attraction.visit_duration <= 150:
            score += 8
        if daily_budget_limit < 550 and attraction.ticket_price <= 50:
            score += 10
        if daily_budget_limit < 380 and attraction.ticket_price > 60:
            score -= 12
        if daily_budget_limit >= 1600 and attraction.ticket_price >= 60:
            score += 6
        if daily_budget_limit >= 2500 and attraction.visit_duration >= 150:
            score += 4
        if attraction.rating is not None:
            score += attraction.rating
        return score

    def _matches_preference(self, attraction: Attraction, preferences: list[str] | None) -> bool:
        if not preferences:
            return True
        category = attraction.category or ""
        return any(preference in category for preference in preferences)

    def _find_weather_for_date(self, weather_info: list[WeatherInfo], current_date: str) -> WeatherInfo | None:
        if not weather_info:
            return None
        for item in weather_info:
            if current_date == item.date:
                return item
        return weather_info[0]

    def _prefers_indoor_day(self, constraints: PlanningConstraints | None, current_weather: WeatherInfo | None) -> bool:
        if constraints is None or not constraints.prefers_indoor_backup:
            return False
        if current_weather is None or current_weather.day_weather is None:
            return False
        weather = current_weather.day_weather
        return "雨" in weather or "雪" in weather or "雷" in weather

    def _is_indoor_friendly(self, attraction: Attraction) -> bool:
        category = attraction.category or ""
        return category in ("人文历史", "城市漫游")

    def _try_select_attractions(self,
                                selected: list[Attraction],
                                candidate_attractions: list[Attraction],
                                day_index: int,
                                target_count: int,
                                remaining_ticket_budget: int,
                                used_attraction_names: set[str],
                                skip_used_attractions: bool) -> None:
        size = len(candidate_attractions)
        for offset in range(size):
            if len(selected) >= target_count:
                break
            attraction = candidate_attractions[(day_index + offset) % size]
            if skip_used_attractions and attraction.name in used_attraction_names:
                continue
            next_ticket_total = sum(item.ticket_price for item in selected) + attraction.ticket_price
            if not selected or next_ticket_total <= remaining_ticket_budget:
                if not self._contains_attraction(selected, attraction.name) and self._is_reasonably_close(selected, attraction):
                    selected.append(attraction)

    def _mark_attractions_as_used(self, used_attraction_names: set[str], daily_attractions: list[Attraction]) -> None:
        for attraction in daily_attractions:
            used_attraction_names.add(attraction.name)

    def _build_description(self,
                           attraction: Attraction,
                           constraints: PlanningConstraints | None,
                           route_plan: RoutePlan | None,
                           estimated_cost: int,
                           day_number: int,
                           decision_notes: list[str] | None) -> str:
        builder = (
            f"第{day_number}天围绕{attraction.name}展开，重点体验{attraction.category}内容。"
            f"预计当日花费约 {estimated_cost} 元。"
        )
        if constraints is not None and constraints.relaxed_pace:
            builder += " 行程节奏会更宽松，预留更多休息和拍照时间。"
        if constraints is not None and constraints.wants_landmark_ceremony:
            builder += " 如果条件允许，可优先安排带有仪式感的地标打卡。"
        if constraints is not None and constraints.prefers_indoor_backup:
            builder += " 同时预留室内备选方案，方便根据天气灵活调整。"
        if route_plan is not None:
            builder += f" {route_plan.summary}"
        if decision_notes:
            builder += " 已自动优化：" + "；".join(decision_notes) + "。"
        return builder

    def _apply_day_pins(self,
                        decision: "_DailyPlanDecision",
                        pins: list[Attraction],
                        city: str,
                        day_index: int,
                        daily_budget_limit: int,
                        all_hotels: list[Hotel],
                        weather: WeatherInfo | None,
                        anchor_hotel_to_last: bool) -> "_DailyPlanDecision":
        """有必去景点那天的收尾。普通天不走这里。

        - 只 1 个：以它为中心补附近高分（靠"酒店锚到它 + 就近档"已经实现），把它留作当天最后一站。
        - 2 个及以上：这天就=你点名的这些，不再自动补别的；游玩顺序交给 LLM 按天气/环境排（失败回落原序）。
        酒店（用户同意挪时）从全量酒店里取离"当天最后一站"最近的一家，所以两个点离得远也能贴到收工那站。
        超预算/多绕路以重算后的具体数字写进备注，不静默。
        """
        if not pins:
            return decision
        if len(pins) >= 2:
            merged = self._llm_narration_service.order_attractions(pins, weather)
            replaced = True
        else:
            merged = [a for a in decision.attractions if not any(p.name == a.name for p in pins)]
            merged.extend(pins)
            replaced = False
        hotel = decision.hotel
        last = merged[-1] if merged else None
        if anchor_hotel_to_last and last is not None and last.location is not None and all_hotels:
            hotel = self._select_hotel(all_hotels, [last], daily_budget_limit) or decision.hotel
        old_route = decision.route_plan
        route_plan = self._route_agent.plan_route(RouteQuery(
            city=city,
            day_index=day_index,
            transportation=decision.transportation,
            hotel=hotel,
            attractions=merged,
        ))
        estimated_cost = self._estimate_daily_cost(merged, hotel, decision.meals, route_plan)
        label = "、".join(p.name for p in pins)
        note = (f"第 {day_index + 1} 天按你点名的必去景点安排：{label}"
                if replaced else f"已把必去景点排在第 {day_index + 1} 天最后一站：{label}")
        over = estimated_cost - daily_budget_limit
        if over > 0:
            note += f"；当日预计 ¥{estimated_cost}，超日预算 ¥{over}"
        if old_route is not None and route_plan is not None:
            extra_km = route_plan.total_distance_km - old_route.total_distance_km
            extra_minutes = route_plan.total_duration_minutes - old_route.total_duration_minutes
            if extra_km >= 0.1 or extra_minutes >= 1:
                note += f"；通勤多 {jround(extra_km)} 公里 / {extra_minutes} 分钟"
        notes = list(decision.notes)
        notes.append(note)
        return _DailyPlanDecision(hotel, merged, decision.transportation, decision.meals,
                                  route_plan, estimated_cost, notes)

    def _refine_daily_plan(self,
                           request: TripPlanRequest,
                           constraints: PlanningConstraints | None,
                           hotels: list[Hotel],
                           all_attractions: list[Attraction],
                           used_attraction_names: set[str],
                           agent_traces: list[AgentTrace],
                           day_index: int,
                           daily_budget_limit: int,
                           current_weather: WeatherInfo | None,
                           selected_hotel: Hotel | None,
                           daily_attractions: list[Attraction]) -> _DailyPlanDecision:
        working_hotel = selected_hotel
        working_attractions = list(daily_attractions)
        notes: list[str] = []
        hotel_adjusted = False
        adjust_rounds = 0

        while True:
            adjust_rounds += 1
            transportation = self._decide_transportation(request, constraints, working_attractions)
            self._enforce_hard_budget_cuts(working_attractions, working_hotel, daily_budget_limit, notes)
            meals = self._build_meals(request.city, request.preferences, constraints, working_attractions, daily_budget_limit)
            route_plan = self._route_agent.plan_route(RouteQuery(
                city=request.city,
                day_index=day_index,
                transportation=transportation,
                hotel=working_hotel,
                attractions=working_attractions,
            ))
            estimated_cost = self._estimate_daily_cost(working_attractions, working_hotel, meals, route_plan)
            agent_traces.append(AgentTrace(
                agent_name="PlannerAgent",
                action=f"生成第{day_index + 1}天候选方案",
                input_summary=f"候选景点={self._join_attraction_names(working_attractions)}，酒店={self._safe_hotel_name(working_hotel)}",
                output_summary=(
                    f"预计花费={estimated_cost}元，"
                    f"通勤={0 if route_plan is None else route_plan.total_duration_minutes}分钟"
                ),
                status="generated",
            ))
            reflection = self._daily_plan_evaluator.reflect_daily_plan(
                estimated_cost,
                daily_budget_limit,
                route_plan,
                constraints,
                current_weather,
            )
            evaluation = reflection.evaluation
            self._append_reflection_notes(notes, reflection.reflection_notes)
            agent_traces.append(AgentTrace(
                agent_name="ReflectionAgent",
                action=f"反思第{day_index + 1}天候选方案",
                input_summary=f"预算上限={daily_budget_limit}元，天气={'未知' if current_weather is None else current_weather.day_weather}",
                output_summary=f"{reflection.reason}，建议动作={reflection.recommended_action}",
                status="accepted" if reflection.is_acceptable() else "need_revision",
            ))
            if reflection.is_acceptable():
                return _DailyPlanDecision(working_hotel, working_attractions, transportation, meals, route_plan, estimated_cost, notes)
            if adjust_rounds >= 6:
                notes.append("已达到预算压缩上限，当前方案为可生成的最低配版本")
                return _DailyPlanDecision(working_hotel, working_attractions, transportation, meals, route_plan, estimated_cost, notes)
            if self._recommends(reflection, "replace_with_indoor"):
                self._append_diagnosis(notes, evaluation.weather_diagnosis)
                indoor_replacement_target = self._find_outdoor_attraction_for_replacement(working_attractions)
                indoor_alternative = self._select_replacement_attraction(
                    all_attractions,
                    working_attractions,
                    used_attraction_names,
                    working_hotel,
                    request.preferences,
                    constraints,
                    current_weather,
                    daily_budget_limit,
                    indoor_replacement_target,
                    "indoor",
                )
                if indoor_replacement_target is not None and indoor_alternative is not None:
                    self._replace_attraction(working_attractions, indoor_replacement_target, indoor_alternative)
                    notes.append(
                        f"已将 {indoor_replacement_target.name} 调整为更适合雨天的 {indoor_alternative.name}"
                    )
                    continue
            if self._recommends(reflection, "reduce_budget") and not hotel_adjusted:
                self._append_diagnosis(notes, evaluation.budget_diagnosis)
                cheaper_hotel = self._select_budget_friendly_hotel(hotels, working_attractions, daily_budget_limit, working_hotel)
                # Java 这里是引用比较（!=），不是字段相等比较，所以必须用 is not。
                if cheaper_hotel is not None and cheaper_hotel is not working_hotel:
                    working_hotel = cheaper_hotel
                    hotel_adjusted = True
                    notes.append("已切换为更省预算的酒店")
                    continue
            if self._recommends(reflection, "reduce_budget"):
                self._append_diagnosis(notes, evaluation.budget_diagnosis)
                expensive_target = self._find_most_expensive_attraction(working_attractions)
                cheaper_alternative = self._select_replacement_attraction(
                    all_attractions,
                    working_attractions,
                    used_attraction_names,
                    working_hotel,
                    request.preferences,
                    constraints,
                    current_weather,
                    daily_budget_limit,
                    expensive_target,
                    "budget",
                )
                if expensive_target is not None and cheaper_alternative is not None:
                    self._replace_attraction(working_attractions, expensive_target, cheaper_alternative)
                    notes.append(f"已将 {expensive_target.name} 调整为更省预算的 {cheaper_alternative.name}")
                    continue
            if self._recommends(reflection, "compress_route"):
                self._append_diagnosis(notes, evaluation.route_diagnosis)
                farthest_target = self._find_farthest_attraction(working_attractions, working_hotel)
                closer_alternative = self._select_replacement_attraction(
                    all_attractions,
                    working_attractions,
                    used_attraction_names,
                    working_hotel,
                    request.preferences,
                    constraints,
                    current_weather,
                    daily_budget_limit,
                    farthest_target,
                    "distance",
                )
                if farthest_target is not None and closer_alternative is not None:
                    self._replace_attraction(working_attractions, farthest_target, closer_alternative)
                    notes.append(f"已将 {farthest_target.name} 调整为更近的 {closer_alternative.name}")
                    continue
            if len(working_attractions) > 1:
                removed = self._select_attraction_to_trim(working_attractions, working_hotel, evaluation.route_too_long)
                self._remove_by_identity(working_attractions, removed)
                if evaluation.route_too_long:
                    notes.append(f"已减少景点 {removed.name} 以压缩通勤")
                else:
                    notes.append(f"已减少景点 {removed.name} 以控制预算")
                continue
            return _DailyPlanDecision(working_hotel, working_attractions, transportation, meals, route_plan, estimated_cost, notes)

    def _enforce_hard_budget_cuts(self,
                                  working_attractions: list[Attraction],
                                  working_hotel: Hotel | None,
                                  daily_budget_limit: int,
                                  notes: list[str]) -> None:
        max_attractions = self._determine_hard_budget_attraction_limit(daily_budget_limit, working_hotel)
        while len(working_attractions) > max_attractions:
            removed = working_attractions.pop(len(working_attractions) - 1)
            notes.append(f"低预算模式已减少景点 {removed.name}")

    def _determine_hard_budget_attraction_limit(self, daily_budget_limit: int, hotel: Hotel | None) -> int:
        hotel_cost = self._estimate_hotel_cost(hotel)
        if daily_budget_limit <= 380 or hotel_cost >= daily_budget_limit * 0.65:
            return 1
        if daily_budget_limit <= 520 or hotel_cost >= daily_budget_limit * 0.52:
            return 2
        return 3

    def _select_budget_friendly_hotel(self,
                                      hotels: list[Hotel] | None,
                                      attractions: list[Attraction],
                                      daily_budget_limit: int,
                                      current_hotel: Hotel | None) -> Hotel | None:
        if not hotels:
            return current_hotel
        best_hotel = current_hotel
        best_score = float("inf")
        attraction_center = self._calculate_center(attractions)
        for hotel in hotels:
            if current_hotel is not None and hotel.estimated_cost >= current_hotel.estimated_cost:
                continue
            budget_penalty = 10.0 if hotel.estimated_cost > daily_budget_limit * 0.48 else 0.0
            distance_penalty = (
                0.0
                if attraction_center is None or hotel.location is None
                else self._calculate_distance_km(hotel.location, attraction_center)
            )
            score = budget_penalty + distance_penalty
            if score < best_score:
                best_score = score
                best_hotel = hotel
        return best_hotel

    def _find_outdoor_attraction_for_replacement(self, attractions: list[Attraction]) -> Attraction | None:
        for index in range(len(attractions) - 1, -1, -1):
            attraction = attractions[index]
            if not self._is_indoor_friendly(attraction):
                return attraction
        return None

    def _find_most_expensive_attraction(self, attractions: list[Attraction]) -> Attraction | None:
        target: Attraction | None = None
        for attraction in attractions:
            if target is None or attraction.ticket_price > target.ticket_price:
                target = attraction
        return target

    def _find_farthest_attraction(self, attractions: list[Attraction], hotel: Hotel | None) -> Attraction:
        if hotel is None or hotel.location is None:
            return attractions[len(attractions) - 1]
        target: Attraction | None = None
        farthest_distance = -1
        for attraction in attractions:
            if attraction.location is None:
                continue
            distance = self._calculate_distance_km(hotel.location, attraction.location)
            if distance > farthest_distance:
                farthest_distance = distance
                target = attraction
        return target if target is not None else attractions[len(attractions) - 1]

    def _select_replacement_attraction(self,
                                       all_attractions: list[Attraction],
                                       working_attractions: list[Attraction],
                                       used_attraction_names: set[str],
                                       hotel: Hotel | None,
                                       preferences: list[str] | None,
                                       constraints: PlanningConstraints | None,
                                       current_weather: WeatherInfo | None,
                                       daily_budget_limit: int,
                                       target: Attraction | None,
                                       mode: str) -> Attraction | None:
        if target is None:
            return None
        nearby_attractions = self._filter_nearby_attractions(all_attractions, hotel)
        candidate_attractions = self._prioritize_candidate_attractions(
            nearby_attractions or all_attractions,
            hotel,
            preferences,
            constraints,
            current_weather,
            daily_budget_limit,
        )
        for candidate in candidate_attractions:
            if candidate.name == target.name:
                continue
            if self._contains_attraction(working_attractions, candidate.name):
                continue
            if candidate.name in used_attraction_names:
                continue
            if not self._is_reasonably_close_excluding_target(working_attractions, target, candidate):
                continue
            if mode == "indoor" and not self._is_indoor_friendly(candidate):
                continue
            if mode == "budget" and candidate.ticket_price >= target.ticket_price:
                continue
            if mode == "distance" and not self._is_closer_to_hotel(candidate, target, hotel):
                continue
            return candidate
        return None

    def _replace_attraction(self, attractions: list[Attraction], original: Attraction, replacement: Attraction) -> None:
        for index, attraction in enumerate(attractions):
            if attraction.name == original.name:
                attractions[index] = replacement
                return

    def _is_reasonably_close_excluding_target(self,
                                              working_attractions: list[Attraction],
                                              target: Attraction,
                                              candidate: Attraction) -> bool:
        remaining = [attraction for attraction in working_attractions if attraction.name != target.name]
        return self._is_reasonably_close(remaining, candidate)

    def _is_closer_to_hotel(self, candidate: Attraction, target: Attraction | None, hotel: Hotel | None) -> bool:
        if (hotel is None or hotel.location is None or candidate.location is None
                or target is None or target.location is None):
            return True
        return (self._calculate_distance_km(hotel.location, candidate.location)
                < self._calculate_distance_km(hotel.location, target.location))

    def _select_attraction_to_trim(self, attractions: list[Attraction], hotel: Hotel | None, route_too_long: bool) -> Attraction:
        if route_too_long:
            return self._find_farthest_attraction(attractions, hotel)
        return self._find_most_expensive_attraction(attractions)

    def _append_diagnosis(self, notes: list[str], diagnosis: str | None) -> None:
        if diagnosis is None or not diagnosis.strip() or diagnosis in notes:
            return
        notes.append(diagnosis)

    def _append_reflection_notes(self, notes: list[str], reflection_notes: list[str] | None) -> None:
        if not reflection_notes:
            return
        for reflection_note in reflection_notes:
            self._append_diagnosis(notes, reflection_note)

    def _recommends(self, reflection: DailyPlanReflection | None, action: str) -> bool:
        return reflection is not None and action == reflection.recommended_action

    def _decide_transportation(self,
                               request: TripPlanRequest,
                               constraints: PlanningConstraints | None,
                               daily_attractions: list[Attraction]) -> str:
        if request.transportation is not None and request.transportation.strip():
            return request.transportation
        total_visit_duration = sum(attraction.visit_duration for attraction in daily_attractions)
        if total_visit_duration > 360:
            return "公共交通 + 短途打车"
        if constraints is not None and constraints.relaxed_pace:
            return "公共交通 + 短途打车"
        return "公共交通"

    def _build_meals(self,
                     city: str,
                     preferences: list[str] | None,
                     constraints: PlanningConstraints | None,
                     daily_attractions: list[Attraction],
                     daily_budget_limit: int) -> list[Meal]:
        preference_list = preferences or []
        meals: list[Meal] = []
        premium_budget = daily_budget_limit >= 1600
        luxury_budget = daily_budget_limit >= 2500
        budget_tight = daily_budget_limit < 700
        budget_very_tight = daily_budget_limit < 520
        budget_critical = daily_budget_limit < 380
        breakfast_cost = (
            58 if luxury_budget
            else 42 if premium_budget
            else 12 if budget_critical
            else 15 if budget_very_tight
            else 20 if budget_tight
            else 30
        )
        lunch_cost = (
            168 if luxury_budget
            else 118 if premium_budget
            else 25 if budget_critical
            else 35 if budget_very_tight
            else 45 if budget_tight
            else 60
        )
        dinner_cost = (
            238 if luxury_budget
            else 168 if premium_budget
            else 35 if budget_critical
            else 50 if budget_very_tight
            else 70 if budget_tight
            else 90
        )
        first_attraction = daily_attractions[0]
        meals.append(Meal(
            type="breakfast",
            name=f"{city}风味早餐",
            address=first_attraction.address,
            location=first_attraction.location,
            description=(
                "严格控预算，优先便利店早餐或社区早餐铺" if budget_critical
                else "早餐升级为更完整的本地特色体验" if luxury_budget
                else "早餐适度升级，兼顾舒适度和体验" if premium_budget
                else "控制预算，选择高性价比早餐" if budget_tight
                else "轻量开启当天行程"
            ),
            estimated_cost=breakfast_cost,
        ))
        wants_food_preference = "美食" in preference_list
        meals.append(Meal(
            type="lunch",
            name="本地特色午餐" if wants_food_preference else "简洁午餐",
            address=first_attraction.address,
            location=first_attraction.location,
            description=(
                "午餐升级为高口碑本地特色餐厅" if wants_food_preference and luxury_budget
                else "优先安排口碑较好的特色餐厅" if wants_food_preference and premium_budget
                else "优先选择口碑稳定的热门店铺" if wants_food_preference and not budget_tight
                else "严格控预算，安排基础简餐" if budget_critical
                else "控制节奏和预算，安排稳定简餐"
            ),
            estimated_cost=lunch_cost,
        ))
        avoid_seafood = constraints is not None and constraints.avoid_seafood
        dinner_description = (
            "避开海鲜类餐厅，优先选择本地家常菜或小吃集合店" if avoid_seafood
            else "结合景点周边安排本地特色美食体验"
        )
        if budget_critical:
            dinner_description = "严格控预算，晚餐优先安排平价本地简餐"
        elif luxury_budget:
            dinner_description = (
                "避开海鲜前提下，晚餐升级为更完整的本地特色餐饮体验" if avoid_seafood
                else "晚餐升级为更完整的城市代表性美食体验"
            )
        elif premium_budget:
            dinner_description = (
                "避开海鲜前提下，晚餐优先安排更舒适的本地餐厅" if avoid_seafood
                else "晚餐优先安排环境和口碑都更好的本地餐厅"
            )
        dinner_attraction = daily_attractions[len(daily_attractions) - 1]
        meals.append(Meal(
            type="dinner",
            name="晚餐推荐",
            address=dinner_attraction.address,
            location=dinner_attraction.location,
            description=dinner_description,
            estimated_cost=dinner_cost,
        ))
        return meals

    def _build_budget(self, day_plans: list[DayPlan], requested_budget: int) -> Budget:
        total_attractions = 0
        total_hotels = 0
        total_meals = 0
        total_transportation = 0
        for day_plan in day_plans:
            total_attractions += sum(attraction.ticket_price for attraction in day_plan.attractions or [])
            total_meals += sum(meal.estimated_cost for meal in day_plan.meals or [])
            total_transportation += self._estimate_transportation_cost(day_plan.route_plan)
            if day_plan.hotel is not None:
                total_hotels += day_plan.hotel.estimated_cost
        total = total_attractions + total_hotels + total_meals + total_transportation
        remaining_budget = requested_budget - total
        within_budget = remaining_budget >= 0
        return Budget(
            total_attractions=total_attractions,
            total_hotels=total_hotels,
            total_meals=total_meals,
            total_transportation=total_transportation,
            total=total,
            requested_budget=requested_budget,
            remaining_budget=remaining_budget,
            status="within_budget" if within_budget else "over_budget",
            within_budget=within_budget,
        )

    def _build_overall_suggestions(self,
                                   request: TripPlanRequest,
                                   weather_info: list[WeatherInfo],
                                   planning_notes: list[str],
                                   day_plans: list[DayPlan]) -> str:
        weather = (
            "天气信息待补充" if not weather_info
            else f"{weather_info[0].day_weather}，白天 {weather_info[0].day_temp}°C"
        )
        route_minutes = sum(
            day_plan.route_plan.total_duration_minutes
            for day_plan in day_plans
            if day_plan.route_plan is not None
        )
        builder = (
            f"{request.city} 的 {request.calculate_days()} 日行程已生成。"
            f"优先考虑{self._join_preferences(request.preferences)}，当前首日天气为{weather}。"
            f"建议结合预算 {request.budget} 元和住宿偏好 {request.accommodation} 灵活调整节奏，"
            f"预计总通勤约 {route_minutes} 分钟。"
        )
        if planning_notes:
            builder += f" 规划备注：{planning_notes[len(planning_notes) - 1]}"
        return builder

    def _join_preferences(self, preferences: list[str] | None) -> str:
        return "、".join(preferences or [])

    def _join_attraction_names(self, attractions: list[Attraction] | None) -> str:
        if not attractions:
            return "无"
        return "、".join(attraction.name for attraction in attractions)

    def _safe_hotel_name(self, hotel: Hotel | None) -> str:
        return "未选择酒店" if hotel is None else hotel.name

    def _narrow_hotel_candidates(self,
                                 hotels: list[Hotel],
                                 anchor: Location | None,
                                 anchor_label: str | None,
                                 anchor_notes: list[str]) -> list[Hotel]:
        """用户指定了住宿位置时，先圈掉远处、再按远近分档，同档内才交给三项打分。

        只改"谁有资格进打分"，不改分数本身：加一个"离锚点多少公里"的分数项会和
        "离当天景点多少公里"抢权重（两者同单位，且前者跨天不变、后者每天重算），
        倍数小了等于没写、大了就把预算那一维碾掉。详见 docs/ADJUST_AGENT_DESIGN.md 9.3。
        """
        if anchor is None or not hotels:
            return hotels

        inside = [
            hotel for hotel in hotels
            if hotel.location is None
            or self._calculate_distance_km(hotel.location, anchor) <= self._anchor_settings.radius_km
        ]
        named = [hotel for hotel in inside if hotel.location is not None]
        if not named:
            anchor_notes.append(
                f"「{anchor_label or '指定位置'}」{self._anchor_settings.radius_km:.0f} 公里内没有候选酒店，"
                "已按全城候选继续"
                if any(hotel.location is not None for hotel in hotels)
                else "候选酒店都没有坐标，无法按位置筛住宿，已按全城候选继续"
            )
            return hotels

        for tier in (
            [hotel for hotel in inside if self._anchor_tier(hotel, anchor) == 0],
            [hotel for hotel in inside if self._anchor_tier(hotel, anchor) == 1],
        ):
            if tier:
                return tier
        return inside

    def _anchor_tier(self, hotel: Hotel, anchor: Location) -> int:
        """0＝贴得很近（含没有坐标、无法判断的），1＝中等，2＝圈内边缘。"""
        if hotel.location is None:
            return 0
        distance = self._calculate_distance_km(hotel.location, anchor)
        if distance <= self._anchor_settings.near_km:
            return 0
        if distance <= self._anchor_settings.mid_km:
            return 1
        return 2

    def _locked_hotel_note(self,
                           locked_hotel: Hotel,
                           hotels: list[Hotel],
                           day_attractions: list[Attraction],
                           day_anchor_label: str | None,
                           day_index: int) -> str:
        """锁同一家时把代价明写出来：它离当天景点可能比别家远不少。

        用公里差而不是通勤分钟，因为分钟差要再发一轮路线请求（占高德每秒配额、可能撞 CUQPS），
        为一句备注付这个成本不值。
        """
        label = self._safe_hotel_name(locked_hotel)
        if day_anchor_label:
            return (f"已锁定第 1 天的酒店「{label}」，"
                    f"第 {day_index + 1} 天指定的住宿位置「{day_anchor_label}」未生效")
        center = self._calculate_center(day_attractions)
        if center is None or locked_hotel.location is None:
            return f"第 {day_index + 1} 天沿用第 1 天的酒店「{label}」（整趟住同一家）"
        locked_km = self._calculate_distance_km(locked_hotel.location, center)
        reachable = [self._calculate_distance_km(hotel.location, center)
                     for hotel in hotels if hotel.location is not None]
        if not reachable:
            return f"第 {day_index + 1} 天沿用第 1 天的酒店「{label}」（整趟住同一家）"
        nearest_km = min(reachable)
        if locked_km - nearest_km < 1.0:
            return f"第 {day_index + 1} 天沿用第 1 天的酒店「{label}」（整趟住同一家）"
        return (f"已锁定整趟住同一家「{label}」，第 {day_index + 1} 天它离当天景点中心 "
                f"{locked_km:.1f} 公里，比最近的一家远 {locked_km - nearest_km:.1f} 公里")

    def _select_hotel(self, hotels: list[Hotel] | None, attractions: list[Attraction], daily_budget_limit: int) -> Hotel | None:
        if not hotels:
            return None
        attraction_center = self._calculate_center(attractions)
        best_hotel = hotels[0]
        best_score = float("inf")

        for hotel in hotels:
            budget_penalty = self._resolve_hotel_budget_penalty(hotel, daily_budget_limit)
            distance_penalty = (
                0.0
                if attraction_center is None or hotel.location is None
                else self._calculate_distance_km(hotel.location, attraction_center)
            )
            quality_adjustment = self._resolve_hotel_quality_adjustment(hotel, daily_budget_limit)
            score = budget_penalty + distance_penalty + quality_adjustment
            if score < best_score:
                best_score = score
                best_hotel = hotel
        return best_hotel

    def _resolve_hotel_budget_penalty(self, hotel: Hotel, daily_budget_limit: int) -> float:
        hotel_cost = hotel.estimated_cost
        if daily_budget_limit <= 380:
            return 50.0 if hotel_cost > daily_budget_limit * 0.42 else 0.0
        if daily_budget_limit <= 520:
            return 24.0 if hotel_cost > daily_budget_limit * 0.48 else 0.0
        if daily_budget_limit <= 700:
            return 12.0 if hotel_cost > daily_budget_limit * 0.55 else 0.0
        return 8.0 if hotel_cost > daily_budget_limit * 0.55 else 0.0

    def _resolve_hotel_quality_adjustment(self, hotel: Hotel | None, daily_budget_limit: int) -> float:
        if hotel is None:
            return 0.0
        rating_score = self._parse_hotel_rating_score(hotel)
        if daily_budget_limit >= 2500:
            cost_bonus = hotel.estimated_cost * -0.004 if hotel.estimated_cost <= daily_budget_limit * 0.7 else 0.0
            return cost_bonus - rating_score * 6.0
        if daily_budget_limit >= 1600:
            cost_bonus = hotel.estimated_cost * -0.002 if hotel.estimated_cost <= daily_budget_limit * 0.6 else 0.0
            return cost_bonus - rating_score * 3.5
        return 0.0

    def _parse_hotel_rating_score(self, hotel: Hotel) -> float:
        if hotel.rating is None or not hotel.rating.strip():
            return 4.0
        try:
            return float(hotel.rating)
        except ValueError:
            return 4.0

    def _estimate_daily_cost(self,
                             attractions: list[Attraction],
                             hotel: Hotel | None,
                             meals: list[Meal],
                             route_plan: RoutePlan | None) -> int:
        ticket_cost = sum(attraction.ticket_price for attraction in attractions)
        hotel_cost = self._estimate_hotel_cost(hotel)
        meal_cost = sum(meal.estimated_cost for meal in meals)
        transport_cost = self._estimate_transportation_cost(route_plan)
        return ticket_cost + hotel_cost + meal_cost + transport_cost

    def _estimate_transportation_cost(self, route_plan: RoutePlan | None) -> int:
        if route_plan is None:
            return 120
        distance = route_plan.total_distance_km
        mode = route_plan.transportation_mode
        if mode is not None and "步行" in mode:
            return 20
        if mode is not None and "打车" in mode:
            return max(60, jround(distance * 9))
        return max(35, jround(distance * 4.5))

    def _estimate_hotel_cost(self, hotel: Hotel | None) -> int:
        return 0 if hotel is None else hotel.estimated_cost

    def _contains_attraction(self, attractions: list[Attraction], attraction_name: str) -> bool:
        return any(attraction.name == attraction_name for attraction in attractions)

    def _sanitize_attractions(self, attractions: list[Attraction]) -> list[Attraction]:
        scenic_attractions: list[Attraction] = []
        for attraction in attractions:
            if attraction.location is None:
                continue
            if self._is_food_like_attraction(attraction) or self._is_invalid_attraction(attraction):
                continue
            scenic_attractions.append(attraction)
        if len(scenic_attractions) <= 3:
            return scenic_attractions or attractions

        center = self._calculate_center(scenic_attractions)
        clustered_attractions = [
            attraction
            for attraction in scenic_attractions
            if center is None or self._calculate_distance_km(attraction.location, center) <= 35
        ]
        return clustered_attractions if len(clustered_attractions) >= 3 else scenic_attractions

    def _filter_nearby_attractions(self, attractions: list[Attraction], hotel: Hotel | None) -> list[Attraction]:
        if hotel is None or hotel.location is None:
            return attractions
        nearby_attractions = [
            attraction
            for attraction in attractions
            if attraction.location is not None
            and self._calculate_distance_km(hotel.location, attraction.location) <= _ATTRACTION_HOTEL_RADIUS_KM
        ]
        return nearby_attractions if len(nearby_attractions) >= 2 else attractions

    def _is_reasonably_close(self, selected: list[Attraction], candidate: Attraction) -> bool:
        if not selected or candidate.location is None:
            return True
        for attraction in selected:
            if attraction.location is not None and self._calculate_distance_km(attraction.location, candidate.location) > 30:
                return False
        return True

    def _is_food_like_attraction(self, attraction: Attraction) -> bool:
        category = attraction.category or ""
        description = attraction.description or ""
        return (
            "美食" in category
            or "餐饮服务" in description
            or "中餐厅" in description
            or "咖啡厅" in description
            or "火锅" in description
            or "烧烤" in description
        )

    def _is_invalid_attraction(self, attraction: Attraction) -> bool:
        name = attraction.name or ""
        description = attraction.description or ""
        return (
            "公司企业" in description
            or "金融保险服务" in description
            or "汽车服务" in description
            or "商务住宅" in description
            or "政府机构及社会团体" in description
            or "公司" in name
            or "有限公司" in name
        )

    def _calculate_center(self, attractions: list[Attraction]) -> Location | None:
        longitude_sum = 0.0
        latitude_sum = 0.0
        count = 0
        for attraction in attractions:
            if attraction.location is None:
                continue
            longitude_sum += attraction.location.longitude
            latitude_sum += attraction.location.latitude
            count += 1
        if count == 0:
            return None
        return Location(longitude=longitude_sum / count, latitude=latitude_sum / count)

    def _calculate_distance_km(self, first: Location, second: Location) -> float:
        latitude_distance = math.radians(second.latitude - first.latitude)
        longitude_distance = math.radians(second.longitude - first.longitude)
        start_latitude = math.radians(first.latitude)
        end_latitude = math.radians(second.latitude)

        a = (math.sin(latitude_distance / 2) * math.sin(latitude_distance / 2)
             + math.cos(start_latitude) * math.cos(end_latitude)
             * math.sin(longitude_distance / 2) * math.sin(longitude_distance / 2))
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
        return 6371.0 * c

    def _remove_by_identity(self, attractions: list[Attraction], target: Attraction) -> None:
        for index, attraction in enumerate(attractions):
            if attraction is target:
                del attractions[index]
                return
