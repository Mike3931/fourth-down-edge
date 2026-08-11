"""The two endpoints behind the screens Phase 3 named and never built.

`/v1/forward/candidates` and `/v1/forward/performance`.

Both READ the forward ledger. Neither evaluates anything, and that is the
property most worth pinning: an endpoint that computed a fresh candidate
on GET would produce a recommendation with no scheduler run behind it, no
policy attached, and no place in the record chain — exactly the shape
`chain.py` exists to detect.

The other property worth pinning is the difference between "the engine
looked and found nothing" and "the engine declined to look". Both render
as an empty list; only one of them means there were no opportunities.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from fde_api.db.forward_models import ForwardLedgerEntry, ScheduleObservation
from fde_api.db.models import Base

NOW = datetime.now(UTC)
KICKOFF = NOW + timedelta(days=3)
GAME = "2026_02_KC_BUF"


@pytest.fixture()
def client(monkeypatch, tmp_path) -> TestClient:
    from fde_api.config import settings

    url = f"sqlite:///{(tmp_path / 'c.db').as_posix()}"
    monkeypatch.setattr(settings, "database_url", url)
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setenv("FDE_ALLOW_UNAUTHENTICATED", "1")

    import fde_api.db.engine as db_engine
    from fde_api.api.main import app

    db_engine.get_engine.cache_clear()  # type: ignore[attr-defined]
    Base.metadata.create_all(db_engine.get_engine())
    yield TestClient(app)
    db_engine.get_engine.cache_clear()  # type: ignore[attr-defined]


def _seed(session: Session, *, status: str = "RESEARCH_CANDIDATE") -> None:
    session.add(ScheduleObservation(
        canonical_game_id=GAME, data_mode="LIVE_RESEARCH", season=2026,
        season_type="REG", week=2, away_team_id="KC", home_team_id="BUF",
        kickoff_utc=KICKOFF, game_status="SCHEDULED", provider="nflverse",
        provider_game_id="nflverse:2026_02_KC_BUF", content_hash="h1",
        observed_at=NOW - timedelta(days=1),
    ))
    session.add(ForwardLedgerEntry(
        data_mode="LIVE_RESEARCH", canonical_game_id=GAME,
        policy_version="ftp-2026-v1", model_version="market-residual-v1",
        horizon="PREGAME", market="SPREAD", selection="HOME", status=status,
        qualifying_line=-3.0, qualifying_american=-110,
        price_source="consensus", price_age_seconds=600,
        model_probability=0.58, break_even_probability=0.5238,
        reasons={"why": "edge above threshold"},
        as_of_at=NOW - timedelta(hours=1), created_at=NOW - timedelta(hours=1),
    ))
    session.commit()


def _session(client: TestClient) -> Session:
    import fde_api.db.engine as db_engine

    return sessionmaker(bind=db_engine.get_engine(), future=True)()


class TestTheEndpointsOnlyRead:
    def test_candidates_writes_nothing(self, client: TestClient) -> None:
        with _session(client) as s:
            _seed(s)
            before = len(list(s.scalars(select(ForwardLedgerEntry))))
        client.get("/v1/forward/candidates")
        with _session(client) as s:
            after = len(list(s.scalars(select(ForwardLedgerEntry))))
        assert after == before, "the endpoint evaluated something"

    def test_performance_writes_nothing(self, client: TestClient) -> None:
        with _session(client) as s:
            _seed(s)
            before = len(list(s.scalars(select(ForwardLedgerEntry))))
        client.get("/v1/forward/performance")
        with _session(client) as s:
            assert len(list(s.scalars(select(ForwardLedgerEntry)))) == before


class TestAClosedGateIsNotAnEmptyResult:
    def test_the_gate_reports_what_is_blocking(self, client: TestClient) -> None:
        """With no scheduler runs and no odds key, candidates are
        suppressed. The response must say so rather than return an empty
        list that reads as 'no opportunities'."""
        with _session(client) as s:
            _seed(s)
        body = client.get("/v1/forward/candidates").json()
        assert body["gate"]["open"] is False
        blocked = {b["check"] for b in body["gate"]["blocked_by"]}
        assert blocked, "a closed gate must name what closed it"
        assert all(b["remediation"] for b in body["gate"]["blocked_by"])

    def test_every_blocker_is_a_suppressing_health_check(
        self, client: TestClient
    ) -> None:
        """The gate applies the same RULE the evaluation path applies, not
        merely the same checks.

        This used to compare against `suppresses_candidates` alone, which
        `_fail()` derives from severity and which therefore includes every
        CRITICAL operational check. The evaluation path also requires the
        SCOPE to be one that may suppress, so the two disagreed and this
        test pinned the disagreement in place. See test_one_gate_not_two.py.
        """
        from fde_api.forward.health import may_suppress

        body = client.get("/v1/forward/candidates").json()
        health = client.get("/v1/forward/health").json()
        suppressing = {
            c["id"]
            for group in health["by_scope"].values()
            for c in group
            if c["suppresses_candidates"] and c["status"] != "OK" and may_suppress(c["id"])
        }
        assert {b["check"] for b in body["gate"]["blocked_by"]} == suppressing


class TestWhatTheCandidateListContains:
    def test_only_candidate_and_watch_rows_appear(self, client: TestClient) -> None:
        with _session(client) as s:
            _seed(s, status="PASS")
        body = client.get("/v1/forward/candidates").json()
        assert body["count"] == 0, "a PASS is not a candidate"

    def test_a_candidate_carries_its_price_age_and_policy(
        self, client: TestClient
    ) -> None:
        """Both matter for honesty: the age says the price may be gone, and
        the policy says which frozen rules admitted it."""
        with _session(client) as s:
            _seed(s)
        body = client.get("/v1/forward/candidates").json()
        assert body["count"] == 1
        row = body["candidates"][0]
        assert row["price_age_seconds"] == 600
        assert row["policy_version"] == "ftp-2026-v1"
        assert row["edge"] == pytest.approx(0.58 - 0.5238)

    def test_a_game_already_kicked_off_is_excluded(self, client: TestClient) -> None:
        """A candidate for a game in progress is not actionable and would
        only invite acting on a stale price."""
        with _session(client) as s:
            _seed(s)
            obs = s.scalars(select(ScheduleObservation)).first()
            assert obs is not None
            obs.kickoff_utc = NOW - timedelta(hours=2)
            s.commit()
        body = client.get("/v1/forward/candidates").json()
        assert body["count"] == 0

    def test_the_response_states_it_is_not_a_wager(self, client: TestClient) -> None:
        body = client.get("/v1/forward/candidates").json()
        assert "not a wager" in body["not_a_claim"].lower()


class TestThePerformanceRecord:
    def test_it_reports_nothing_when_no_policy_is_frozen(
        self, client: TestClient
    ) -> None:
        body = client.get("/v1/forward/performance").json()
        assert body["cohorts"] == []

    def test_it_refuses_to_read_as_a_profitability_claim(
        self, client: TestClient
    ) -> None:
        body = client.get("/v1/forward/performance").json()
        assert "not evidence of profitability" in body["not_a_claim"].lower()
