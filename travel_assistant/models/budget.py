from typing import Optional

from .base import CamelModel


class Budget(CamelModel):
    total_attractions: int = 0
    total_hotels: int = 0
    total_meals: int = 0
    total_transportation: int = 0
    # 城际往返票价（本项目新增的一桶）：先从总预算里扣掉，剩下的才按天分。
    total_intercity: int = 0
    intercity_mode: Optional[str] = None
    intercity_estimated: bool = False
    total: int = 0
    requested_budget: int = 0
    remaining_budget: int = 0
    status: Optional[str] = None
    within_budget: bool = False
