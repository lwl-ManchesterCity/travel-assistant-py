"""对应 Java agent/LoopPlanningAgent.java 的闭环行为断言。

Java 侧没有 LoopPlanningAgentTest，这里的期望值全部按源码流程推：
停止条件与 stopReason 文案、appendLoopResult 追加的三句 planningNotes、
loopTraces 前插到 agentTraces 头部、markSelectedRound 只留一条 selectedAsBest，
以及 loopIterations 每轮字段的取值来源。
"""

from travel_assistant.agent.llm_critic_agent import LlmCriticAgent
from travel_assistant.agent.loop_decision_executor import LoopDecisionExecutor
from travel_assistant.agent.loop_planning_agent import LoopPlanningAgent
from travel_assistant.agent.reflection_agent import ReflectionAgent
from travel_assistant.config.settings import LoopSettings
from travel_assistant.evaluator.budget_evaluator import BudgetEvaluator
from travel_assistant.evaluator.daily_plan_evaluator import DailyPlanEvaluator
from travel_assistant.evaluator.route_evaluator import RouteEvaluator
from travel_assistant.evaluator.weather_evaluator import WeatherEvaluator
from travel_assistant.models.budget import Budget
from travel_assistant.models.day_plan import DayPlan
from travel_assistant.models.loop_critique import LoopCritique
from travel_assistant.models.route_plan import RoutePlan
from travel_assistant.models.trip_plan import TripPlan
from travel_assistant.models.trip_plan_request import TripPlanRequest
from travel_assistant.models.weather_info import WeatherInfo
from travel_assistant.service.request_optimization_strategy_service import RequestOptimizationStrategyService

_LOOP_DONE_NOTE = "LoopPlanningAgent 已完成生成-评估-反思-修正闭环"


class _StubPlannerInputBuilder:
    """只占住 LoopPlanningAgent 的取数入口位置，本测试关心的是闭环而不是取数。"""

    def build(self, request: TripPlanRequest) -> TripPlanRequest:
        return request


class _StubPlannerAgent:

    def __init__(self, plans: list[TripPlan]):
        self._plans = plans
        self.built_requests: list[TripPlanRequest] = []

    def build_plan(self, planner_input: TripPlanRequest) -> TripPlan:
        self.built_requests.append(planner_input)
        return self._plans.pop(0)


class _StubCritic:

    def __init__(self, critique: LoopCritique):
        self._critique = critique

    def critique(self, request, plan, reflection) -> LoopCritique:
        return self._critique


class _StubDecisionExecutor:

    def __init__(self, updated_request: TripPlanRequest, change_notes: list[str]):
        self._result = LoopDecisionExecutor.ExecutionResult(updated_request, change_notes)

    def apply(self, request, plan, critique) -> LoopDecisionExecutor.ExecutionResult:
        return self._result


def _request(**overrides) -> TripPlanRequest:
    values = dict(
        city="杭州",
        start_date="2026-07-01",
        end_date="2026-07-03",
        budget=3000,
        preferences=["美食", "自然风景"],
        transportation="地铁",
        accommodation="舒适型酒店",
        extra_requirements="希望节奏轻松一点",
    )
    values.update(overrides)
    return TripPlanRequest(**values)


def _plan(remaining_budget: int, within_budget: bool, day_weather: str = "晴") -> TripPlan:
    return TripPlan(
        city="杭州",
        start_date="2026-07-01",
        end_date="2026-07-03",
        days=[
            DayPlan(date=f"2026-07-0{index + 1}", day_index=index,
                    route_plan=RoutePlan(total_duration_minutes=60))
            for index in range(3)
        ],
        weather_info=[WeatherInfo(date=f"2026-07-0{index + 1}", day_weather=day_weather)
                      for index in range(3)],
        overall_suggestions="杭州 3 天旅行计划",
        budget=Budget(total=3000 - remaining_budget, requested_budget=3000,
                      remaining_budget=remaining_budget, within_budget=within_budget),
        planning_notes=["规则阶段的规划备注"],
        agent_traces=[],
    )


def _agent(plans: list[TripPlan],
           loop: LoopSettings | None = None,
           critic=None,
           decision_executor=None) -> tuple[LoopPlanningAgent, _StubPlannerAgent]:
    planner_agent = _StubPlannerAgent(plans)
    agent = LoopPlanningAgent(
        _StubPlannerInputBuilder(),
        planner_agent,
        ReflectionAgent(DailyPlanEvaluator(BudgetEvaluator(), RouteEvaluator(), WeatherEvaluator())),
        critic or LlmCriticAgent(_UnavailableLlm()),
        decision_executor or LoopDecisionExecutor(RequestOptimizationStrategyService()),
        loop or LoopSettings(),
    )
    return agent, planner_agent


class _UnavailableLlm:

    def is_available(self) -> bool:
        return False

    def chat(self, system_prompt: str, user_prompt: str) -> str:
        return ""


def test_shouldStopAtFirstRoundWhenReflectionAccepts():
    first_round_plan = _plan(24, True)
    agent, planner_agent = _agent([first_round_plan])

    result = agent.plan(_request())

    assert result is first_round_plan
    assert len(planner_agent.built_requests) == 1
    assert result.loop_summary is not None
    assert result.loop_summary.model_dump(by_alias=True) == {
        "enabled": True,
        "roundsExecuted": 1,
        "selectedRound": 1,
        "stopReason": "第 1 轮通过规则评估，直接停止",
    }
    assert result.planning_notes[-3:] == [
        _LOOP_DONE_NOTE,
        "LoopPlanningAgent 选定第 1 轮作为当前最优方案",
        "第 1 轮通过规则评估，直接停止",
    ]
    assert result.planning_notes[0] == "规则阶段的规划备注"

    iterations = result.loop_iterations
    assert iterations is not None and len(iterations) == 1
    assert [iteration.selected_as_best for iteration in iterations] == [True]
    assert iterations[0].round == 1
    assert iterations[0].request_summary == "城市=杭州，预算=3000，住宿=舒适型酒店，交通=地铁"
    assert iterations[0].total_cost == 2976
    assert iterations[0].remaining_budget == 24
    assert iterations[0].total_route_minutes == 180
    assert iterations[0].reflection_score == 0
    assert iterations[0].reflection_action == "STOP"
    assert iterations[0].within_budget is True
    assert iterations[0].should_replan is False
    assert iterations[0].applied_changes == []
    assert iterations[0].critique_source is None
    assert iterations[0].critique_action is None

    assert [trace.agent_name for trace in result.agent_traces] == [
        "LoopPlanningAgent",
        "ReflectionAgent",
    ]
    assert result.agent_traces[0].action == "第 1 轮生成初稿"
    assert result.agent_traces[0].output_summary == "总费用=2976，剩余预算=24"
    assert result.agent_traces[1].status == "accept"
    assert result.agent_traces[1].input_summary == "总费用=2976，总通勤=180 分钟"


def test_shouldAppendLoopResultEvenWhenLoopDisabled():
    single_plan = _plan(24, True)
    agent, planner_agent = _agent([single_plan], loop=LoopSettings(enabled=False))

    result = agent.plan(_request())

    assert len(planner_agent.built_requests) == 1
    assert result.loop_summary is not None
    assert result.loop_summary.model_dump(by_alias=True) == {
        "enabled": False,
        "roundsExecuted": 0,
        "selectedRound": 1,
        "stopReason": "loop-disabled",
    }
    assert result.loop_iterations == []
    assert result.planning_notes[-3:] == [
        _LOOP_DONE_NOTE,
        "LoopPlanningAgent 选定第 1 轮作为当前最优方案",
        "loop-disabled",
    ]
    assert result.agent_traces == []


def test_shouldReplanOnceAndSelectTheImprovedRound():
    first_round_plan = _plan(-600, False)
    second_round_plan = _plan(24, True)
    agent, planner_agent = _agent([first_round_plan, second_round_plan])

    result = agent.plan(_request())

    assert result is second_round_plan
    assert len(planner_agent.built_requests) == 2
    assert result.loop_summary.stop_reason == "第 2 轮通过规则评估，直接停止"
    assert result.loop_summary.rounds_executed == 2
    assert result.loop_summary.selected_round == 2

    iterations = result.loop_iterations
    assert iterations is not None and len(iterations) == 2
    assert [iteration.selected_as_best for iteration in iterations] == [False, True]
    assert iterations[0].critique_source == "rule"
    assert iterations[0].critique_action == "REDUCE_BUDGET"
    assert iterations[0].reflection_action == "REDUCE_BUDGET"
    assert iterations[0].reflection_score == 40
    assert iterations[0].should_replan is True
    assert iterations[0].applied_changes == [
        "住宿偏好下调为 经济型酒店",
        "交通方式调整为公共交通",
        "已加入预算收紧约束",
    ]
    # 第二轮的输入是 executor 复制并净化过的请求：上一轮的“节奏”类表述被剔除，只剩本轮约束
    assert iterations[1].request_summary == "城市=杭州，预算=3000，住宿=经济型酒店，交通=公共交通"
    assert iterations[1].critique_source is None
    assert iterations[1].applied_changes == []

    assert [trace.agent_name for trace in result.agent_traces] == [
        "LoopPlanningAgent",
        "ReflectionAgent",
        "LlmCriticAgent",
        "LoopDecisionExecutor",
        "LoopPlanningAgent",
        "ReflectionAgent",
    ]
    executor_trace = result.agent_traces[3]
    assert executor_trace.action == "生成第 2 轮输入"
    assert executor_trace.input_summary == (
        "当前总预算超支 600 元；评分摘要：overall=40，budget=40，route=0，weather=0，experience=0"
    )
    assert executor_trace.output_summary == (
        "住宿偏好下调为 经济型酒店；交通方式调整为公共交通；已加入预算收紧约束"
    )
    assert result.agent_traces[2].status == "replan"
    assert result.agent_traces[2].output_summary == "动作=REDUCE_BUDGET，原因=当前方案预算严重度最高，建议优先压缩成本后重规划，来源=rule"


def test_shouldStopWhenCriticRejectsReplanning():
    plan = _plan(-600, False)
    critic = _StubCritic(LoopCritique(action="STOP", reason="可以收工", expected_outcome="保留当前方案", source="llm", notes=["够了"]))
    agent, planner_agent = _agent([plan, _plan(24, True)], critic=critic)

    result = agent.plan(_request())

    assert result is plan
    assert len(planner_agent.built_requests) == 1
    assert result.loop_summary.stop_reason == "第 1 轮达到可接受状态"
    assert result.loop_summary.rounds_executed == 1
    assert result.loop_iterations[0].should_replan is False
    assert result.loop_iterations[0].critique_source == "llm"
    assert result.agent_traces[2].status == "accept"


def test_shouldStopEarlyAfterConsecutiveRoundsWithoutImprovement():
    first_round_plan = _plan(-600, False)
    second_round_plan = _plan(-600, False)
    agent, _ = _agent(
        [first_round_plan, second_round_plan],
        loop=LoopSettings(enabled=True, max_rounds=3, max_no_improvement_rounds=1),
    )

    result = agent.plan(_request())

    assert result is first_round_plan
    assert result.loop_summary.model_dump(by_alias=True) == {
        "enabled": True,
        "roundsExecuted": 2,
        "selectedRound": 1,
        "stopReason": "连续 1 轮没有改善，提前停止并保留当前最优方案",
    }
    assert result.loop_iterations is not None
    assert [iteration.selected_as_best for iteration in result.loop_iterations] == [True, False]


def test_shouldKeepBestPlanWhenMaxRoundsExhausted():
    agent, planner_agent = _agent(
        [_plan(-600, False), _plan(-300, False)],
        loop=LoopSettings(enabled=True, max_rounds=2, max_no_improvement_rounds=5),
    )

    result = agent.plan(_request())

    assert len(planner_agent.built_requests) == 2
    assert result.loop_summary.stop_reason == "达到最大循环轮次，保留当前最优方案"
    assert result.loop_summary.selected_round == 2
    assert result.loop_summary.rounds_executed == 2
    assert result.budget.remaining_budget == -300
    assert result.loop_iterations is not None
    assert [iteration.selected_as_best for iteration in result.loop_iterations] == [False, True]


def test_shouldStopWhenCriticGivesNoExecutableAdjustment():
    plan = _plan(24, True, day_weather="中雨")
    critic = _StubCritic(LoopCritique(action="REDUCE_BUDGET", reason="预算优先", expected_outcome="降成本", source="llm", notes=[]))
    agent, planner_agent = _agent(
        [plan],
        loop=LoopSettings(enabled=True, max_rounds=3, max_no_improvement_rounds=5),
        critic=critic,
        decision_executor=LoopDecisionExecutor(RequestOptimizationStrategyService()),
    )

    result = agent.plan(_request(accommodation="经济型酒店", transportation="公共交通"))

    assert len(planner_agent.built_requests) == 1
    assert result.loop_summary.stop_reason == "批评已给出，但未生成新的可执行调整"
    assert result.loop_summary.selected_round == 1
    assert result.loop_iterations[0].applied_changes == []
    assert result.agent_traces[2].status == "replan"


def test_shouldStopWhenAdjustedRequestRepeats():
    repeated_request = _request(accommodation="经济型酒店", transportation="公共交通")
    executor = _StubDecisionExecutor(repeated_request, ["已加入压缩路线约束"])
    agent, planner_agent = _agent(
        [_plan(-600, False), _plan(-600, False)],
        loop=LoopSettings(enabled=True, max_rounds=4, max_no_improvement_rounds=9),
        decision_executor=executor,
    )

    result = agent.plan(_request())

    assert len(planner_agent.built_requests) == 2
    assert result.loop_summary.stop_reason == "检测到循环调整重复，保留当前最优方案"
    assert result.loop_summary.rounds_executed == 2
    assert result.loop_summary.selected_round == 1
    assert len([trace for trace in result.agent_traces if trace.agent_name == "LoopDecisionExecutor"]) == 2
