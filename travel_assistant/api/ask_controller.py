"""方案 A 的追问端点：POST /api/trips/ask。

与 Java 无对应物，属于移植基线之外的能力扩展，见 docs/ASK_AGENT_DESIGN.md。
"""

from fastapi import APIRouter, Depends

from ..agent.ask_agent import AskAgent
from ..config.container import get_ask_agent
from ..models.ask_request import AskRequest
from ..models.ask_response import AskResponse

router = APIRouter(prefix="/api/trips")


@router.post("/ask", response_model=AskResponse)
def ask_trip_plan(
    request: AskRequest,
    ask_agent: AskAgent = Depends(get_ask_agent),
) -> AskResponse:
    return ask_agent.ask(request)
