"""对应 Java service/RequirementAnalysisService.java：模型只做需求理解，失败即回到规则关键词。"""

import json

from ..clients.llm import LlmClient
from ..models.planning_constraints import PlanningConstraints
from ..models.trip_plan_request import TripPlanRequest
from ..utils import java_list_to_string
from .preference_service import build_injection_block
from .request_optimization_strategy_service import strip_system_requirements

_SYSTEM_PROMPT = """你是旅行需求理解助手。你的任务是把用户的旅行请求解析成结构化约束 JSON。
你必须只返回 JSON，不要返回解释、Markdown 或代码块。
JSON 字段固定为：
{
  "relaxedPace": boolean,
  "avoidSeafood": boolean,
  "wantsLandmarkCeremony": boolean,
  "prefersIndoorBackup": boolean,
  "extractedTags": string[],
  "extractedPreferences": string[]
}
字段含义：
relaxedPace：用户希望慢节奏、轻松、不要太赶。
avoidSeafood：用户不吃海鲜、海鲜过敏、避开海鲜。
wantsLandmarkCeremony：用户提到升旗、仪式感、地标打卡等。
prefersIndoorBackup：用户担心下雨、高温、天气不稳定，或希望室内备选。
extractedTags 用简短中文标签总结命中的约束。
extractedPreferences 从「额外要求」里挑出**关于用户本人**的长期偏好，会存下来、
影响他以后每一趟。判断标准是「这个人的」还是「这一趟的」：
  要挑（这个人的）：「对海鲜过敏」「不吃辣」「不吃香菜」「预算敏感」「恐高」
    「带小孩出行」「腿脚不便」「喜欢安静」。
  不挑（这一趟的）：「想去看升旗」「这次轻松点」「多留一天」「想去西湖」。
  **拿不准就按「这一趟的」不挑**——挑错的那条会跟着他每一趟，而他不知道它在那儿。
  只挑**用户本人**的。同行的人自己什么口味、什么状况，不算他的：
    「我老婆不能吃辣」「我朋友对海鲜过敏」「我妈腿脚不好」都整条不挑。
  注意别人的话**不许「去掉主语再挑」**：下面要求你不带主语，而「老婆不能吃辣」
    剥掉主语就是「不能吃辣」，跟用户本人说的**长得一模一样**，你分不出来，
    他以后每一趟也就都被这么带着了——所以只能整条丢。
  分界看是**谁**：同行的人是谁，是他的情况（「带小孩出行」要挑）；
    同行的人**自己**的口味和状况，不是。
  每条只写偏好本身，短，不超过十几个字，不要带主语，不要把整句话抄进来。
  「偏好」那一栏里勾选的类型词（如「人文历史」「自然风景」）**不要挑**，
  那是他这一趟选的，不是他这个人的。
  没有就交空数组，不要为了填而填。
"""



class RequirementAnalysisService:

    def __init__(self, llm_client: LlmClient):
        self._llm_client = llm_client

    def analyze(self, request: TripPlanRequest,
                long_term_preferences: list[str] | None = None) -> PlanningConstraints:
        """解析需求。

        `long_term_preferences` 是这个人的长期偏好**原文**，由调用方（PlannerInputBuilder）
        从 PreferenceService 取好传进来。**这里不自己取**：留痕要写"注入了 N 条"，
        而只有拿到同一个列表，那条留痕才真的证明了注入发生过（§2.2.9 判据 7）。
        """
        if self._llm_client.is_available():
            try:
                return self._analyze_by_llm(request, long_term_preferences)
            except Exception:  # noqa: BLE001 - 与 Java 一致：任何解析失败都退回规则结果
                fallback = self._analyze_by_rules(request)
                fallback.analysis_source = "rule-fallback"
                return fallback
        return self._analyze_by_rules(request)

    def _analyze_by_llm(self, request: TripPlanRequest,
                        long_term_preferences: list[str] | None = None) -> PlanningConstraints:
        content = self._llm_client.chat(
            _SYSTEM_PROMPT, self._build_user_prompt(request, long_term_preferences))
        result = json.loads(content)
        constraints = PlanningConstraints(
            relaxed_pace=self._as_bool(result.get("relaxedPace"), False),
            avoid_seafood=self._as_bool(result.get("avoidSeafood"), False),
            wants_landmark_ceremony=self._as_bool(result.get("wantsLandmarkCeremony"), False),
            prefers_indoor_backup=self._as_bool(result.get("prefersIndoorBackup"), False),
            extracted_tags=self._read_string_list(result.get("extractedTags")),
            extracted_preferences=self._read_preferences(
                result.get("extractedPreferences"), request),
        )
        constraints.analysis_source = "llm"
        if not constraints.extracted_tags:
            constraints.extracted_tags = self._build_tags(constraints)
        return constraints

    def _build_user_prompt(self, request: TripPlanRequest,
                           long_term_preferences: list[str] | None = None) -> str:
        # **喂给模型的额外要求要先洗过**：里面混着策略服务自己追加的句子
        # （「整体控制预算，优先保留高价值景点」等）。它们是"这一次"的调节，
        # 存下来会变成"每一趟都强制压预算"（§2.2.4）。用户原话才进这一格。
        user_requirements = strip_system_requirements(request.extra_requirements)
        lines = [
            f"目的地：{self._safe(request.city)}",
            f"日期：{self._safe(request.start_date)} 至 {self._safe(request.end_date)}",
            f"预算：{request.budget}",
            f"偏好：{java_list_to_string(request.preferences)}",
            f"交通：{self._safe(request.transportation)}",
            f"住宿：{self._safe(request.accommodation)}",
            f"额外要求：{self._safe(user_requirements)}",
        ]
        # 长期偏好**原文**进的是这一格，不是 _SYSTEM_PROMPT：那是模块级常量，
        # 而偏好是每人每趟不同的。规则（判断标准）在系统提示词里，事实（具体哪几条）
        # 在用户提示词里——同一次调用，位置只影响模型怎么读。
        # 这一段里还带着「表单优先」那条纪律（§2.2.6）。
        block = build_injection_block(long_term_preferences or [])
        if block:
            lines.append(block)
        return "\n".join(lines)

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

    def _read_preferences(self, node: object, request: TripPlanRequest) -> list[str]:
        """读 extractedPreferences，并**挡掉表单勾选框的回声**。

        用户提示词里那行「偏好：人文历史、自然风景」是这一次选的类型，模型有可能
        顺手把它们当成"关于这个人的偏好"吐回来。那不是长期偏好——用户自己纠正过
        这条（见 docs/ASK_LONGTERM_MEMORY.md §1.1：写入点只有备注和助手输入两处）。
        精确相等就够：回声必然与勾选框一字不差。
        """
        chosen = {item.strip() for item in (request.preferences or []) if item and item.strip()}
        return [value for value in self._read_string_list(node) if value.strip() not in chosen]

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
