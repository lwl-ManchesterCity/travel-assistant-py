"""本地景点源：拿 TravelKnowledgeService 里写死的数据当 mock 景点用。"""

from ..models.attraction import Attraction
from ..models.attraction_query import AttractionQuery
from ..models.provider_metadata import ProviderMetadata
from .base import AttractionClient
from .knowledge_base import TravelKnowledgeService

_PROVIDER = ProviderMetadata(
    provider_code="local-knowledge-base",
    provider_name="本地知识库景点源",
    mock=True,
)


class InMemoryAttractionClient(AttractionClient):
    def __init__(self, travel_knowledge_service: TravelKnowledgeService | None = None):
        self._travel_knowledge_service = travel_knowledge_service or TravelKnowledgeService()

    def find_attractions(self, query: AttractionQuery) -> list[Attraction]:
        return self._travel_knowledge_service.find_attractions(query.city)

    def find_attraction_by_name(self, city: str, name: str) -> Attraction | None:
        if not name or not name.strip():
            return None
        name = name.strip()
        attractions = self._travel_knowledge_service.find_attractions(city)
        exact = [a for a in attractions if a.name == name]
        pool = exact or [a for a in attractions if a.name and (name in a.name or a.name in name)]
        return pool[0] if pool else None

    def get_metadata(self) -> ProviderMetadata:
        return _PROVIDER
