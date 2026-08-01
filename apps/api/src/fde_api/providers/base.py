"""Provider adapter contract.

The pipeline depends on this interface, never on a concrete source, so a
new data vendor is an adapter, not a rewrite. Adapters fetch bytes and
provenance; persistence and immutability live in the raw store.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class FetchResult:
    payload: bytes
    filename: str
    observed_at: str  # ISO instant the bytes were in hand
    source_version: str | None  # ETag / release id when the source exposes one
    source_updated_at: str | None  # Last-Modified when the source exposes one


class ProviderAdapter(ABC):
    """A named source able to fetch named datasets."""

    name: str

    @abstractmethod
    def datasets(self) -> list[str]:
        """Dataset identifiers this adapter can fetch."""

    @abstractmethod
    def fetch(self, dataset: str, params: dict[str, Any]) -> FetchResult:
        """Fetch one dataset; raise on failure (caller records it)."""

    @abstractmethod
    def schema_version(self, dataset: str) -> str:
        """Version of the *contract we assume* for the dataset's columns."""
