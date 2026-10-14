"""方案 A 的追问端点。

- `POST /api/trips/ask`：一次性返回整份 AskResponse。**行为与从前一字不差**，
  留着是因为它是既有判据的基准，也是不需要流式时的退路。
- `POST /api/trips/ask/stream`：SSE，边跑边推工具调用，最后推完整答案。

**为什么答案不逐 token 流**：模型最后一轮输出的是规则 9 约定的 JSON 信封
（`{"intent":"answer","answer":"…"}`）。逐 token 推出去，用户会先看到脚手架。
而工具调用发生在答案之前，是天然的进度条，所以**那部分真流**；答案整条推，
前端拿到之后本地做打字机效果（"假流式"）。

与 Java 无对应物，属于移植基线之外的能力扩展，见 docs/ASK_AGENT_DESIGN.md。
"""

import asyncio
import json
from typing import AsyncIterator

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from ..agent.ask_agent import AskAgent
from ..config.container import get_ask_agent
from ..models.ask_request import AskRequest
from ..models.ask_response import AskResponse
from ..models.auth import AuthUser
from .auth_dependency import require_current_user

router = APIRouter(prefix="/api/trips")

# 队列里的结束哨兵。用一个模块级对象而不是字符串，免得跟真实事件名撞上。
_DONE = object()


def _frame(event: str, payload: dict) -> str:
    """一条 SSE 帧。空行是帧分隔符，必须留着。"""
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


# 这两条挂 `require_current_user`：**依赖在进入 handler 之前跑**，所以没登录时
# 返回的是一条普通的 JSON 401，而不是"SSE 流建好了再推一条 error 事件"——
# 前端不必为流式那条路另写一套鉴权失败的解析。
# 第二档起它是**参数**而不是 dependencies=[...]：偏好要按 user_id 存取（见
# docs/ASK_LONGTERM_MEMORY.md §2.2.3），拿不到用户对象就没法记、也没法注入。

@router.post("/ask", response_model=AskResponse)
def ask_trip_plan(
    request: AskRequest,
    user: AuthUser = Depends(require_current_user),
    ask_agent: AskAgent = Depends(get_ask_agent),
) -> AskResponse:
    return ask_agent.ask(request, user_id=user.id)


@router.post("/ask/stream")
async def ask_trip_plan_stream(
    request: AskRequest,
    user: AuthUser = Depends(require_current_user),
    ask_agent: AskAgent = Depends(get_ask_agent),
) -> StreamingResponse:
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()

    def on_event(event: str, payload: dict) -> None:
        # ask() 跑在**线程**里（它是同步的），而这个队列归事件循环线程。
        # 所以必须 call_soon_threadsafe —— 直接 put_nowait 不是线程安全的。
        loop.call_soon_threadsafe(queue.put_nowait, (event, payload))

    async def worker() -> None:
        try:
            await asyncio.to_thread(ask_agent.ask, request, on_event, user.id)
        except Exception as exception:  # noqa: BLE001 - 失败要当成一条事件推给前端，
            # 而不是让连接断在半路——断了前端只会看到"流意外结束"，不知道发生了什么。
            loop.call_soon_threadsafe(
                queue.put_nowait, ("error", {"message": str(exception)}))
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, (_DONE, None))

    async def frames() -> AsyncIterator[str]:
        task = asyncio.create_task(worker())
        try:
            while True:
                event, payload = await queue.get()
                if event is _DONE:
                    break
                yield _frame(event, payload)
        finally:
            # 前端中途关掉连接时，别把线程里的那轮追问丢着不管。
            await task

    return StreamingResponse(
        frames(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            # 反代（nginx 等）默认会缓冲响应，那样"流式"就变成"攒完一次性给你"。
            "X-Accel-Buffering": "no",
        },
    )
