"""Odds-provider adapter architecture.

No licensed historical odds feed is wired in this phase, and the demo
consensus numbers from the TypeScript mock are NOT execution prices and
are never ingested here. Two legitimate sources exist today:

  1. nflverse `games` closing lines — Closing Capture benchmark only
     (handled by the nflverse adapter and the canonical loader).
  2. Manually entered bet365 prices from the existing application —
     accepted as *user-observed quotes* with their original observed_at,
     via the API, matching the app's manual-entry contract. No scraping,
     no automation, no credentials, ever.

This module defines the adapter contract a licensed multi-book feed
would implement, so the market-feature pipeline has a stable seam.
"""

from __future__ import annotations

from abc import abstractmethod
from typing import Any

from fde_api.providers.base import FetchResult, ProviderAdapter


class OddsProviderAdapter(ProviderAdapter):
    """Contract for a licensed historical/live odds source.

    Implementations must deliver *timestamped quotes* (book, market, line,
    price, observed_at) — a snapshot without an observation instant cannot
    participate in point-in-time features and must be rejected upstream.
    """

    @abstractmethod
    def fetch(self, dataset: str, params: dict[str, Any]) -> FetchResult: ...


class NoOddsProviderConfigured(OddsProviderAdapter):
    """Explicit null object: makes 'no odds feed' a visible state, not a crash."""

    name = "odds-unconfigured"

    def datasets(self) -> list[str]:
        return []

    def schema_version(self, dataset: str) -> str:
        raise LookupError("No odds provider is configured")

    def fetch(self, dataset: str, params: dict[str, Any]) -> FetchResult:
        raise LookupError(
            "No odds provider is configured. Historical multi-book odds require "
            "a licensed feed; consensus demo odds are not real execution prices."
        )
