from typing import Optional

from .base import CamelModel
from .hotel import Hotel
from .provider_metadata import ProviderMetadata


class HotelSearchResult(CamelModel):
    city: Optional[str] = None
    accommodation: Optional[str] = None
    hotels: Optional[list[Hotel]] = None
    summary: Optional[str] = None
    provider: Optional[ProviderMetadata] = None
