from typing import Optional

from .base import CamelModel


class Budget(CamelModel):
    total_attractions: int = 0
    total_hotels: int = 0
    total_meals: int = 0
    total_transportation: int = 0
    total: int = 0
    requested_budget: int = 0
    remaining_budget: int = 0
    status: Optional[str] = None
    within_budget: bool = False
