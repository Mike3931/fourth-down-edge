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
    # Added later than this file, and covered only by their own narrower
    # check that the forward ledger did not grow. This one counts EVERY
    # table, which is the assertion that catches a write through a path
    # nobody thought to look at — and `/v1/forward/candidates` runs the
    # full health report to build its gate, which touches a good deal of
    # the schema.
    "/v1/forward/candidates",
    "/v1/forward/performance",
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


def test_upcoming_slate_survives_large_history_and_is_paginated(client):
    api, factory = client
    with factory() as s:
        for i in range(405):
            s.add(ScheduleObservation(
                data_mode="LIVE_RESEARCH", canonical_game_id=f"HISTORY_{i:03}",
                provider="t", provider_game_id=f"h{i}", season=2025, season_type="REG", week=1,
                home_team_id="ARI", away_team_id="CAR",
                kickoff_utc=NOW - timedelta(days=i + 1),
                game_status="FINAL", content_hash=f"h{i}", observed_at=NOW,
            ))
        s.commit()
    before = _census(factory)
    first = api.get("/v1/forward/slate").json()
    assert first["games"][0]["canonical_game_id"] == GAME
    assert first["total_count"] == 406 and first["has_more"]
    assert first == {**api.get("/v1/forward/slate").json(),
                     "generated_at_utc": first["generated_at_utc"]}
    second = api.get("/v1/forward/slate?offset=400").json()
    assert len(second["games"]) == 6 and not second["has_more"]
    assert not ({g["canonical_game_id"] for g in first["games"]}
                & {g["canonical_game_id"] for g in second["games"]})
    scoped = api.get("/v1/forward/slate?canonical_game_id=HISTORY_404").json()
    assert scoped["count"] == 1
    assert _census(factory) == before
    assert api.get("/v1/forward/slate?limit=-1").status_code == 422


def test_scoped_market_history_is_bounded_and_read_only(client):
    """One Game Lab GET must not read other games or lose old captured evidence."""
    api, factory = client
    old_game = "READONLY_HISTORICAL"
    with factory() as s:
        s.add(ScheduleObservation(
            data_mode="LIVE_RESEARCH", canonical_game_id=old_game, provider="t",
            provider_game_id="historical", season=2026, season_type="REG", week=1,
            home_team_id="ARI", away_team_id="CAR", kickoff_utc=NOW - timedelta(days=8),
            game_status="SCHEDULED", content_hash="historical", observed_at=NOW - timedelta(days=9),
        ))
        s.add(OddsQuote(
            data_mode="LIVE_RESEARCH", canonical_game_id=old_game, provider="t",
            provider_mode="LIVE", sportsbook="draftkings", market="SPREAD",
            selection="HOME", line=-1.5, american=-110, decimal_odds=1.91,
            is_live=False, observed_at=NOW - timedelta(days=9), raw_hash="historical-quote",
        ))
        s.commit()
    before = _census(factory)
    unscoped = api.get("/v1/forward/live")
    assert unscoped.status_code == 200
    assert [g["canonical_game_id"] for g in unscoped.json()["games"]] == [GAME]

    current = api.get(f"/v1/forward/live?canonical_game_id={GAME}")
    assert current.status_code == 200
    assert [g["canonical_game_id"] for g in current.json()["games"]] == [GAME]
    assert len(current.json()["games"][0]["quotes"]) == 6

    historical = api.get(f"/v1/forward/live?canonical_game_id={old_game}")
    assert historical.status_code == 200
    assert [g["canonical_game_id"] for g in historical.json()["games"]] == [old_game]
    assert len(historical.json()["games"][0]["quotes"]) == 1
    assert historical.json()["games"][0]["quotes"][0]["currently_available_offer"] is False

    unknown = api.get("/v1/forward/live?canonical_game_id=NOT_CAPTURED")
    assert unknown.status_code == 200 and unknown.json()["games"] == []
    assert _census(factory) == before


def test_live_market_window_filters_after_latest_revision(client):
    """A rescheduled game must not reappear under its superseded kickoff."""
    api, factory = client
    with factory() as s:
        for gid, old_kick, new_kick in (
            ("MOVED_OUT", NOW + timedelta(hours=4), NOW + timedelta(days=14)),
            ("MOVED_IN", NOW + timedelta(days=14), NOW + timedelta(hours=8)),
        ):
            for i, (kick, observed) in enumerate(((old_kick, NOW - timedelta(hours=2)),
                                                    (new_kick, NOW - timedelta(hours=1)))):
                s.add(ScheduleObservation(
                    data_mode="LIVE_RESEARCH", canonical_game_id=gid,
                    provider="t", provider_game_id=gid, season=2026, season_type="REG",
                    week=7, home_team_id="ARI", away_team_id="CAR",
                    kickoff_utc=kick, game_status="SCHEDULED",
                    content_hash=f"{gid}:{i}", observed_at=observed,
                ))
        s.commit()
    before = _census(factory)
    unscoped = api.get("/v1/forward/live")
    assert unscoped.status_code == 200
    ids = {g["canonical_game_id"] for g in unscoped.json()["games"]}
    assert "MOVED_OUT" not in ids
    assert "MOVED_IN" in ids
    selected = api.get("/v1/forward/live?canonical_game_id=MOVED_OUT")
    assert selected.status_code == 200
    assert [g["canonical_game_id"] for g in selected.json()["games"]] == ["MOVED_OUT"]
    assert selected.json()["games"][0]["kickoff_utc"] == (
        NOW + timedelta(days=14)).isoformat()
    assert _census(factory) == before


def test_quote_capture_time_is_not_a_sportsbook_offer(client):
    api, _ = client
    quote = api.get("/v1/forward/live").json()["games"][0]["quotes"][0]
    assert quote["captured_at_utc"] == quote["observed_at"]
    assert quote["sportsbook_price_established_at_utc"] is None
    assert quote["timestamp_basis"] == "APPLICATION_CAPTURE"
    assert quote["currently_available_offer"] is False


def test_recent_espn_capture_without_book_time_cannot_price_a_consensus(client):
    from fde_api.forward.cohort import Cohort
    from fde_api.forward.consensus import CONSENSUS_METHOD_VERSION, build_consensus

    api, factory = client
    with factory() as s:
        quotes = list(s.scalars(select(OddsQuote)))
        for quote in quotes:
            quote.provider = "espn"
        s.commit()
        original = [(q.id, q.raw_hash, q.observed_at, q.provider_timestamp) for q in quotes]
        snap, report = build_consensus(
            s, canonical_game_id=GAME, market="SPREAD", as_of_at=NOW,
            kickoff_utc=KICK, cohort=Cohort.BURN_IN,
        )
        assert snap is None
        assert report.rejected_unverified_price_time == 6 and report.eligible == 0
        assert CONSENSUS_METHOD_VERSION == "consensus-v3"
        assert original == [(q.id, q.raw_hash, q.observed_at, q.provider_timestamp)
                            for q in s.scalars(select(OddsQuote))]
    body = api.get("/v1/forward/live").json()["games"][0]
    assert all(q["price_origin"] == "ESPN_PUBLISHED_CLOSE_OR_SUMMARY" for q in body["quotes"])
    assert any("without verified" in reason for reason in body["markets"]["SPREAD"]["reasons"])


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
