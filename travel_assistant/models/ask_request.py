from typing import Literal, Optional

from pydantic import ConfigDict, field_validator

from .base import CamelModel
from .trip_plan_request import TripPlanRequest
from .trip_plan import TripPlan

# 历史里认的两种角色。用 Literal 而不是 str：模型侧的拼装只认这两种，
# 让它在校验层就把别的值挡住，别一路带到 HumanMessage/AIMessage 那儿才炸。
_TURN_ROLES = ("user", "assistant")


class AskTurn(CamelModel):
    """一条历史对话。

    `content` 存的是**当轮解析后的答案文本**，不是模型吐出的原始输出——
    原始输出是规则 9 约定的 JSON 信封（`{"intent":"answer","answer":"…"}`），
    存原文等于每轮把脚手架喂回模型（见 docs/ASK_MULTITURN_MEMORY.md §3.2）。

    工具调用与工具返回值**不进历史**（同文 §3.3）：一是体积差一个数量级，
    二是会拆掉提示词规则 2「每个数字都要在本轮工具返回值里找到原文」那道闸门。
    """

    role: Literal["user", "assistant"]
    content: str


class AskRequest(CamelModel):
    """一次方案追问：问题 + 被追问的那份方案 + 生成它时用的原始请求 + 可选的历史对话。

    `trip_plan` 与 `request` 都由前端原样回传（服务端不存方案）。两者都要：
    方案里只有排好的行程，偏好、住宿档位、预算上限这些取数参数只在原始请求里，
    追问涉及外部信息（周边景点、实时天气）时要用它们再去打数据源。

    `history` 是短期记忆：这一趟会话里前几轮的问答，前端从 sessionStorage 里取。
    **可选**——不传就是"单轮"，行为与从前一字不差。
    """

    # validate_default：JSON 里缺字段时字段校验器同样要跑，与 TripPlanRequest 一致。
    model_config = ConfigDict(validate_default=True)

    question: Optional[str] = None
    trip_plan: Optional[TripPlan] = None
    request: Optional[TripPlanRequest] = None
    thread_id: Optional[str] = None
    history: Optional[list[AskTurn]] = None

    @field_validator("history", mode="before")
    @classmethod
    def _drop_malformed_turns(cls, value):
        """挑出认得的那几条，剩下的丢掉，**不因此拒掉整个请求**。

        history 是可选字段，一个坏元素把整次追问打成 400 不划算——用户会看到
        "发送失败"，而其实他问的这句话完全没问题。丢掉的代价只是少一点上下文。
        """
        if value is None:
            return None
        if not isinstance(value, list):
            return []
        kept = []
        for item in value:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or "").strip().lower()
            content = item.get("content")
            if role not in _TURN_ROLES or not isinstance(content, str) or not content.strip():
                continue
            kept.append({"role": role, "content": content.strip()})
        return kept

    @field_validator("question")
    @classmethod
    def _question_not_blank(cls, value: Optional[str]) -> Optional[str]:
        if value is None or not value.strip():
            raise ValueError("must not be blank")
        return value

    @field_validator("trip_plan", "request")
    @classmethod
    def _not_null(cls, value):
        if value is None:
            raise ValueError("must not be null")
        return value
