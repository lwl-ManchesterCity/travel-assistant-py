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


# ---------- 开放时间与闭馆日（docs/ASK_LIVE_HOURS.md §3.3） ----------

def test_opentime2_fills_both_hours_and_closed_days():
    poi = {"biz_ext": {"opentime2": "周二至周日 09:00-17:00开放 最晚进入16:30；周一 全天不开放"}}
    assert _client()._parse_opentime(poi) == ("09:00-17:00", ["周一"])


def test_open_time_is_the_fallback_when_opentime2_is_missing():
    """简版里没有闭馆日，所以只回落 hours，**别顺手编一个闭馆日出来**。"""
    assert _client()._parse_opentime({"biz_ext": {"open_time": "08:00-18:00"}}) == ("08:00-18:00", None)


def test_both_fields_stay_none_when_amap_gives_neither():
    """两格都空时给 None，不给空串、也不给 []——`[]` 会被读成"确认没有闭馆日"。"""
    assert _client()._parse_opentime({"biz_ext": {}}) == (None, None)
    assert _client()._parse_opentime({}) == (None, None)


def test_all_day_placeholder_becomes_the_answer_all_day():
    """00:00-24:00 是高德对「全天开放」的占位。

    对 `parse_opentime2` 它是占位（那个函数要 HH:MM-HH:MM），
    但**对追问侧它是答案**——「全天开放」回答「几点关门」完全够用，丢掉就变成「未给出」。
    """
    assert _client()._parse_opentime({"biz_ext": {"opentime2": "00:00-24:00"}}) == ("全天开放", None)


def test_the_literal_all_day_wording_counts_too():
    """高德有时候不写占位、直接写「全天开放」。"""
    assert _client()._parse_opentime({"biz_ext": {"opentime2": "周一至周日 全天开放"}}) == ("全天开放", None)


def test_a_real_time_wins_over_the_all_day_placeholder():
    """同一条里既有占位又有具体时段时，具体时段更准——别被「全天开放」盖掉。"""
    poi = {"biz_ext": {"opentime2": "周一至周日 00:00-24:00；北门开放时间:07:00-22:00"}}
    assert _client()._parse_opentime(poi) == ("07:00-22:00", None)


def test_to_attraction_carries_the_two_fields_through():
    poi = {
        "id": "B000A7",
        "name": "中国丝绸博物馆",
        "location": "120.15,30.25",
        "type": "科教文化服务;博物馆;博物馆",
        "biz_ext": {"opentime2": "周二至周日 09:00-17:00；周一 全天不开放"},
    }
    attraction = _client()._to_attraction(poi)
    assert (attraction.hours, attraction.closed_days) == ("09:00-17:00", ["周一"])


def test_to_attraction_leaves_the_two_fields_none_without_biz_ext():
    poi = {"id": "B000A7", "name": "某公园", "location": "120.15,30.25",
           "type": "风景名胜;公园广场;公园"}
    attraction = _client()._to_attraction(poi)
    assert attraction.hours is None
    assert attraction.closed_days is None
