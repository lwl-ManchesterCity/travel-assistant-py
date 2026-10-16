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
    # 开放时间与闭馆日（2026-10-16 加，见 docs/ASK_LIVE_HOURS.md §3.2）：
    # 高德 `biz_ext.opentime2` 一直返回它们，只是以前运行时没读。
    # 与本地库那两格**同名同形**（`kb/retrieve.py` 的 hours / closed_days），工具层排版共用一套说法。
    # **追问链不读方案里这两格**：本地库那层是查证过、带 source_url 的，高德这层是原始 POI 文本，
    # 两者打架时该赢的是前者（同文档 §五第 2 条）。加它们只是让新生成的方案带上，
    # 前端原样回传整份方案，能穿过一轮往返。
    hours: Optional[str] = None
    closed_days: Optional[list[str]] = None
