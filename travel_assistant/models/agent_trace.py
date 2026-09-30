from typing import Optional

from .base import CamelModel


class AgentTrace(CamelModel):
    agent_name: Optional[str] = None
    action: Optional[str] = None
    input_summary: Optional[str] = None
    output_summary: Optional[str] = None
    status: Optional[str] = None
