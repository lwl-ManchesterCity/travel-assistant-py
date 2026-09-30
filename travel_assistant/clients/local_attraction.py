"""对应 Java client/impl/InMemoryAttractionClient.java（mock 景点源）。"""

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

    def get_metadata(self) -> ProviderMetadata:
        return _PROVIDER
