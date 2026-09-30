from typing import Optional

from .agent_trace import AgentTrace
from .base import CamelModel
from .budget import Budget
from .day_plan import DayPlan
from .loop_execution_summary import LoopExecutionSummary
from .loop_iteration_trace import LoopIterationTrace
from .weather_info import WeatherInfo


class TripPlan(CamelModel):
    city: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    days: Optional[list[DayPlan]] = None
    weather_info: Optional[list[WeatherInfo]] = None
    overall_suggestions: Optional[str] = None
    budget: Optional[Budget] = None
    planning_notes: Optional[list[str]] = None
    agent_traces: Optional[list[AgentTrace]] = None
    loop_summary: Optional[LoopExecutionSummary] = None
    loop_iterations: Optional[list[LoopIterationTrace]] = None
