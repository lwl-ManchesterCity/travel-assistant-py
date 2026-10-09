from typing import Optional

from .agent_trace import AgentTrace
from .base import CamelModel


class AskResponse(CamelModel):
    """追问的响应：一句回答 + 这一步的意图 + 本次工具调用留痕。

    `intent` 取 `answer`（只是问）或 `need_change`（其实想改方案）。判成 `need_change` 时
    `adjustment_prompt` 是给 /api/trips/adjust 的那句话，必须由用户确认后才发出去——
    追问 agent 全程零写权限，改方案的动作仍由 /adjust 做。

    `anchor_hint` / `anchor_day` 是模型从用户原话里摘出来的住宿地名与天数（从 0 起），
    前端据此把地名填进「住宿位置」那一格，再随请求一起交给 /adjust。它们是意图不是事实，
    不来自任何工具返回值，所以不受"事实必须来自工具"那条护栏约束。
    """

    answer: Optional[str] = None
    intent: str = "answer"
    adjustment_prompt: Optional[str] = None
    anchor_hint: Optional[str] = None
    anchor_day: Optional[int] = None
    # 模型从原话里摘出的"某天必去景点名（可多个）"与天数（从 0 起）。同 anchor 一样是意图不是事实。
    pin_hints: Optional[list[str]] = None
    pin_day: Optional[int] = None
    traces: Optional[list[AgentTrace]] = None
