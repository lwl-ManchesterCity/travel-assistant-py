"""对应 Java agent/LlmCriticAgent.java：让模型批评当前方案，失败或不可用时走规则批评兜底。

Java 用注入的 ObjectMapper 把 request/reflection/plan 以 writerWithDefaultPrettyPrinter 打进提示词，
Python 侧等价用 json.dumps(indent=2, ensure_ascii=False)；两边缩进与冒号空格的差异记在
MIGRATION_NOTES.md，不影响批评动作的判定。
"""

import json

from ..clients.llm import LlmClient
from ..models.loop_critique import LoopCritique
from ..models.trip_plan import TripPlan
from ..models.trip_plan_reflection import TripPlanReflection
from ..models.trip_plan_request import TripPlanRequest
from ..utils import node_as_text

_CRITIC_ACTIONS = ("REDUCE_BUDGET", "COMPRESS_ROUTE", "REPLACE_OUTDOOR_WITH_INDOOR", "UPGRADE_EXPERIENCE", "STOP")
_RISKY_WEATHER_KEYWORDS = ("雨", "雪", "雷")

_SYSTEM_PROMPT = """你是旅行规划批评智能体。请根据当前旅行方案，判断是否需要再规划一轮。
你必须只返回 JSON，不要返回 Markdown、解释或代码块。
JSON 格式：
{
  "action": string,
  "reason": string,
  "expectedOutcome": string,
  "notes": string[]
}
规则：
1. action 只能从以下枚举中选一个：
   REDUCE_BUDGET
   COMPRESS_ROUTE
   REPLACE_OUTDOOR_WITH_INDOOR
   UPGRADE_EXPERIENCE
   STOP
2. 如果预算明显超支，优先 REDUCE_BUDGET。
3. 如果预算剩余很多且体验明显偏保守，可考虑 UPGRADE_EXPERIENCE。
4. 如果天气恶劣且需要室内备选，可考虑 REPLACE_OUTDOOR_WITH_INDOOR。
5. 如果总通勤过长，可考虑 COMPRESS_ROUTE。
6. 如果当前方案已经足够合理，返回 STOP。
7. 请重点参考 reflection 中的 budgetSeverity、routeSeverity、weatherSeverity、experienceSeverity、overallScore。
8. notes 用简短中文说明批评依据。
"""


class LlmCriticAgent:

    def __init__(self, llm_client: LlmClient):
        self._llm_client = llm_client

    def critique(self,
                 request: TripPlanRequest,
                 plan: TripPlan,
                 reflection: TripPlanReflection | None) -> LoopCritique:
        if self._llm_client.is_available():
            try:
                return self._critique_by_llm(request, plan, reflection)
            except Exception:  # noqa: BLE001 - 对齐 Java：批评阶段任何失败都回退规则批评
                fallback = self._critique_by_rules(request, plan, reflection)
                fallback.source = "rule-fallback"
                notes = [] if fallback.notes is None else list(fallback.notes)
                notes.append("LLM 批评阶段失败，已回退到规则批评")
                fallback.notes = notes
                return fallback
        return self._critique_by_rules(request, plan, reflection)

    def _critique_by_llm(self,
                         request: TripPlanRequest,
                         plan: TripPlan,
                         reflection: TripPlanReflection | None) -> LoopCritique:
        content = self._llm_client.chat(_SYSTEM_PROMPT, self._build_user_prompt(request, plan, reflection))
        node = json.loads(content)
        return LoopCritique(
            action=self._normalize_action(_path_as_text(node, "action", "STOP")),
            reason=_path_as_text(node, "reason", "LLM 未给出原因"),
            expected_outcome=_path_as_text(node, "expectedOutcome", "维持当前方案"),
            source="llm",
            notes=_read_string_list(node.get("notes") if isinstance(node, dict) else None),
        )

    def _build_user_prompt(self,
                           request: TripPlanRequest,
                           plan: TripPlan,
                           reflection: TripPlanReflection | None) -> str:
        return ("用户请求：\n"
                + _pretty_json(request)
                + "\n\n规则评估结果：\n"
                + _pretty_json(reflection)
                + "\n\n当前方案：\n"
                + _pretty_json(plan))

    def _critique_by_rules(self,
                           request: TripPlanRequest,
                           plan: TripPlan,
                           reflection: TripPlanReflection | None) -> LoopCritique:
        total_duration_minutes = 0
        rainy_weather = False
        if plan.days is not None:
            for day_plan in plan.days:
                if day_plan.route_plan is not None:
                    total_duration_minutes += day_plan.route_plan.total_duration_minutes
        if plan.weather_info is not None:
            for weather_info in plan.weather_info:
                if _is_risky_weather(weather_info.day_weather):
                    rainy_weather = True
                    break

        over_budget = (
            reflection.over_budget if reflection is not None
            else plan.budget is not None and not plan.budget.within_budget
        )
        if reflection is not None:
            experience_too_conservative = reflection.experience_severity > 0
        else:
            experience_too_conservative = (
                plan.budget is not None
                and plan.budget.within_budget
                and request.budget >= 5000
                and plan.budget.remaining_budget > max(1800, request.budget // 3)
            )
        if reflection is not None:
            route_too_long = reflection.route_too_long
        else:
            route_too_long = (
                plan.days is not None
                and len(plan.days) > 0
                and total_duration_minutes > len(plan.days) * 180
            )
        if reflection is not None and reflection.indoor_adjustment_recommended:
            rainy_weather = True
        budget_severity = 0 if reflection is None else reflection.budget_severity
        route_severity = 0 if reflection is None else reflection.route_severity
        weather_severity = 0 if reflection is None else reflection.weather_severity
        experience_severity = 0 if reflection is None else reflection.experience_severity
        overall_score = 0 if reflection is None else reflection.overall_score

        notes: list[str] = []
        if over_budget:
            notes.append(f"当前总预算超支 {abs(plan.budget.remaining_budget)} 元")
        if experience_too_conservative:
            notes.append("预算结余较多，当前方案体验偏保守")
        if route_too_long:
            notes.append(f"总通勤时长达到 {total_duration_minutes} 分钟")
        if rainy_weather:
            notes.append("出行期存在降雨天气，可准备室内备选")
        if reflection is not None:
            notes.append(
                f"评分摘要：overall={overall_score}"
                f"，budget={budget_severity}"
                f"，route={route_severity}"
                f"，weather={weather_severity}"
                f"，experience={experience_severity}"
            )

        if reflection is not None and reflection.acceptable:
            action = "STOP"
            reason = "规则评分已处于可接受范围，无需继续重规划"
            expected_outcome = "保留当前方案作为最终结果"
        elif (budget_severity >= route_severity and budget_severity >= weather_severity
                and budget_severity >= experience_severity and budget_severity > 0):
            action = "REDUCE_BUDGET"
            reason = "当前方案预算严重度最高，建议优先压缩成本后重规划"
            expected_outcome = "通过降低住宿和交通成本，让总费用回到预算范围附近"
        elif (route_severity >= budget_severity and route_severity >= weather_severity
                and route_severity >= experience_severity and route_severity > 0):
            action = "COMPRESS_ROUTE"
            reason = "当前方案路线严重度最高，建议优先压缩跨区通勤"
            expected_outcome = "减少跨区折返，让总通勤时长明显下降"
        elif (weather_severity >= budget_severity and weather_severity >= route_severity
                and weather_severity >= experience_severity and weather_severity > 0):
            action = "REPLACE_OUTDOOR_WITH_INDOOR"
            reason = "当前方案天气风险严重度最高，建议增加室内备选"
            expected_outcome = "降低天气波动对行程体验的影响"
        elif experience_too_conservative:
            action = "UPGRADE_EXPERIENCE"
            reason = "当前方案体验提升严重度最高，建议适度提升住宿或体验强度"
            expected_outcome = "适度提升住宿和行程体验，减少预算浪费"
        else:
            action = "STOP"
            reason = "当前方案整体可接受，无需进入下一轮"
            expected_outcome = "保留当前方案作为最终结果"

        return LoopCritique(
            action=action,
            reason=reason,
            expected_outcome=expected_outcome,
            source="rule",
            notes=notes,
        )

    def _normalize_action(self, action: str | None) -> str:
        if action is None or not action.strip():
            return "STOP"
        normalized = action.strip().upper()
        return normalized if normalized in _CRITIC_ACTIONS else "STOP"


def _is_risky_weather(day_weather: str | None) -> bool:
    return day_weather is not None and any(keyword in day_weather for keyword in _RISKY_WEATHER_KEYWORDS)


def _pretty_json(model) -> str:
    if model is None:
        return "null"
    return json.dumps(model.model_dump(mode="json"), indent=2, ensure_ascii=False)


def _path_as_text(node: object, name: str, default: str) -> str:
    if not isinstance(node, dict) or name not in node:
        return default
    return node_as_text(node[name], default)


def _read_string_list(value: object) -> list[str]:
    values: list[str] = []
    if not isinstance(value, list):
        return values
    for item in value:
        text = node_as_text(item, "")
        if text.strip():
            values.append(text)
    return values
