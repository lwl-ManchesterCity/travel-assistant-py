"""当日路线的起讫点：从第一个景点起步，收工才回酒店。

曾经的链路是「酒店 → 景点… → 酒店」，早上那段"从酒店出发"每天顶在第一行，
既不是用户的真实动线（人是自行抵达首站的），也把通勤分钟数虚增了一段。
"""

from travel_assistant.clients.amap_route import AmapRouteClient
from travel_assistant.clients.rule_route import RuleBasedRouteClient
from travel_assistant.config.settings import AmapSettings
from travel_assistant.models.attraction import Attraction
from travel_assistant.models.hotel import Hotel
from travel_assistant.models.location import Location
from travel_assistant.models.route_query import RouteQuery


def _attraction(name: str, longitude: float, latitude: float) -> Attraction:
    return Attraction(
        name=name,
        address=f"{name}门口",
        location=Location(longitude=longitude, latitude=latitude),
        visit_duration=120,
        category="自然风景",
        rating=4.6,
        ticket_price=30,
    )


def _hotel(name: str = "广州酒店") -> Hotel:
    return Hotel(
        name=name,
        address="广州市天河区",
        location=Location(longitude=113.3300, latitude=23.1180),
        estimated_cost=420,
    )


def _query(attractions: list[Attraction], hotel: Hotel | None) -> RouteQuery:
    return RouteQuery(city="广州", day_index=1, transportation="地铁", hotel=hotel, attractions=attractions)


def test_shouldStartAtFirstAttractionAndOnlyReturnToHotelAtTheEnd():
    attractions = [
        _attraction("广州动物园", 113.3000, 23.1400),
        _attraction("人民公园", 113.2700, 23.1300),
        _attraction("白云山", 113.2900, 23.2100),
    ]
    route_plan = RuleBasedRouteClient().plan_route(_query(attractions, _hotel()))

    assert [step.from_name for step in route_plan.steps] == ["广州动物园", "人民公园", "白云山"]
    assert [step.to_name for step in route_plan.steps] == ["人民公园", "白云山", "广州酒店"]
    assert route_plan.start_point.name == "广州动物园"
    assert route_plan.end_point.name == "广州酒店"


def test_shouldNotEmitAMorningLegFromTheHotel():
    attractions = [_attraction("白云山", 113.2900, 23.2100), _attraction("广州塔", 113.3200, 23.1050)]
    route_plan = RuleBasedRouteClient().plan_route(_query(attractions, _hotel()))

    assert len(route_plan.steps) == 2
    assert all(step.from_name != "广州酒店" for step in route_plan.steps)
    assert [step.order_index for step in route_plan.steps] == [1, 2]


def test_shouldKeepSingleAttractionDayAsOneReturnLegOnly():
    route_plan = RuleBasedRouteClient().plan_route(_query([_attraction("白云山", 113.2900, 23.2100)], _hotel()))

    assert len(route_plan.steps) == 1
    assert route_plan.steps[0].from_name == "白云山"
    assert route_plan.steps[0].to_name == "广州酒店"


def test_shouldChainAttractionsWithoutHotelAtEitherEnd():
    attractions = [_attraction("白云山", 113.2900, 23.2100), _attraction("广州塔", 113.3200, 23.1050)]
    route_plan = RuleBasedRouteClient().plan_route(_query(attractions, None))

    assert len(route_plan.steps) == 1
    assert route_plan.start_point.name == "白云山"
    assert route_plan.end_point.name == "广州塔"


def test_amapRoutePointsShouldDropTheLeadingHotelStop():
    client = AmapRouteClient(AmapSettings(api_key="test-key", mock_response=True), RuleBasedRouteClient())
    attractions = [
        _attraction("广州动物园", 113.3000, 23.1400),
        _attraction("白云山", 113.2900, 23.2100),
    ]

    points = client._build_route_points(_query(attractions, _hotel()))

    assert [point.name for point in points] == ["广州动物园", "白云山", "广州酒店"]
    assert [point.point_type for point in points] == ["attraction", "attraction", "hotel"]
