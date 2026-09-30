from typing import Optional

from .base import CamelModel


class LoopCritique(CamelModel):
    """对应 Java model/LoopCritique.java。"""

    action: Optional[str] = None
    reason: Optional[str] = None
    expected_outcome: Optional[str] = None
    source: Optional[str] = None
    notes: Optional[list[str]] = None

    def is_should_replan(self) -> bool:
        return self.action is not None and self.action.upper() != "STOP"
