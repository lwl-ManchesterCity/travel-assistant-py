from typing import Optional

from .agent_trace import AgentTrace
from .base import CamelModel


class AskResponse(CamelModel):
    """追问的响应：一句回答 + 这一步的意图 + 本次工具调用留痕。

    `intent` 在方案 A 里恒为 `answer`，字段先占住是给方案 B 留的口子：
    那时模型可以返回 `need_change`，前端据此去调 /api/trips/adjust。
    """

    answer: Optional[str] = None
    intent: str = "answer"
    traces: Optional[list[AgentTrace]] = None
