"""对应 Java service/LlmNarrationService.java：只润色文案，不改变任何结构化结果。

一处扩展（2026-10-09，见 ATTRACTION_PIN_DESIGN.md）：`order_attractions` 会让模型给"某天必去景点"
排游玩顺序。它只重排、绝不增删景点，且模型不可用/输出异常一律回落成原顺序——所以"不改变结构化结果"
这条对内容仍然成立。
"""

import re

from ..clients.llm import LlmClient
from ..models.attraction import Attraction
from ..models.day_plan import DayPlan
from ..models.trip_plan_request import TripPlanRequest
from ..models.weather_info import WeatherInfo
from ..utils import java_list_to_string

_SYSTEM_PROMPT = "你是一个中文旅游规划文案助手。输出尽量自然、准确、简洁。"


class LlmNarrationService:

    def __init__(self, llm_client: LlmClient):
        self._llm_client = llm_client

    def is_available(self) -> bool:
        return self._llm_client.is_available()

    def polish_day_description(self, request: TripPlanRequest, day_plan: DayPlan) -> str:
        fallback = day_plan.description
        if not self.is_available():
            return fallback
        try:
            prompt = self._build_day_prompt(request, day_plan)
            result = self._llm_client.chat(_SYSTEM_PROMPT, prompt)
            return fallback if result is None or not result.strip() else result.strip()
        except Exception:  # noqa: BLE001 - 文案失败必须退回规则文案
            return fallback

    def polish_overall_suggestions(self,
                                   request: TripPlanRequest,
                                   weather_info: list[WeatherInfo],
                                   day_plans: list[DayPlan],
                                   planning_notes: list[str],
                                   fallback: str) -> str:
        if not self.is_available():
            return fallback
        try:
            prompt = self._build_overall_prompt(request, weather_info, day_plans, planning_notes, fallback)
            result = self._llm_client.chat(_SYSTEM_PROMPT, prompt)
            return fallback if result is None or not result.strip() else result.strip()
        except Exception:  # noqa: BLE001
            return fallback

    _TIME_SLOT_RULE = (
        "时段常识（这是写死的常识，不是联网查来的资料）：\n"
        "1. 爬山、公园、湖这类户外：上午更合适，凉快、光线好。\n"
        "2. 观景塔、摩天轮、夜景这类：傍晚更合适，能一次看到白天、日落和亮灯。\n"
        "3. 博物馆、寺庙、商圈这类室内：正午或下雨时更合适。\n"
    )

    def choose_attraction_order(self,
                                candidates: list[list[Attraction]],
                                costs_km: list[float],
                                weather: WeatherInfo | None,
                                endpoint_label: str | None) -> tuple[list[Attraction], str] | None:
        """在"里程差不多"的几个候选游玩顺序里挑一个，返回（顺序，一句理由）。

        调用方只在并列时才走到这里——比最省路的那条多绕超过容差的候选早被筛掉了，
        所以规则写死成"默认取编号 1（最省路），只有时段明显排反了才允许换"。
        模型不可用或输出不合规就返回 None，由调用方按同一张常识表自己排，不联网也就不存在查不到。
        """
        if not self.is_available() or not candidates:
            return None
        weather_text = "未知" if weather is None else f"{weather.day_weather} {weather.day_temp}℃"
        lines = [
            f"{index}. {' → '.join(attraction.name or '' for attraction in order)}，全程约 {km:.1f} 公里"
            for index, (order, km) in enumerate(zip(candidates, costs_km), start=1)
        ]
        prompt = (
            "给这一天点名的必去景点挑一个游玩先后顺序。候选已按里程从省到费排好，编号 1 就是最省路的那条。\n"
            "规则（按优先级，不许破）：\n"
            "1. 默认选编号 1。只有当编号 1 把更适合上午的排到了后面、或把更适合傍晚看夜景的排到了前面，"
            "而某个候选正好排对了，才允许改选那条。\n"
            "2. 不许自己估距离，也不许提出候选之外的顺序。\n"
            f"{self._TIME_SLOT_RULE}"
            f"当日天气：{weather_text}\n"
            f"当天收工后要回的地方：{endpoint_label or '未定（酒店跟着最后一站走）'}\n"
            "候选：\n" + "\n".join(lines) + "\n"
            "只输出一行：编号|一句话理由（不超过 30 字）"
        )
        try:
            result = self._llm_client.chat("你是行程顺序助手，只按候选编号作答。", prompt)
        except Exception:  # noqa: BLE001 - 判不了就交回调用方按常识表排
            return None
        return self._parse_choice(result, candidates)

    def _parse_choice(self,
                      raw: str | None,
                      candidates: list[list[Attraction]]) -> tuple[list[Attraction], str] | None:
        text = (raw or "").strip()
        if not text:
            return None
        head, _, reason = text.partition("|")
        digits = re.findall(r"\d+", head)
        if not digits:
            return None
        index = int(digits[0]) - 1
        if index < 0 or index >= len(candidates):
            return None
        return candidates[index], reason.strip()[:60]

    def _build_day_prompt(self, request: TripPlanRequest, day_plan: DayPlan) -> str:
        builder = (
            "你是旅游行程文案助手。请把下面这一天的规则规划结果，改写成自然、简洁、有一点智能体感的中文描述。\n"
            "要求：\n"
            "1. 只输出一段中文，不要标题，不要 Markdown。\n"
            "2. 长度控制在 80 到 140 字。\n"
            "3. 要保留核心事实：第几天、核心景点、预算、路线/节奏/天气相关提示。\n"
            "4. 不要编造不存在的信息。\n"
            "5. 这天从哪儿开始走、到哪儿收工，一律照下面给的\"当天起点\"\"当天收工\"写，"
            "不要自己推断，尤其不要写成\"建议从某酒店乘公共交通出发\"——游客是自行到达首站的。\n\n"
        )
        builder += f"用户请求：{self._safe(request.city)}，预算 {request.budget} 元，偏好 {java_list_to_string(request.preferences)}。\n"
        builder += f"日期：{self._safe(day_plan.date)}\n"
        builder += f"原始描述：{self._safe(day_plan.description)}\n"
        builder += f"交通方式：{self._safe(day_plan.transportation)}\n"
        builder += f"当晚住宿：{'无' if day_plan.hotel is None else self._safe(day_plan.hotel.name)}\n"
        builder += f"景点：{self._join_attraction_names(day_plan)}\n"
        builder += f"当日预计花费：{day_plan.estimated_cost} 元\n"
        route_plan = day_plan.route_plan
        if route_plan is not None:
            builder += (f"当天起点：{self._safe(route_plan.start_point.name if route_plan.start_point else None)}\n")
            builder += (f"当天收工：{self._safe(route_plan.end_point.name if route_plan.end_point else None)}\n")
            builder += f"路线摘要：{self._safe(route_plan.summary)}\n"
        if day_plan.optimization_notes:
            builder += f"自动优化说明：{java_list_to_string(day_plan.optimization_notes)}\n"
        return builder

    def _build_overall_prompt(self,
                              request: TripPlanRequest,
                              weather_info: list[WeatherInfo],
                              day_plans: list[DayPlan],
                              planning_notes: list[str],
                              fallback: str) -> str:
        builder = (
            "你是旅游计划总览文案助手。请把下面的规则规划总结改写成简洁、自然、有智能规划感的中文总览。\n"
            "要求：\n"
            "1. 只输出一段中文，不要标题，不要 Markdown。\n"
            "2. 长度控制在 120 到 200 字。\n"
            "3. 要点要包含：城市、天数、偏好、首日天气、预算、住宿偏好、整体节奏或路线提示。\n"
            "4. 不要编造不存在的信息。\n\n"
        )
        builder += f"城市：{self._safe(request.city)}\n"
        builder += f"日期：{self._safe(request.start_date)} 至 {self._safe(request.end_date)}\n"
        builder += f"预算：{request.budget} 元\n"
        builder += f"住宿偏好：{self._safe(request.accommodation)}\n"
        builder += f"偏好：{java_list_to_string(request.preferences)}\n"
        builder += (
            f"首日天气：{'未知' if not weather_info else self._safe(weather_info[0].day_weather)}\n"
        )
        builder += f"总天数：{0 if day_plans is None else len(day_plans)}\n"
        if planning_notes:
            builder += f"规划备注：{java_list_to_string(planning_notes)}\n"
        builder += f"原始总结：{self._safe(fallback)}\n"
        return builder

    def _join_attraction_names(self, day_plan: DayPlan) -> str:
        if not day_plan.attractions:
            return "无"
        return "、".join(attraction.name for attraction in day_plan.attractions)

    def _safe(self, value: str | None) -> str:
        return "" if value is None else value
