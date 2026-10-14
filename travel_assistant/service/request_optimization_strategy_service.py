"""对应 Java service/RequestOptimizationStrategyService.java：把批评动作翻译成下一轮请求参数。

Java 里这个类被 LoopDecisionExecutor 与 TripAdjustmentAgent 共用；本轮只搬 Loop 需要的那部分调用方，
方法本身按源码整体搬全，便于下一轮 TripAdjustmentAgent 直接复用。
"""

import re

from ..models.trip_plan import TripPlan
from ..models.trip_plan_request import TripPlanRequest

_CLAUSE_SPLIT_PATTERN = re.compile(r"[；;\n]")

# 本服务往 extra_requirements 里追加的**系统自己生成的句子**。
#
# 为什么要单独收成一份：这些句子会跟着 `adjust_trip` → `plan_trip(decision.updated_request)`
# → builder → RequirementAnalysisService.analyze 一路走，**必然出现在偏好抽取的输入里**。
# 但它们是"这一次"的调节，不是"这个人"的偏好——照原样存下来，等于从此每一趟都强制
# 压预算、每一趟都压缩路线，而用户根本不知道有这回事（§2.2.4）。
#
# 抽取那边按**子句精确匹配**剔掉：拼进去用的是 merge_extra_requirement 的 `；`，
# 拆分用的是 _CLAUSE_SPLIT_PATTERN 的 `；;换行`，所以精确相等是成立的，不用模糊匹配。
#
# 每句都是一个具名常量，_apply_* 里引用的就是它——**同一份字面量只写一遍**，
# 否则改了一处忘了另一处，那条就会变成一个人的永久偏好。
REQ_REDUCE_BUDGET = "整体控制预算，优先保留高价值景点"
REQ_FEWER_TICKETS = "减少高门票和远距离景点"
REQ_COMPRESS_COMMUTE = "尽量压缩跨区通勤，优先安排同片区景点"
REQ_FEWER_DETOURS = "减少折返路线"
REQ_INDOOR_ON_RAIN = "如遇降雨优先安排室内备选景点"
REQ_LOW_WEATHER_EXPOSURE = "优先博物馆、美术馆、商圈等低天气暴露景点"
REQ_UPGRADE_QUALITY = "预算充足时优先提升体验质量和景点丰富度"

SYSTEM_GENERATED_REQUIREMENTS = frozenset({
    REQ_REDUCE_BUDGET,
    REQ_FEWER_TICKETS,
    REQ_COMPRESS_COMMUTE,
    REQ_FEWER_DETOURS,
    REQ_INDOOR_ON_RAIN,
    REQ_LOW_WEATHER_EXPOSURE,
    REQ_UPGRADE_QUALITY,
})
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


def split_extra_requirements(text: str | None) -> list[str]:
    """按子句切开 extra_requirements。拼接用的是 `；`（merge_extra_requirement），
    所以这里按 `；;换行` 切，切出来的每一段就是当初拼进去的那一段原文。"""
    if text is None or not text.strip():
        return []
    return [clause.strip() for clause in _CLAUSE_SPLIT_PATTERN.split(text) if clause.strip()]


def strip_system_requirements(text: str | None) -> str | None:
    """去掉系统自己追加的那些句子，只留用户原话。**抽长期偏好之前必须过这一道**（§2.2.4）。

    精确相等即可，不需要模糊匹配：两边用的是同一个分隔符，切出来的就是当初拼进去的原文。
    全被剔光时返回 None——调用方按"没填备注"处理。
    """
    kept = [clause for clause in split_extra_requirements(text)
            if clause not in SYSTEM_GENERATED_REQUIREMENTS]
    return "；".join(kept) if kept else None


class RequestOptimizationStrategyService:

    def copy_request(self, request: TripPlanRequest) -> TripPlanRequest:
        return TripPlanRequest(
            city=request.city,
            start_date=request.start_date,
            end_date=request.end_date,
            budget=request.budget,
            preferences=list(request.preferences) if request.preferences is not None else [],
            transportation=request.transportation,
            accommodation=request.accommodation,
            extra_requirements=self._sanitize_persisted_extra_requirements(request.extra_requirements),
            # 这三格是 Java 没有的扩展，必须显式带上：漏一格的后果是用户改一次行程，
            # 上次填的住宿位置与"整趟住同一家"就静默消失了。
            hotel_anchor=request.hotel_anchor,
            day_hotel_anchors=dict(request.day_hotel_anchors) if request.day_hotel_anchors else None,
            hotel_continuity=request.hotel_continuity,
            day_attraction_pins=({day: list(names) for day, names in request.day_attraction_pins.items()}
                                 if request.day_attraction_pins else None),
            origin_city=request.origin_city,
        )

    def apply_action(self,
                     updated_request: TripPlanRequest,
                     plan: TripPlan | None,
                     action: str | None,
                     change_notes: list[str]) -> None:
        normalized = self.normalize_action(action)
        if normalized == "REDUCE_BUDGET":
            self._apply_reduce_budget(updated_request, plan, change_notes)
        elif normalized == "COMPRESS_ROUTE":
            self._apply_compress_route(updated_request, change_notes)
        elif normalized == "REPLACE_OUTDOOR_WITH_INDOOR":
            self._apply_indoor_replacement(updated_request, change_notes)
        elif normalized == "UPGRADE_EXPERIENCE":
            self._apply_upgrade_experience(updated_request, change_notes)

    def merge_extra_requirement(self, request: TripPlanRequest, new_requirement: str | None) -> None:
        if new_requirement is None or not new_requirement.strip():
            return
        existing = request.extra_requirements
        if existing is None or not existing.strip():
            request.extra_requirements = new_requirement
            return
        if new_requirement not in existing:
            request.extra_requirements = f"{existing}；{new_requirement}"

    def normalize_action(self, action: str | None) -> str:
        return "STOP" if action is None else action.strip().upper()

    def downgrade_accommodation(self, accommodation: str | None) -> str:
        if accommodation is None or not accommodation.strip():
            return "经济型酒店"
        if "高端" in accommodation or "豪华" in accommodation:
            return "舒适型酒店"
        if "舒适" in accommodation:
            return "经济型酒店"
        if "民宿" in accommodation or "客栈" in accommodation:
            return "经济型酒店"
        return accommodation

    def upgrade_accommodation(self, accommodation: str | None) -> str:
        if accommodation is None or not accommodation.strip():
            return "舒适型酒店"
        if "经济" in accommodation or "便捷" in accommodation:
            return "舒适型酒店"
        if "舒适" in accommodation:
            return "高端酒店"
        return accommodation

    def _apply_reduce_budget(self,
                             updated_request: TripPlanRequest,
                             plan: TripPlan | None,
                             change_notes: list[str]) -> None:
        downgraded = self.downgrade_accommodation(updated_request.accommodation)
        if downgraded != updated_request.accommodation:
            updated_request.accommodation = downgraded
            change_notes.append(f"住宿偏好下调为 {downgraded}")
        if updated_request.transportation != "公共交通":
            updated_request.transportation = "公共交通"
            change_notes.append("交通方式调整为公共交通")
        self.merge_extra_requirement(updated_request, REQ_REDUCE_BUDGET)
        self.merge_extra_requirement(updated_request, REQ_FEWER_TICKETS)
        if plan is not None and plan.budget is not None and not plan.budget.within_budget:
            change_notes.append("已加入预算收紧约束")

    def _apply_compress_route(self, updated_request: TripPlanRequest, change_notes: list[str]) -> None:
        self.merge_extra_requirement(updated_request, REQ_COMPRESS_COMMUTE)
        self.merge_extra_requirement(updated_request, REQ_FEWER_DETOURS)
        change_notes.append("已加入压缩路线约束")

    def _apply_indoor_replacement(self, updated_request: TripPlanRequest, change_notes: list[str]) -> None:
        self.merge_extra_requirement(updated_request, REQ_INDOOR_ON_RAIN)
        self.merge_extra_requirement(updated_request, REQ_LOW_WEATHER_EXPOSURE)
        change_notes.append("已加入雨天室内备选约束")

    def _apply_upgrade_experience(self, updated_request: TripPlanRequest, change_notes: list[str]) -> None:
        upgraded = self.upgrade_accommodation(updated_request.accommodation)
        if upgraded != updated_request.accommodation:
            updated_request.accommodation = upgraded
            change_notes.append(f"住宿偏好上调为 {upgraded}")
        self.merge_extra_requirement(updated_request, REQ_UPGRADE_QUALITY)
        change_notes.append("已加入体验升级约束")

    def _sanitize_persisted_extra_requirements(self, extra_requirements: str | None) -> str | None:
        cleaned_requirements = self._extract_residual_requirements(extra_requirements)
        return "；".join(cleaned_requirements) if cleaned_requirements else None

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

    def _is_adjustment_instruction(self, text: str | None) -> bool:
        normalized = ("" if text is None else text.strip()).lower()
        if not normalized:
            return False
        return any(keyword in normalized for keyword in _ADJUSTMENT_KEYWORDS)
