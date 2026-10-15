from typing import Optional

from .base import CamelModel


class LoopExecutionSummary(CamelModel):
    """闭环跑完的概况：是否启用、跑了几轮、选中哪一轮、为什么停。"""

    enabled: bool = False
    rounds_executed: int = 0
    selected_round: int = 0
    stop_reason: Optional[str] = None
