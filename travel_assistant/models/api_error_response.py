from datetime import datetime
from typing import Optional

from .base import CamelModel


class ApiErrorResponse(CamelModel):
    code: Optional[str] = None
    message: Optional[str] = None
    details: Optional[list[str]] = None
    timestamp: Optional[datetime] = None
