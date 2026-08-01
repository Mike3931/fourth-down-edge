"""nflverse-data adapter.

Fetches published release assets from the nflverse-data GitHub releases —
the same artifacts nflreadr serves. These are versioned public releases,
not scraped pages.

Datasets:
    games                games.csv — full schedule/results 1999→present, incl.
                         closing market lines (spread/total/moneylines), QBs,
                         coaches, referee, stadium, roof/surface/temp/wind, rest.
    pbp_{season}         play_by_play_{season}.parquet — includes EPA columns.
    rosters_{season}     roster_{season}.parquet — seasonal rosters with GSIS ids.

IMPORTANT integrity note recorded once here and honored by the feature
layer: the market columns in `games` are effectively CLOSING numbers.
They may only serve as (a) the Closing Capture evaluation benchmark and
(b) the residualization baseline — never as features for earlier
prediction horizons. There is no historical line-movement in this source.
"""

from __future__ import annotations

from typing import Any

import httpx

from fde_api.config import settings
from fde_api.providers.base import FetchResult, ProviderAdapter
from fde_api.util import iso_now

_BASE = "https://github.com/nflverse/nflverse-data/releases/download"

_SCHEMA_VERSIONS = {
    "games": "nflverse-games-v1",
    "pbp": "nflverse-pbp-v1",
    "rosters": "nflverse-rosters-v1",
}


class NflverseProvider(ProviderAdapter):
    name = "nflverse"

    def __init__(self, client: httpx.Client | None = None) -> None:
        self._client = client or httpx.Client(
            timeout=settings.request_timeout_seconds, follow_redirects=True
        )

    def datasets(self) -> list[str]:
        return ["games", "pbp", "rosters"]

    def schema_version(self, dataset: str) -> str:
        key = dataset.split("_")[0]
        try:
            return _SCHEMA_VERSIONS[key]
        except KeyError as e:
            raise ValueError(f"Unknown nflverse dataset {dataset!r}") from e

    def _url(self, dataset: str, params: dict[str, Any]) -> tuple[str, str]:
        if dataset == "games":
            # Schedules/results live in nflverse/nfldata (the source behind
            # nflreadr::load_schedules), not in nflverse-data release assets.
            return "https://github.com/nflverse/nfldata/raw/master/data/games.csv", "games.csv"
        if dataset == "pbp":
            season = int(params["season"])
            fn = f"play_by_play_{season}.parquet"
            return f"{_BASE}/pbp/{fn}", fn
        if dataset == "rosters":
            season = int(params["season"])
            fn = f"roster_{season}.parquet"
            return f"{_BASE}/rosters/{fn}", fn
        raise ValueError(f"Unknown nflverse dataset {dataset!r}")

    def fetch(self, dataset: str, params: dict[str, Any]) -> FetchResult:
        url, filename = self._url(dataset, params)
        resp = self._client.get(url)
        resp.raise_for_status()
        return FetchResult(
            payload=resp.content,
            filename=filename,
            observed_at=iso_now(),
            source_version=resp.headers.get("etag"),
            source_updated_at=resp.headers.get("last-modified"),
        )
