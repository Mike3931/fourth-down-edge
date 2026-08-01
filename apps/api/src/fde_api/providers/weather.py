"""National Weather Service adapter architecture.

Contract only for this phase: the NWS API serves *forecasts going
forward*, so there is no legitimate way to obtain point-in-time
historical forecasts from it retroactively. Backfilling historical
weather features from realized observations would be lookahead, so
weather features remain UNAVAILABLE for historical replay (see
features.availability) until forward capture accumulates.

The adapter shape matches ProviderAdapter so forward-looking capture can
be scheduled without pipeline changes.
"""

from __future__ import annotations

from typing import Any

import httpx

from fde_api.config import settings
from fde_api.providers.base import FetchResult, ProviderAdapter
from fde_api.util import iso_now

_NWS = "https://api.weather.gov"


class NwsProvider(ProviderAdapter):
    name = "nws"

    def __init__(self, client: httpx.Client | None = None) -> None:
        self._client = client or httpx.Client(
            timeout=settings.request_timeout_seconds,
            follow_redirects=True,
            headers={"User-Agent": "fourth-down-edge (research; contact: owner)"},
        )

    def datasets(self) -> list[str]:
        return ["gridpoint_forecast"]

    def schema_version(self, dataset: str) -> str:
        return "nws-gridpoint-v1"

    def fetch(self, dataset: str, params: dict[str, Any]) -> FetchResult:
        if dataset != "gridpoint_forecast":
            raise ValueError(f"Unknown NWS dataset {dataset!r}")
        lat, lon = float(params["lat"]), float(params["lon"])
        # Two-step per NWS API: resolve the gridpoint, then fetch its forecast.
        meta = self._client.get(f"{_NWS}/points/{lat:.4f},{lon:.4f}")
        meta.raise_for_status()
        forecast_url = meta.json()["properties"]["forecast"]
        resp = self._client.get(forecast_url)
        resp.raise_for_status()
        return FetchResult(
            payload=resp.content,
            filename=f"forecast_{lat:.4f}_{lon:.4f}.json",
            observed_at=iso_now(),
            source_version=resp.headers.get("etag"),
            source_updated_at=resp.headers.get("last-modified"),
        )
