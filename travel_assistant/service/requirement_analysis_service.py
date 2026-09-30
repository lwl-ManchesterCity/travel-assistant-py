"""对应 Java service/RequirementAnalysisService.java：模型只做需求理解，失败即回到规则关键词。"""

import json

from ..clients.llm import LlmClient
from ..models.planning_constraints import PlanningConstraints
from ..models.trip_plan_request import TripPlanRequest
from ..utils import java_list_to_string

_SYSTEM_PROMPT = """你是旅行需求理解助手。你的任务是把用户的旅行请求解析成结构化约束 JSON。
你必须只返回 JSON，不要返回解释、Markdown 或代码块。
JSON 字段固定为：
{
  "relaxedPace": boolean,
  "avoidSeafood": boolean,
  "wantsLandmarkCeremony": boolean,
  "prefersIndoorBackup": boolean,
  "extractedTags": string[]
}
字段含义：
relaxedPace：用户希望慢节奏、轻松、不要太赶。
avoidSeafood：用户不吃海鲜、海鲜过敏、避开海鲜。
wantsLandmarkCeremony：用户提到升旗、仪式感、地标打卡等。
prefersIndoorBackup：用户担心下雨、高温、天气不稳定，或希望室内备选。
extractedTags 用简短中文标签总结命中的约束。
"""


class RequirementAnalysisService:

    def __init__(self, llm_client: LlmClient):
        self._llm_client = llm_client

    def analyze(self, request: TripPlanRequest) -> PlanningConstraints:
        if self._llm_client.is_available():
            try:
                return self._analyze_by_llm(request)
            except Exception:  # noqa: BLE001 - 与 Java 一致：任何解析失败都退回规则结果
                fallback = self._analyze_by_rules(request)
                fallback.analysis_source = "rule-fallback"
                return fallback
        return self._analyze_by_rules(request)

    def _analyze_by_llm(self, request: TripPlanRequest) -> PlanningConstraints:
        content = self._llm_client.chat(_SYSTEM_PROMPT, self._build_user_prompt(request))
        result = json.loads(content)
        constraints = PlanningConstraints(
            relaxed_pace=self._as_bool(result.get("relaxedPace"), False),
            avoid_seafood=self._as_bool(result.get("avoidSeafood"), False),
            wants_landmark_ceremony=self._as_bool(result.get("wantsLandmarkCeremony"), False),
            prefers_indoor_backup=self._as_bool(result.get("prefersIndoorBackup"), False),
            extracted_tags=self._read_string_list(result.get("extractedTags")),
        )
        constraints.analysis_source = "llm"
        if not constraints.extracted_tags:
            constraints.extracted_tags = self._build_tags(constraints)
        return constraints

    def _build_user_prompt(self, request: TripPlanRequest) -> str:
        return (
            f"目的地：{self._safe(request.city)}\n"
            f"日期：{self._safe(request.start_date)} 至 {self._safe(request.end_date)}\n"
            f"预算：{request.budget}\n"
            f"偏好：{java_list_to_string(request.preferences)}\n"
            f"交通：{self._safe(request.transportation)}\n"
            f"住宿：{self._safe(request.accommodation)}\n"
            f"额外要求：{self._safe(request.extra_requirements)}"
        )

    def _analyze_by_rules(self, request: TripPlanRequest) -> PlanningConstraints:
        content = (request.extra_requirements or "").lower()
        preferences = request.preferences or []

        relaxed_pace = "轻松" in content or "慢" in content or "轻松节奏" in preferences
        avoid_seafood = "海鲜过敏" in content or "不吃海鲜" in content
        wants_landmark_ceremony = "升旗" in content or "仪式" in content
        prefers_indoor_backup = "下雨" in content or "室内" in content

        constraints = PlanningConstraints(
            relaxed_pace=relaxed_pace,
            avoid_seafood=avoid_seafood,
            wants_landmark_ceremony=wants_landmark_ceremony,
            prefers_indoor_backup=prefers_indoor_backup,
            extracted_tags=self._build_tags_from_booleans(
                relaxed_pace, avoid_seafood, wants_landmark_ceremony, prefers_indoor_backup
            ),
        )
        constraints.analysis_source = "rule"
        return constraints

    def _build_tags(self, constraints: PlanningConstraints) -> list[str]:
        return self._build_tags_from_booleans(
            constraints.relaxed_pace,
            constraints.avoid_seafood,
            constraints.wants_landmark_ceremony,
            constraints.prefers_indoor_backup,
        )

    def _build_tags_from_booleans(self,
                                  relaxed_pace: bool,
                                  avoid_seafood: bool,
                                  wants_landmark_ceremony: bool,
                                  prefers_indoor_backup: bool) -> list[str]:
        extracted_tags: list[str] = []
        if relaxed_pace:
            extracted_tags.append("轻松节奏")
        if avoid_seafood:
            extracted_tags.append("餐饮避开海鲜")
        if wants_landmark_ceremony:
            extracted_tags.append("优先考虑仪式感景点")
        if prefers_indoor_backup:
            extracted_tags.append("预留室内备选方案")
        return extracted_tags

    def _read_string_list(self, node: object) -> list[str]:
        values: list[str] = []
        if not isinstance(node, list):
            return values
        for item in node:
            value = item if isinstance(item, str) else ("" if isinstance(item, (dict, list)) else str(item))
            if value.strip():
                values.append(value)
        return values

    def _as_bool(self, node: object, default: bool) -> bool:
        if node is None:
            return default
        if isinstance(node, bool):
            return node
        if isinstance(node, (int, float)):
            return node != 0
        if isinstance(node, str):
            return node.strip().lower() in ("true", "t", "1")
        return default

    def _safe(self, value: str | None) -> str:
        return "" if value is None else value
