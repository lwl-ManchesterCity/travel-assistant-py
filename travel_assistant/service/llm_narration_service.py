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

    def order_attractions(self, attractions: list[Attraction], weather: WeatherInfo | None) -> list[Attraction]:
        """给"某天必去景点"排游玩先后：让模型按天气(雨天先室内)/少折返给序；不可用或输出异常回落原序。

        只重排、不增删——集合内容与传入完全一致。
        """
        if len(attractions) < 2:
            return list(attractions)
        ordered = self._order_by_llm(attractions, weather)
        return ordered if ordered is not None else list(attractions)

    def _order_by_llm(self, attractions: list[Attraction], weather: WeatherInfo | None) -> list[Attraction] | None:
        if not self.is_available():
            return None
        names = [a.name for a in attractions]
        weather_text = "未知" if weather is None else f"{weather.day_weather} {weather.day_temp}℃"
        listing = "；".join(f"{a.name}[{a.category or '景点'}]" for a in attractions)
        prompt = (
            "给这一天必去的景点排一个游玩先后顺序，规则：雨天/高温优先把室内类排前面，整体少折返。\n"
            f"当日天气：{weather_text}\n"
            f"景点（含类别）：{listing}\n"
            f"只输出这些景点名、用|分隔、每个恰好一次：{'、'.join(names)}"
        )
        try:
            result = self._llm_client.chat("你是行程顺序助手，只输出景点名序列，不要解释。", prompt)
        except Exception:  # noqa: BLE001 - 排序失败回落原序
            return None
        if not result or not result.strip():
            return None
        by_name = {a.name: a for a in attractions}
        ordered: list[Attraction] = []
        for token in re.split(r"[|,，、;；\n]", result):
            attraction = by_name.get(token.strip())
            if attraction is not None and attraction not in ordered:
                ordered.append(attraction)
        for attraction in attractions:
            if attraction not in ordered:
                ordered.append(attraction)
        return ordered if len(ordered) == len(attractions) else None

    def _build_day_prompt(self, request: TripPlanRequest, day_plan: DayPlan) -> str:
        builder = (
            "你是旅游行程文案助手。请把下面这一天的规则规划结果，改写成自然、简洁、有一点智能体感的中文描述。\n"
            "要求：\n"
            "1. 只输出一段中文，不要标题，不要 Markdown。\n"
            "2. 长度控制在 80 到 140 字。\n"
            "3. 要保留核心事实：第几天、核心景点、预算、路线/节奏/天气相关提示。\n"
            "4. 不要编造不存在的信息。\n\n"
        )
        builder += f"用户请求：{self._safe(request.city)}，预算 {request.budget} 元，偏好 {java_list_to_string(request.preferences)}。\n"
        builder += f"日期：{self._safe(day_plan.date)}\n"
        builder += f"原始描述：{self._safe(day_plan.description)}\n"
        builder += f"交通方式：{self._safe(day_plan.transportation)}\n"
        builder += f"酒店：{'无' if day_plan.hotel is None else self._safe(day_plan.hotel.name)}\n"
        builder += f"景点：{self._join_attraction_names(day_plan)}\n"
        builder += f"当日预计花费：{day_plan.estimated_cost} 元\n"
        route_plan = day_plan.route_plan
        if route_plan is not None:
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
