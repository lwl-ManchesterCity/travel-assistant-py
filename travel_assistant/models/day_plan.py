from typing import Optional

from .attraction import Attraction
from .base import CamelModel
from .hotel import Hotel
from .meal import Meal
from .route_plan import RoutePlan


class DayPlan(CamelModel):
    date: Optional[str] = None
    day_index: int = 0
    description: Optional[str] = None
    transportation: Optional[str] = None
    accommodation: Optional[str] = None
    hotel: Optional[Hotel] = None
    attractions: Optional[list[Attraction]] = None
    meals: Optional[list[Meal]] = None
    route_plan: Optional[RoutePlan] = None
    estimated_cost: int = 0
    optimization_notes: Optional[list[str]] = None
