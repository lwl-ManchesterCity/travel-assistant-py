from typing import Optional

from .base import CamelModel


class AttractionQuery(CamelModel):
    city: Optional[str] = None
    preferences: Optional[list[str]] = None
