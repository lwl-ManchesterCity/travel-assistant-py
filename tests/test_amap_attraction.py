"""高德景点源：拿不到评分时**不许编一个默认分**。

与酒店那次（`tests/test_amap_hotel.py`）同理。这个值会进"选哪个景点"的打分
（`planner_agent._score_attraction` 里 `if attraction.rating is not None` 才加分）。
以前这里填的是写死的 4.5，于是"没有评分"的景点跟真有 4.5 的平起平坐——
编出来的数直接进了决策，页面上也分不出来。
"""

from travel_assistant.agent.planner_agent import PlannerAgent
from travel_assistant.clients.amap_attraction import AmapAttractionClient
from travel_assistant.models.attraction import Attraction


def _client() -> AmapAttractionClient:
    return object.__new__(AmapAttractionClient)      # 只要这一个纯方法，不建整条链路


def test_a_missing_rating_stays_none_instead_of_a_made_up_four_point_five():
    assert _client()._parse_rating({"biz_ext": {}}) is None


def test_missing_biz_ext_also_stays_none():
    assert _client()._parse_rating({}) is None


def test_a_blank_rating_is_not_a_rating():
    assert _client()._parse_rating({"biz_ext": {"rating": "   "}}) is None


def test_a_real_rating_is_kept_as_is():
    assert _client()._parse_rating({"biz_ext": {"rating": "4.7"}}) == 4.7


def test_a_missing_rating_does_not_break_the_scoring():
    """上下游在这里接上：评分缺失只是少加那一项，不该抛，也不该凭空得利。"""
    planner = object.__new__(PlannerAgent)
    fields = dict(address="测试", category="人文历史", ticket_price=0)

    without = planner._score_attraction(
        Attraction(name="无评分", rating=None, **fields), ["人文历史"], None, None, 1000)
    with_rating = planner._score_attraction(
        Attraction(name="有评分", rating=4.7, **fields), ["人文历史"], None, None, 1000)

    assert without == with_rating - 4.7
