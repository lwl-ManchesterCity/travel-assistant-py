"""对应 Java evaluator/BudgetEvaluator.java。"""

_TOLERANCE = 120


class BudgetEvaluator:
    def get_budget_tolerance(self) -> int:
        return _TOLERANCE

    def is_over_budget(self, estimated_cost: int, daily_budget_limit: int) -> bool:
        return estimated_cost > daily_budget_limit + self.get_budget_tolerance()
