from typing import Optional

from .base import CamelModel


class PlanningConstraints(CamelModel):
    relaxed_pace: bool = False
    avoid_seafood: bool = False
    wants_landmark_ceremony: bool = False
    prefers_indoor_backup: bool = False
    extracted_tags: Optional[list[str]] = None
    # 这一趟的额外要求里露出的**长期偏好原文**（第二档，§2.2.4）。它不是规划用的约束，
    # 是给 PreferenceService 落库的原料——所以**不参与** planner 的任何判断。
    extracted_preferences: Optional[list[str]] = None
    # 备注里露出的**场景原文**（第四档，§2.4.4），一条是 `{"city": …, "content": …}`。
    # 与 extracted_preferences 一样**不参与** planner 的任何判断，只管落库。
    extracted_episodes: Optional[list[dict]] = None
    analysis_source: Optional[str] = None

    def __init__(self, **data) -> None:
        super().__init__(**data)
        # Java 构造器把 analysisSource 固定成 "rule"，RequirementsService 之后再改写。
        if self.analysis_source is None:
            self.analysis_source = "rule"
