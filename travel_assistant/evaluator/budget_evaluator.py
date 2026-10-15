"""判定当天花费是否超预算。

日均预算之上还留了 _TOLERANCE 的容差，只有超出「预算 + 容差」才算超。
"""

_TOLERANCE = 120


class BudgetEvaluator:
    def get_budget_tolerance(self) -> int:
        return _TOLERANCE

    def is_over_budget(self, estimated_cost: int, daily_budget_limit: int) -> bool:
        return estimated_cost > daily_budget_limit + self.get_budget_tolerance()
