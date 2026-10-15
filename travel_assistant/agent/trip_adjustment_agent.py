"""把自然语言改计划要求解析成新的请求副本。

模型可用时先让模型输出结构化 JSON，解析过程抛任何异常都回退规则解析并把 adjustmentSource
标成 rule-fallback；模型不可用时直接走规则解析。两条路径都只改请求副本，真正的重新规划
由 TripPlanningService.adjustTrip 调 planTrip（即 LoopPlanningAgent）完成。
"""

import json
import re

from ..clients.llm import LlmClient
from ..models.trip_plan_request import TripPlanRequest
from ..service.request_optimization_strategy_service import RequestOptimizationStrategyService
from ..utils import node_as_text

# \d 会连全角、阿拉伯-印度数字一起匹配，这里只要 ASCII 数字，所以写成 [0-9]。
_BUDGET_PATTERN = re.compile(r"([0-9]{3,6})")
_CLAUSE_SPLIT_PATTERN = re.compile(r"[；;\n]")
_ADJUSTMENT_KEYWORDS = (
    "预算",
    "交通",
    "自驾",
    "地铁",
    "公交",
    "打车",
    "酒店",
    "住宿",
    "民宿",
    "客栈",
    "经济型",
    "高端",
    "舒适型",
    "便捷",
    "自然风景",
    "人文历史",
    "城市漫游",
    "轻松节奏",
    "美食",
    "节奏",
    "慢一点",
    "多一点",
    "少一点",
)

_SYSTEM_PROMPT = """你是旅行计划修改助手。你的任务不是重新写一份文案，而是把用户的自然语言修改要求解析成结构化 JSON。
你必须只返回 JSON，不要返回任何解释、Markdown、代码块。
JSON 字段固定为：
{
  "budget": number|null,
  "transportation": string|null,
  "accommodation": string|null,
  "preferencesToAdd": string[],
  "preferencesToRemove": string[],
  "extraRequirements": string[]
}
规则：
1. transportation 只允许返回：自驾、公共交通、打车为主、智能推荐、null
2. accommodation 只允许返回：经济型酒店、便捷酒店、舒适型酒店、高端酒店、民宿、客栈、null
3. preferences 只允许：人文历史、自然风景、美食、城市漫游、轻松节奏
4. 如果用户没有明确修改某项，就返回 null 或空数组
5. extraRequirements 用于保留补充限制，比如“不吃海鲜”“别太赶”“靠近地铁”
"""


class TripAdjustmentAgent:

    def __init__(self,
                 llm_client: LlmClient,
                 request_optimization_strategy_service: RequestOptimizationStrategyService):
        self._llm_client = llm_client
        self._request_optimization_strategy_service = request_optimization_strategy_service

    def adjust(self,
               original_request: TripPlanRequest,
               adjustment_prompt: str | None,
               anchor_hint: str | None = None,
               anchor_day: int | None = None,
               pin_hints: list[str] | None = None,
               pin_day: int | None = None,
               pin_move_hotel: bool = False) -> "TripAdjustmentAgent.TripAdjustmentDecision":
        if self._llm_client.is_available():
            try:
                decision = self._adjust_by_llm(original_request, adjustment_prompt)
            except Exception:  # noqa: BLE001 - 解析阶段任何失败都回退规则解析
                decision = self._adjust_by_rules(original_request, adjustment_prompt)
                decision.applied_changes.append("LLM 解析失败，已自动回退到规则解析")
                decision.adjustment_source = "rule-fallback"
        else:
            decision = self._adjust_by_rules(original_request, adjustment_prompt)
        self._apply_anchor(decision.updated_request, anchor_hint, anchor_day, decision.applied_changes)
        self._apply_pin(decision.updated_request, pin_hints, pin_day, pin_move_hotel, decision.applied_changes)
        return decision

    def _apply_pin(self,
                   updated_request: TripPlanRequest,
                   pin_hints: list[str] | None,
                   pin_day: int | None,
                   move_hotel: bool,
                   applied_changes: list[str]) -> None:
        """把追问摘出的"某天必去景点（可多个）"写回 day_attraction_pins；move_hotel 时把住宿锚点也指过去。

        一天可累加多个（一句话点名多个或多次追问）；move_hotel=是 就是替用户填了那格"期望住宿位置"，
        复用现成酒店锚点链路，最终酒店位置由规划器排完游玩顺序后贴到当天最后一站。
        """
        names = [n.strip() for n in (pin_hints or []) if n and n.strip()]
        if not names:
            return
        day = pin_day if (pin_day is not None and pin_day >= 0) else 0
        pins = {d: list(v) for d, v in (updated_request.day_attraction_pins or {}).items()}
        day_names = list(pins.get(day) or [])
        added = [n for n in names if n not in day_names]
        if not added:
            return
        day_names.extend(added)
        pins[day] = day_names
        updated_request.day_attraction_pins = pins
        applied_changes.append(f"已将第 {day + 1} 天必去景点设为 {'、'.join(added)}")
        if move_hotel:
            anchors = dict(updated_request.day_hotel_anchors or {})
            anchors[day] = names[0]
            updated_request.day_hotel_anchors = anchors
            applied_changes.append(f"已把第 {day + 1} 天住宿位置也挪到 {names[0]} 附近")

    def _apply_anchor(self,
                      updated_request: TripPlanRequest,
                      anchor_hint: str | None,
                      anchor_day: int | None,
                      applied_changes: list[str]) -> None:
        """把追问摘出的住宿地名写回请求那一格：整趟写 hotel_anchor，某天写 day_hotel_anchors。

        这条"整趟 vs 按天"的判断原先在前端抄，挪来后端后 /adjust 是唯一写口；按天写要并入
        已有 map，否则改一天会把其它天已填的锚点顶掉。
        """
        if not anchor_hint or not anchor_hint.strip():
            return
        hint = anchor_hint.strip()
        if anchor_day is None:
            updated_request.hotel_anchor = hint
            applied_changes.append(f"已将整趟住宿位置锚点设为 {hint}")
            return
        if anchor_day < 0:
            return
        day_map = dict(updated_request.day_hotel_anchors or {})
        day_map[anchor_day] = hint
        updated_request.day_hotel_anchors = day_map
        applied_changes.append(f"已将第 {anchor_day + 1} 天住宿位置锚点设为 {hint}")

    def _adjust_by_llm(self,
                       original_request: TripPlanRequest,
                       adjustment_prompt: str | None) -> "TripAdjustmentAgent.TripAdjustmentDecision":
        updated_request = self._request_optimization_strategy_service.copy_request(original_request)
        adjustment_node = self._request_llm_adjustment(original_request, adjustment_prompt)
        applied_changes: list[str] = []

        if _has_non_null(adjustment_node, "budget"):
            budget = _as_int(adjustment_node["budget"])
            if budget > 0 and budget != updated_request.budget:
                updated_request.budget = budget
                applied_changes.append(f"LLM 已将预算调整为 {budget} 元")
        if _has(adjustment_node, "transportation"):
            transportation = _read_nullable_text(adjustment_node.get("transportation"))
            if transportation == "智能推荐":
                updated_request.transportation = None
                applied_changes.append("LLM 已改为交通方式智能推荐")
            elif transportation is not None and transportation.strip():
                updated_request.transportation = transportation
                applied_changes.append(f"LLM 已调整交通方式为 {transportation}")
        if _has(adjustment_node, "accommodation"):
            accommodation = _read_nullable_text(adjustment_node.get("accommodation"))
            if accommodation is not None and accommodation.strip():
                updated_request.accommodation = accommodation
                applied_changes.append(f"LLM 已调整住宿偏好为 {accommodation}")

        self._apply_preference_changes(updated_request, adjustment_node, applied_changes)
        self._apply_extra_requirement_changes(
            updated_request, adjustment_prompt, adjustment_node, applied_changes
        )
        self._apply_shared_strategy_actions(updated_request, adjustment_prompt, applied_changes)

        if not applied_changes:
            applied_changes.append("LLM 已保留原始规划条件，并记录新的调整要求")
        return TripAdjustmentAgent.TripAdjustmentDecision(updated_request, applied_changes, "llm")

    def _apply_preference_changes(self,
                                  updated_request: TripPlanRequest,
                                  adjustment_node: object,
                                  applied_changes: list[str]) -> None:
        preferences = updated_request.preferences
        if preferences is None:
            preferences = []
            updated_request.preferences = preferences

        for item in _array_items(adjustment_node, "preferencesToAdd"):
            preference = node_as_text(item, "")
            if preference.strip() and preference not in preferences:
                preferences.append(preference)
                applied_changes.append(f"LLM 已增加偏好：{preference}")

        for item in _array_items(adjustment_node, "preferencesToRemove"):
            preference = node_as_text(item, "")
            if preference.strip() and _remove_first(preferences, preference):
                applied_changes.append(f"LLM 已移除偏好：{preference}")

    def _apply_extra_requirement_changes(self,
                                         updated_request: TripPlanRequest,
                                         adjustment_prompt: str | None,
                                         adjustment_node: object,
                                         applied_changes: list[str]) -> None:
        extra_requirements: list[str] = []
        for item in _array_items(adjustment_node, "extraRequirements"):
            value = node_as_text(item, "")
            if value.strip() and not self._is_adjustment_instruction(value):
                extra_requirements.append(value)
        if not extra_requirements and adjustment_prompt is not None and adjustment_prompt.strip():
            extra_requirements.extend(self._extract_residual_requirements(adjustment_prompt))
        merged = False
        for extra_requirement in extra_requirements:
            self._request_optimization_strategy_service.merge_extra_requirement(
                updated_request, extra_requirement
            )
            merged = True
        if merged:
            applied_changes.append("LLM 已记录新的自然语言调整要求")

    def _request_llm_adjustment(self, original_request: TripPlanRequest, adjustment_prompt: str | None) -> object:
        content = self._llm_client.chat(
            _SYSTEM_PROMPT, self._build_user_prompt(original_request, adjustment_prompt)
        )
        return json.loads(content)

    @staticmethod
    def _build_user_prompt(original_request: TripPlanRequest, adjustment_prompt: str | None) -> str:
        return ("原始请求：\n"
                + _object_to_pretty_json(original_request)
                + "\n\n用户新的修改要求：\n"
                + ("null" if adjustment_prompt is None else adjustment_prompt))

    def _adjust_by_rules(self,
                         original_request: TripPlanRequest,
                         adjustment_prompt: str | None) -> "TripAdjustmentAgent.TripAdjustmentDecision":
        updated_request = self._request_optimization_strategy_service.copy_request(original_request)
        applied_changes: list[str] = []
        normalized_prompt = "" if adjustment_prompt is None else adjustment_prompt.strip()
        lower_prompt = normalized_prompt.lower()

        self._apply_budget_adjustment(updated_request, normalized_prompt, lower_prompt, applied_changes)
        self._apply_transportation_adjustment(
            updated_request, normalized_prompt, lower_prompt, applied_changes
        )
        self._apply_accommodation_adjustment(
            updated_request, normalized_prompt, lower_prompt, applied_changes
        )
        self._apply_preference_adjustment(updated_request, normalized_prompt, lower_prompt, applied_changes)
        self._apply_extra_requirement_adjustment(updated_request, normalized_prompt, applied_changes)
        self._apply_shared_strategy_actions(updated_request, normalized_prompt, applied_changes)

        if not applied_changes:
            applied_changes.append("已保留原始规划条件，并记录新的调整要求")
        return TripAdjustmentAgent.TripAdjustmentDecision(updated_request, applied_changes, "rule")

    def _apply_budget_adjustment(self,
                                 updated_request: TripPlanRequest,
                                 normalized_prompt: str,
                                 lower_prompt: str,
                                 applied_changes: list[str]) -> None:
        if "预算" not in normalized_prompt:
            return
        matcher = _BUDGET_PATTERN.search(normalized_prompt)
        if matcher:
            budget = int(matcher.group(1))
            if budget > 0 and budget != updated_request.budget:
                updated_request.budget = budget
                applied_changes.append(f"已将预算调整为 {budget} 元")
        if _contains_any(lower_prompt, "省一点", "便宜点", "控制预算"):
            self._request_optimization_strategy_service.merge_extra_requirement(
                updated_request, "控制预算，优先高性价比方案"
            )
            applied_changes.append("已追加预算收紧约束")

    def _apply_transportation_adjustment(self,
                                         updated_request: TripPlanRequest,
                                         normalized_prompt: str,
                                         lower_prompt: str,
                                         applied_changes: list[str]) -> None:
        if "自驾" in normalized_prompt:
            updated_request.transportation = "自驾"
            applied_changes.append("已切换为自驾方案")
            return
        if _contains_any(normalized_prompt, "打车", "滴滴"):
            updated_request.transportation = "打车为主"
            applied_changes.append("已切换为打车为主")
            return
        if _contains_any(normalized_prompt, "公共交通", "地铁", "公交"):
            updated_request.transportation = "公共交通"
            applied_changes.append("已切换为公共交通")
            return
        if _contains_any(lower_prompt, "智能推荐", "你来安排交通"):
            updated_request.transportation = None
            applied_changes.append("已改为交通方式智能推荐")

    def _apply_accommodation_adjustment(self,
                                        updated_request: TripPlanRequest,
                                        normalized_prompt: str,
                                        lower_prompt: str,
                                        applied_changes: list[str]) -> None:
        if _contains_any(normalized_prompt, "经济", "便宜酒店", "省钱酒店"):
            updated_request.accommodation = "经济型酒店"
            applied_changes.append("已切换为经济型酒店偏好")
            return
        if _contains_any(normalized_prompt, "便捷酒店", "快捷酒店"):
            updated_request.accommodation = "便捷酒店"
            applied_changes.append("已切换为便捷酒店偏好")
            return
        if _contains_any(normalized_prompt, "舒适", "舒服一点"):
            updated_request.accommodation = "舒适型酒店"
            applied_changes.append("已切换为舒适型酒店偏好")
            return
        if _contains_any(normalized_prompt, "高端", "品质酒店", "豪华"):
            updated_request.accommodation = "高端酒店"
            applied_changes.append("已切换为高端酒店偏好")
            return
        if _contains_any(normalized_prompt, "民宿"):
            updated_request.accommodation = "民宿"
            applied_changes.append("已切换为民宿偏好")
            return
        if _contains_any(lower_prompt, "客栈"):
            updated_request.accommodation = "客栈"
            applied_changes.append("已切换为客栈偏好")

    def _apply_preference_adjustment(self,
                                     updated_request: TripPlanRequest,
                                     normalized_prompt: str,
                                     lower_prompt: str,
                                     applied_changes: list[str]) -> None:
        self._update_preference(updated_request, normalized_prompt, lower_prompt,
                                "人文历史", ("历史", "博物馆", "人文"), applied_changes)
        self._update_preference(updated_request, normalized_prompt, lower_prompt,
                                "自然风景", ("自然", "公园", "风景", "户外"), applied_changes)
        self._update_preference(updated_request, normalized_prompt, lower_prompt,
                                "美食", ("美食", "小吃", "吃"), applied_changes)
        self._update_preference(updated_request, normalized_prompt, lower_prompt,
                                "城市漫游", ("艺术", "城市漫游", "展览", "citywalk"), applied_changes)
        self._update_preference(updated_request, normalized_prompt, lower_prompt,
                                "轻松节奏", ("轻松", "慢一点", "别太赶", "悠闲"), applied_changes)

    def _update_preference(self,
                           updated_request: TripPlanRequest,
                           normalized_prompt: str,
                           lower_prompt: str,
                           preference: str,
                           keywords: tuple[str, ...],
                           applied_changes: list[str]) -> None:
        preferences = updated_request.preferences
        if preferences is None:
            preferences = []
            updated_request.preferences = preferences
        wants_remove = _contains_any(
            normalized_prompt, f"不要{preference}", f"不想要{preference}"
        ) or any(
            f"不要{keyword}" in normalized_prompt or f"不想去{keyword}" in normalized_prompt
            for keyword in keywords
        )
        if wants_remove:
            if _remove_first(preferences, preference):
                applied_changes.append(f"已移除偏好：{preference}")
            return
        wants_add = any(keyword in normalized_prompt for keyword in keywords) or (
            preference == "轻松节奏" and _contains_any(lower_prompt, "轻松", "慢一点", "悠闲")
        )
        if wants_add and preference not in preferences:
            preferences.append(preference)
            applied_changes.append(f"已增加偏好：{preference}")

    def _apply_extra_requirement_adjustment(self,
                                            updated_request: TripPlanRequest,
                                            normalized_prompt: str,
                                            applied_changes: list[str]) -> None:
        if not normalized_prompt.strip():
            return
        residual_requirements = self._extract_residual_requirements(normalized_prompt)
        merged = False
        for residual_requirement in residual_requirements:
            self._request_optimization_strategy_service.merge_extra_requirement(
                updated_request, residual_requirement
            )
            merged = True
        if merged:
            applied_changes.append("已记录新的自然语言调整要求")

    def _apply_shared_strategy_actions(self,
                                       updated_request: TripPlanRequest,
                                       prompt: str | None,
                                       applied_changes: list[str]) -> None:
        if prompt is None or not prompt.strip():
            return
        normalized = prompt.lower()
        self._apply_shared_action(
            updated_request, applied_changes, normalized, "REDUCE_BUDGET", "已应用统一预算收紧策略",
            "省一点", "便宜点", "控制预算", "缩预算", "预算紧"
        )
        self._apply_shared_action(
            updated_request, applied_changes, normalized, "COMPRESS_ROUTE", "已应用统一压缩路线策略",
            "路线紧凑", "少折返", "别太绕", "压缩路线", "近一点", "同片区"
        )
        self._apply_shared_action(
            updated_request, applied_changes, normalized, "REPLACE_OUTDOOR_WITH_INDOOR", "已应用统一室内备选策略",
            "下雨", "室内", "雨天", "别晒", "避雨"
        )
        self._apply_shared_action(
            updated_request, applied_changes, normalized, "UPGRADE_EXPERIENCE", "已应用统一体验升级策略",
            "品质一点", "高端一点", "住好一点", "体验好一点", "升级体验"
        )

    def _apply_shared_action(self,
                             updated_request: TripPlanRequest,
                             applied_changes: list[str],
                             normalized_prompt: str,
                             action: str,
                             applied_message: str,
                             *keywords: str) -> None:
        for keyword in keywords:
            if keyword.lower() in normalized_prompt:
                strategy_notes: list[str] = []
                self._request_optimization_strategy_service.apply_action(
                    updated_request, None, action, strategy_notes
                )
                if strategy_notes:
                    applied_changes.append(applied_message)
                    applied_changes.extend(strategy_notes)
                return

    def _extract_residual_requirements(self, prompt: str | None) -> list[str]:
        residual_requirements: list[str] = []
        if prompt is None or not prompt.strip():
            return residual_requirements
        for clause in _CLAUSE_SPLIT_PATTERN.split(prompt):
            trimmed = clause.strip()
            if not trimmed or self._is_adjustment_instruction(trimmed):
                continue
            residual_requirements.append(trimmed)
        return residual_requirements

    @staticmethod
    def _is_adjustment_instruction(text: str | None) -> bool:
        normalized = ("" if text is None else text.strip()).lower()
        if not normalized:
            return False
        return any(keyword in normalized for keyword in _ADJUSTMENT_KEYWORDS)

    class TripAdjustmentDecision:
        """解析结果：改后的请求副本、改了哪几项、来源是 llm 还是 rule。"""

        def __init__(self,
                     updated_request: TripPlanRequest,
                     applied_changes: list[str],
                     adjustment_source: str):
            self.updated_request = updated_request
            self.applied_changes = applied_changes
            self.adjustment_source = adjustment_source


def _contains_any(text: str, *keywords: str) -> bool:
    return any(keyword in text for keyword in keywords)


def _read_nullable_text(node: object) -> str | None:
    if node is None:
        return None
    value = node_as_text(node, "")
    return None if (not value.strip() or value.lower() == "null") else value


def _has(node: object, name: str) -> bool:
    return isinstance(node, dict) and name in node


def _has_non_null(node: object, name: str) -> bool:
    return _has(node, name) and node[name] is not None


def _array_items(node: object, name: str) -> list[object]:
    value = node.get(name) if isinstance(node, dict) else None
    return value if isinstance(value, list) else []


def _as_int(value: object) -> int:
    # 数字取整数部分，可解析的字符串按十进制读，其余取 0。
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        try:
            return int(text)
        except ValueError:
            try:
                return int(float(text))
            except ValueError:
                return 0
    return 0


def _object_to_pretty_json(model: object) -> str:
    try:
        return json.dumps(model.model_dump(mode="json"), indent=2, ensure_ascii=False)
    except Exception:  # noqa: BLE001 - 序列化失败时回落到 str(model)
        return str(model)


def _remove_first(values: list[str], value: str) -> bool:
    # 按值删除首个匹配项，返回有没有真的删掉。
    try:
        values.remove(value)
        return True
    except ValueError:
        return False
