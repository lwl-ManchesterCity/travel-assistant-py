from typing import Optional

from .attraction import Attraction
from .base import CamelModel
from .provider_metadata import ProviderMetadata


class AttractionSearchResult(CamelModel):
    city: Optional[str] = None
    preferences: Optional[list[str]] = None
    attractions: Optional[list[Attraction]] = None
    summary: Optional[str] = None
    provider: Optional[ProviderMetadata] = None
