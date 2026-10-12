from typing import Optional

from .base import CamelModel
from .location import Location


class Attraction(CamelModel):
    # 景点身份（2026-10-12 加，见 docs/PLANNER_SELECTION_DESIGN.md 3.5）：
    # 高德 POI 的 id，规划器拿它判重。没有它就只能比名字，于是"两个同名景点"会被当成一个
    # （第二个永远排不上）、"名字差一个括号"会被当成两个（可能两天各去一遍）。
    # 本地知识库源造不出这个 id，留 None —— 判重那边会退回比名字，与加这一格之前一致。
    id: Optional[str] = None
    name: Optional[str] = None
    address: Optional[str] = None
    location: Optional[Location] = None
    visit_duration: int = 0
    description: Optional[str] = None
    category: Optional[str] = None
    rating: Optional[float] = None
    image_url: Optional[str] = None
    ticket_price: int = 0
