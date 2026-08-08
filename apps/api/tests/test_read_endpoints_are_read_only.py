"""A GET must not change the database.

`/v1/forward/live` called `build_consensus`, which UPSERTS. Because
`as_of_at` is the wall clock, every call minted a new logical identity, so
a page polling once a minute across the slate would have written consensus
snapshots that no scheduled job ever produced — dated to whenever somebody
happened to have a browser tab open, and indistinguishable in the record
from real captures.

It read as read-only for exactly the wrong reason: the live slate has one
book, `build_consensus` bails below three, and nothing was written. The
write would have started the moment a provider key turned one book into
several — the same shape as the consensus sign bug, hidden behind the same
gate.

So the assertion is on ROW COUNTS ACROSS EVERY TABLE, not on the absence of
a particular call. A future endpoint that persists through some other path
fails this too.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

import fde_api.db.forward_models  # noqa: F401  (registers the tables)
from fde_api.db.forward_models import OddsQuote, ScheduleObservation
from fde_api.db.models import Base

NOW = datetime.now(UTC)
KICK = NOW + timedelta(hours=3)
GAME = "READONLY_PROBE"

READ_ENDPOINTS = [
    "/v1/forward/live",
    "/v1/forward/slate",
    "/v1/forward/health",
    "/v1/models",
    "/v1/performance/model-comparison",
]


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """A real app against a throwaway database seeded with THREE books.

    Three is the point. At one book the consensus builder returns early and
    an endpoint that would write never gets the chance, so a fixture with a
    single book would have passed against the broken code.
    """
    url = f"sqlite:///{(tmp_path / 'probe.db').as_posix()}"
    monkeypatch.setenv("FDE_ALLOW_UNAUTHENTICATED", "1")

    # `settings` is instantiated at import, so setting FDE_DATABASE_URL here
    # would do nothing and the app would quietly read the REAL database -
    # against which the census never changes either, and every assertion
    # below would pass while proving nothing. The attribute is patched
    # directly, and `test_the_app_is_really_using_the_probe_database` is the
    # guard that this worked.
    from fde_api.config import settings as app_settings

    monkeypatch.setattr(app_settings, "database_url", url)

    engine = create_engine(url, future=True)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, future=True)

    with factory() as s:
        s.add(ScheduleObservation(
            data_mode="LIVE_RESEARCH", canonical_game_id=GAME, provider="t",
            provider_game_id="x", season=2026, season_type="REG", week=1,
            home_team_id="ARI", away_team_id="CAR", kickoff_utc=KICK,
            neutral_site=False, international=False, game_status="SCHEDULED",
            content_hash="t", source_manifest_version="t",
            source_updated_at=NOW, observed_at=NOW))
        for book in ("draftkings", "fanduel", "betmgm"):
            for selection, point in (("HOME", -3.0), ("AWAY", 3.0)):
                s.add(OddsQuote(
                    data_mode="LIVE_RESEARCH", canonical_game_id=GAME, provider="t",
                    provider_mode="LIVE", sportsbook=book, market="SPREAD",
                    selection=selection, line=point, american=-110,
                    decimal_odds=1.91, is_live=False,
                    observed_at=NOW - timedelta(minutes=5),
                    raw_hash=hashlib.sha256(f"{book}{selection}".encode()).hexdigest()))
        s.commit()

    import fde_api.db.engine as db_engine

    db_engine.get_engine.cache_clear()  # type: ignore[attr-defined]
    from fde_api.api.main import app

    yield TestClient(app), factory
    db_engine.get_engine.cache_clear()  # type: ignore[attr-defined]


def _census(factory) -> dict[str, int]:
    """Row count for every table the app knows about."""
    with factory() as s:
        return {
            table.name: int(s.scalar(select(func.count()).select_from(table)) or 0)
            for table in Base.metadata.sorted_tables
        }


class TestReadEndpointsDoNotWrite:
    @pytest.mark.parametrize("path", READ_ENDPOINTS)
    def test_a_single_request_changes_nothing(self, client, path) -> None:
        api, factory = client
        before = _census(factory)
        assert api.get(path).status_code == 200
        after = _census(factory)
        changed = {k: (before[k], after[k]) for k in before if before[k] != after[k]}
        assert not changed, f"{path} changed row counts: {changed}"

    def test_repeated_polling_does_not_accumulate(self, client) -> None:
        """The failure mode was growth over time, not a single stray row."""
        api, factory = client
        api.get("/v1/forward/live")
        before = _census(factory)
        for _ in range(5):
            assert api.get("/v1/forward/live").status_code == 200
        after = _census(factory)
        changed = {k: (before[k], after[k]) for k in before if before[k] != after[k]}
        assert not changed, f"polling accumulated rows: {changed}"

    def test_the_app_is_really_using_the_probe_database(self, client) -> None:
        """Without this the module is decorative.

        If the app reads a different database, its row counts never move
        and every read-only assertion passes for the wrong reason.
        """
        api, _factory = client
        body = api.get("/v1/forward/live").json()
        ids = [g["canonical_game_id"] for g in body["games"]]
        assert GAME in ids, f"app is not reading the probe database; saw {ids[:3]}"

    def test_the_fixture_really_has_enough_books_to_trigger_a_write(
        self, client
    ) -> None:
        """Guards the guard.

        With fewer than three books the consensus builder returns before
        persisting, so this whole module would pass against the broken code
        while proving nothing.
        """
        _api, factory = client
        with factory() as s:
            books = {
                q.sportsbook for q in s.scalars(
                    select(OddsQuote).where(OddsQuote.market == "SPREAD"))
            }
        assert len(books) >= 3, books


class TestTheLiveEndpointStillExplainsItself:
    def test_a_game_without_a_captured_consensus_says_why(self, client) -> None:
        api, _factory = client
        body = api.get("/v1/forward/live").json()
        game = next(g for g in body["games"] if g["canonical_game_id"] == GAME)
        spread = game["markets"]["SPREAD"]
        assert spread["consensus"] is None
        assert spread["reasons"], "a missing consensus must carry its reason"
        # The count is computed read-only from the quotes on hand.
        assert spread["eligible_books_now"] == 3

    def test_the_disclaimer_states_it_only_reads(self, client) -> None:
        api, _factory = client
        assert "never builds a consensus" in api.get("/v1/forward/live").json()["not_a_claim"]
