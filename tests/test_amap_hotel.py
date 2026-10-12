"""高德酒店源：拿不到评分时**不许编一个默认分**。

这个值会进"选哪家酒店"的打分（`planner_agent._parse_hotel_rating_score`，空值按 4.0 算）。
以前这里填的是写死的 "4.3"，于是"没有评分"的酒店反而赢过真评分 4.0 的那家——
编出来的数字直接进了决策，而页面上那格和真评分长得一模一样，分不出来。
"""

from travel_assistant.agent.planner_agent import PlannerAgent
from travel_assistant.clients.amap_hotel import AmapHotelClient
from travel_assistant.clients.rule_hotel import RuleBasedHotelClient
from travel_assistant.config.settings import AmapSettings
from travel_assistant.models.hotel import Hotel


def _client() -> AmapHotelClient:
    return AmapHotelClient(AmapSettings(api_key=""), RuleBasedHotelClient())


def test_missingRatingStaysEmptyInsteadOfAMadeUpScore():
    """高德常有的形态：顶层有 biz_ext，里面没有 rating。"""
    assert _client()._extract_rating({"biz_ext": {}}) == ""


def test_missingBizExtAlsoStaysEmpty():
    assert _client()._extract_rating({}) == ""


def test_blankRatingIsNotARating():
    assert _client()._extract_rating({"biz_ext": {"rating": "   "}}) == ""


def test_aRealRatingIsKeptAsIs():
    assert _client()._extract_rating({"biz_ext": {"rating": "4.7"}}) == "4.7"


def test_aHotelWithoutARatingNoLongerBeatsARealFourPointZero():
    """上下游在这里接上：空评分按 4.0 算，跟真 4.0 打平，不再靠编出来的 4.3 取胜。"""
    planner = object.__new__(PlannerAgent)      # 只要这一个纯方法，不建整条链路

    assert planner._parse_hotel_rating_score(Hotel(name="无评分店", rating="")) == 4.0
    assert planner._parse_hotel_rating_score(Hotel(name="真 4.0", rating="4.0")) == 4.0
