from typing import Optional

from .base import CamelModel


class LoopExecutionSummary(CamelModel):
    """对应 Java model/LoopExecutionSummary.java。"""

    enabled: bool = False
    rounds_executed: int = 0
    selected_round: int = 0
    stop_reason: Optional[str] = None
