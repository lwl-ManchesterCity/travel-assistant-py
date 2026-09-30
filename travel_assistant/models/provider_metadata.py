from typing import Optional

from .base import CamelModel


class ProviderMetadata(CamelModel):
    provider_code: Optional[str] = None
    provider_name: Optional[str] = None
    mock: bool = False
