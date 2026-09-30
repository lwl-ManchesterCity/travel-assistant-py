"""对应 Java agent/RouteAgent.java：路线请求只有一层转发，规划链路里由 PlannerAgent 每天多次调用。"""

from ..models.route_plan import RoutePlan
from ..models.route_query import RouteQuery


class RouteAgent:

    def __init__(self, route_client):
        self._route_client = route_client

    def plan_route(self, query: RouteQuery) -> RoutePlan:
        return self._route_client.plan_route(query)
