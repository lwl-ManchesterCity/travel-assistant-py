"""对应 Java service/RequestOptimizationStrategyService.java：把批评动作翻译成下一轮请求参数。

Java 里这个类被 LoopDecisionExecutor 与 TripAdjustmentAgent 共用；本轮只搬 Loop 需要的那部分调用方，
方法本身按源码整体搬全，便于下一轮 TripAdjustmentAgent 直接复用。
"""

import re

from ..models.trip_plan import TripPlan
from ..models.trip_plan_request import TripPlanRequest

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
        self.merge_extra_requirement(updated_request, "整体控制预算，优先保留高价值景点")
        self.merge_extra_requirement(updated_request, "减少高门票和远距离景点")
        if plan is not None and plan.budget is not None and not plan.budget.within_budget:
            change_notes.append("已加入预算收紧约束")

    def _apply_compress_route(self, updated_request: TripPlanRequest, change_notes: list[str]) -> None:
        self.merge_extra_requirement(updated_request, "尽量压缩跨区通勤，优先安排同片区景点")
        self.merge_extra_requirement(updated_request, "减少折返路线")
        change_notes.append("已加入压缩路线约束")

    def _apply_indoor_replacement(self, updated_request: TripPlanRequest, change_notes: list[str]) -> None:
        self.merge_extra_requirement(updated_request, "如遇降雨优先安排室内备选景点")
        self.merge_extra_requirement(updated_request, "优先博物馆、美术馆、商圈等低天气暴露景点")
        change_notes.append("已加入雨天室内备选约束")

    def _apply_upgrade_experience(self, updated_request: TripPlanRequest, change_notes: list[str]) -> None:
        upgraded = self.upgrade_accommodation(updated_request.accommodation)
        if upgraded != updated_request.accommodation:
            updated_request.accommodation = upgraded
            change_notes.append(f"住宿偏好上调为 {upgraded}")
        self.merge_extra_requirement(updated_request, "预算充足时优先提升体验质量和景点丰富度")
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
