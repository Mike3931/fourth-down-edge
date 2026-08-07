"""Runtime configuration.

Everything that varies by machine lives here and is overridable via
environment variables prefixed FDE_ (e.g. FDE_DATABASE_URL).
"""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_API_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FDE_", env_file=_API_ROOT / ".env", extra="ignore")

    # PostgreSQL in production/CI; SQLite locally so the suite runs without a
    # server. Schema is kept portable across both (see docs/limitations).
    database_url: str = f"sqlite:///{(_API_ROOT / 'data' / 'fde.db').as_posix()}"

    data_dir: Path = _API_ROOT / "data"
    raw_dir: Path = _API_ROOT / "data" / "raw"
    artifacts_dir: Path = _API_ROOT / "data" / "artifacts"
    reports_dir: Path = _API_ROOT / "data" / "reports"

    # Seasons the ingestion CLI targets by default. Bounded so a first run is
    # predictable; widen deliberately, not accidentally.
    default_seasons: tuple[int, ...] = tuple(range(2018, 2026))

    request_timeout_seconds: float = 60.0

    # Provider credential, from FDE_ODDS_API_KEY in the environment or in
    # apps/api/.env (gitignored).
    #
    # It has to be declared here to be readable from .env at all: the model
    # ignores extra keys, so a .env containing FDE_ODDS_API_KEY was silently
    # dropped, os.environ was never populated by it, and the adapter stayed
    # unconfigured while the operator had every reason to think they had set
    # it. The runbook recommended that exact non-working path.
    #
    # `repr=False` keeps it out of the model's repr, so a stray print or a
    # logged exception carrying the settings object cannot spill it.
    odds_api_key: str = Field(default="", repr=False)

    def has_odds_key(self) -> bool:
        """Whether a provider credential is available, from either source.

        Presence only. Nothing returns the value except the adapter that
        must send it.
        """
        return bool((self.odds_api_key or os.environ.get("FDE_ODDS_API_KEY") or "").strip())

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.raw_dir, self.artifacts_dir, self.reports_dir):
            d.mkdir(parents=True, exist_ok=True)


settings = Settings()
