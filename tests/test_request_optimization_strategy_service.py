"""对应 Java service/RequestOptimizationStrategyService.java 的行为断言。

期望文案全部取自 Java 源码：动作分支的 changeNotes、mergeExtraRequirement 的 "；" 连接、
downgrade/upgradeAccommodation 的档位表，以及 sanitizePersistedExtraRequirements 按关键词剔除调整指令。
"""

from travel_assistant.models.budget import Budget
from travel_assistant.models.trip_plan import TripPlan
from travel_assistant.models.trip_plan_request import TripPlanRequest
from travel_assistant.service.request_optimization_strategy_service import RequestOptimizationStrategyService

_SERVICE = RequestOptimizationStrategyService()


def _request(**overrides) -> TripPlanRequest:
    values = dict(
        city="杭州",
        start_date="2026-07-01",
        end_date="2026-07-03",
        budget=3000,
        preferences=["美食", "自然风景"],
        transportation="地铁",
        accommodation="舒适型酒店",
        extra_requirements="希望节奏轻松一点",
    )
    values.update(overrides)
    return TripPlanRequest(**values)


def _plan(within_budget: bool, remaining_budget: int) -> TripPlan:
    return TripPlan(
        city="杭州",
        budget=Budget(total=3000 - remaining_budget, requested_budget=3000,
                      remaining_budget=remaining_budget, within_budget=within_budget),
    )


def test_copy_request_should_drop_adjustment_instructions_only():
    copied = _SERVICE.copy_request(_request(extra_requirements="对海鲜过敏；希望节奏轻松一点"))

    # "希望节奏轻松一点" 命中关键词 节奏，被剔除；"对海鲜过敏" 不命中，保留
    assert copied.extra_requirements == "对海鲜过敏"
    assert copied.preferences == ["美食", "自然风景"]
    assert copied.city == "杭州"
    assert copied.accommodation == "舒适型酒店"

    assert _SERVICE.copy_request(_request(extra_requirements="希望节奏轻松一点")).extra_requirements is None


def test_reduce_budget_should_downgrade_accommodation_and_force_public_transport():
    updated_request = _SERVICE.copy_request(_request())
    change_notes: list[str] = []

    _SERVICE.apply_action(updated_request, _plan(False, -600), "REDUCE_BUDGET", change_notes)

    assert updated_request.accommodation == "经济型酒店"
    assert updated_request.transportation == "公共交通"
    assert updated_request.extra_requirements == "整体控制预算，优先保留高价值景点；减少高门票和远距离景点"
    assert change_notes == [
        "住宿偏好下调为 经济型酒店",
        "交通方式调整为公共交通",
        "已加入预算收紧约束",
    ]


def test_reduce_budget_should_not_add_overflow_note_when_still_within_budget():
    updated_request = _SERVICE.copy_request(_request(accommodation="经济型酒店", transportation="公共交通"))
    change_notes: list[str] = []

    _SERVICE.apply_action(updated_request, _plan(True, 24), "REDUCE_BUDGET", change_notes)

    assert change_notes == []
    assert updated_request.accommodation == "经济型酒店"
    assert updated_request.extra_requirements == "整体控制预算，优先保留高价值景点；减少高门票和远距离景点"


def test_compress_route_and_indoor_replacement_should_only_add_constraints():
    compress_target = _SERVICE.copy_request(_request())
    compress_notes: list[str] = []
    _SERVICE.apply_action(compress_target, _plan(True, 100), "compress_route", compress_notes)

    assert compress_notes == ["已加入压缩路线约束"]
    assert compress_target.extra_requirements == "尽量压缩跨区通勤，优先安排同片区景点；减少折返路线"
    assert compress_target.accommodation == "舒适型酒店"

    indoor_target = _SERVICE.copy_request(_request())
    indoor_notes: list[str] = []
    _SERVICE.apply_action(indoor_target, _plan(True, 100), "REPLACE_OUTDOOR_WITH_INDOOR", indoor_notes)

    assert indoor_notes == ["已加入雨天室内备选约束"]
    assert indoor_target.extra_requirements == (
        "如遇降雨优先安排室内备选景点；优先博物馆、美术馆、商圈等低天气暴露景点"
    )


def test_upgrade_experience_should_raise_accommodation_one_level():
    updated_request = _SERVICE.copy_request(_request(accommodation="经济型酒店"))
    change_notes: list[str] = []

    _SERVICE.apply_action(updated_request, _plan(True, 4000), "UPGRADE_EXPERIENCE", change_notes)

    assert updated_request.accommodation == "舒适型酒店"
    assert change_notes == ["住宿偏好上调为 舒适型酒店", "已加入体验升级约束"]
    assert updated_request.extra_requirements == "预算充足时优先提升体验质量和景点丰富度"


def test_stop_action_should_not_change_request():
    updated_request = _SERVICE.copy_request(_request())
    before = updated_request.model_dump()
    change_notes: list[str] = []

    _SERVICE.apply_action(updated_request, _plan(True, 100), "STOP", change_notes)

    assert change_notes == []
    assert updated_request.model_dump() == before


def test_merge_extra_requirement_should_be_idempotent():
    request = _request(extra_requirements=None)
    _SERVICE.merge_extra_requirement(request, "减少折返路线")
    _SERVICE.merge_extra_requirement(request, "减少折返路线")
    _SERVICE.merge_extra_requirement(request, "  ")

    assert request.extra_requirements == "减少折返路线"


def test_accommodation_ladders():
    assert _SERVICE.downgrade_accommodation("高端度假酒店") == "舒适型酒店"
    assert _SERVICE.downgrade_accommodation("西湖民宿") == "经济型酒店"
    assert _SERVICE.downgrade_accommodation("海景客栈") == "经济型酒店"
    assert _SERVICE.downgrade_accommodation(None) == "经济型酒店"
    assert _SERVICE.downgrade_accommodation("青旅") == "青旅"
    assert _SERVICE.upgrade_accommodation("经济型酒店") == "舒适型酒店"
    assert _SERVICE.upgrade_accommodation("舒适型酒店") == "高端酒店"
    assert _SERVICE.upgrade_accommodation("") == "舒适型酒店"
    assert _SERVICE.upgrade_accommodation("青旅") == "青旅"
