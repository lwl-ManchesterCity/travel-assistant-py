from typing import Optional

from .base import CamelModel


class LoopCritique(CamelModel):
    """一轮评审的意见：动作、理由、预期效果，以及这条意见是谁给的（source）。"""

    action: Optional[str] = None
    reason: Optional[str] = None
    expected_outcome: Optional[str] = None
    source: Optional[str] = None
    notes: Optional[list[str]] = None

    def is_should_replan(self) -> bool:
        return self.action is not None and self.action.upper() != "STOP"
