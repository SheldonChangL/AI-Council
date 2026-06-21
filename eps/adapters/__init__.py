"""eps.adapters subpackage — LLMAdapter 契約與後端實作（Story 3.1 / 3.2）。"""

from eps.adapters.base import (
    AdapterError,
    AdapterTimeout,
    AuthError,
    LLMAdapter,
    SourceError,
    TransientError,
)
from eps.adapters.fake import FakeAdapter
from eps.adapters.local_cli import LocalCliAdapter

__all__ = [
    "AdapterError",
    "AdapterTimeout",
    "AuthError",
    "LLMAdapter",
    "SourceError",
    "TransientError",
    "FakeAdapter",
    "LocalCliAdapter",
]
