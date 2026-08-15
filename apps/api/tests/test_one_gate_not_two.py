"""The screen and the engine must apply the same gate.

`CandidatesLive` exists to distinguish two states that both render as an
empty list — "the engine looked and found nothing" and "the engine
declined to look". It was asserting the wrong one.

Two rules were in play, and they disagree:

  * `HealthCheck.suppresses_candidates` is set from SEVERITY alone, in
    `_fail()`. Every CRITICAL check carries it, whatever it is about.
  * `evaluation_health_context` — the one `handlers.py` actually runs
    before an evaluation — additionally requires the check's SCOPE to be
    one that may suppress. An OPERATIONAL_PLATFORM failure is recorded as
    degraded and the evaluation proceeds.

`_candidate_gate` used the first, under a docstring promising it used the
same rule as the engine "so the screen cannot show a rosier answer than
the engine enforces". The divergence ran the other way: with no odds key
and no scheduler the engine evaluated four games and wrote three
DATA_INCOMPLETE rows, while the screen reported a closed gate — telling
the reader nothing had been attempted when in fact it had, and the answer
was that the inputs were not there.

Losing the operational failures would be the opposite error, so they are
still reported. They are reported as what they are.

Both classes below fail against the single-rule gate.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from fde_api.db.forward_models import ForwardLedgerEntry, ScheduleObservation
from fde_api.db.models import Base
from fde_api.forward.health import evaluation_health_context, run_health_checks, scope_of

NOW = datetime.now(UTC)
GAME = "2026_02_KC_BUF"


@pytest.fixture()
def client(monkeypatch, tmp_path) -> TestClient:
    from fde_api.config import settings

    monkeypatch.setattr(settings, "database_url", f"sqlite:///{(tmp_path / 'g.db').as_posix()}")
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setenv("FDE_ALLOW_UNAUTHENTICATED", "1")

    import fde_api.db.engine as db_engine
    from fde_api.api.main import app

    db_engine.get_engine.cache_clear()  # type: ignore[attr-defined]
    Base.metadata.create_all(db_engine.get_engine())
    yield TestClient(app)
    db_engine.get_engine.cache_clear()  # type: ignore[attr-defined]


def _session() -> Session:
    import fde_api.db.engine as db_engine

    return sessionmaker(bind=db_engine.get_engine(), future=True)()


def _seed() -> None:
    with _session() as s:
        s.add(ScheduleObservation(
            canonical_game_id=GAME, data_mode="LIVE_RESEARCH", season=2026,
            season_type="REG", week=2, away_team_id="KC", home_team_id="BUF",
            kickoff_utc=NOW + timedelta(days=3), game_status="SCHEDULED",
            provider="nflverse", provider_game_id="nflverse:2026_02_KC_BUF",
            content_hash="h1", observed_at=NOW - timedelta(days=1),
        ))
        s.add(ForwardLedgerEntry(
            data_mode="LIVE_RESEARCH", cohort="burn_in", canonical_game_id=GAME,
            policy_version="ftp-2026-v1", model_version="market-residual-v1",
            horizon="PREGAME", market="SPREAD", selection="HOME",
            status="RESEARCH_CANDIDATE", qualifying_line=-3.0,
            qualifying_american=-110, price_source="consensus",
            price_age_seconds=600, model_probability=0.58,
            break_even_probability=0.5238, reasons={"why": "edge above threshold"},
            as_of_at=NOW - timedelta(hours=1), created_at=NOW - timedelta(hours=1),
        ))
        s.commit()


class TestTheGateMatchesTheOneTheEngineApplies:
    def test_a_platform_failure_does_not_close_the_gate(self, client: TestClient) -> None:
        """No odds key and no scheduler run: both CRITICAL, both
        OPERATIONAL_PLATFORM. The engine evaluates anyway, so the screen
        may not claim it refused to."""
        _seed()
        gate = client.get("/v1/forward/candidates").json()["gate"]
        blocked = {b["check"] for b in gate["blocked_by"]}
        assert "odds_key_configured" not in blocked
        assert "scheduler_running" not in blocked

    def test_the_gate_agrees_with_evaluation_health_context(
        self, client: TestClient
    ) -> None:
        """The property, stated directly: one rule, two readers."""
        _seed()
        gate = client.get("/v1/forward/candidates").json()["gate"]
        with _session() as s:
            ctx = evaluation_health_context(run_health_checks(s))
        assert {b["check"] for b in gate["blocked_by"]} == set(ctx.failing_check_ids)
        assert gate["open"] is not ctx.suppressed

    def test_no_blocker_comes_from_a_non_suppressing_scope(
        self, client: TestClient
    ) -> None:
        from fde_api.forward.health import SUPPRESSING_SCOPES

        _seed()
        gate = client.get("/v1/forward/candidates").json()["gate"]
        for blocker in gate["blocked_by"]:
            assert scope_of(blocker["check"]) in SUPPRESSING_SCOPES, blocker["check"]


class TestOperationalFailuresAreStillReported:
    def test_they_appear_under_their_own_heading(self, client: TestClient) -> None:
        """Demoting them must not mean hiding them. A missing odds key is
        the single most likely reason a real user sees nothing."""
        _seed()
        gate = client.get("/v1/forward/candidates").json()["gate"]
        degraded = {d["check"] for d in gate["degraded_by"]}
        assert {"odds_key_configured", "scheduler_running"} <= degraded

    def test_each_one_still_carries_its_remediation(self, client: TestClient) -> None:
        _seed()
        gate = client.get("/v1/forward/candidates").json()["gate"]
        assert gate["degraded_by"]
        assert all(d["remediation"] for d in gate["degraded_by"])

    def test_the_two_lists_never_overlap(self, client: TestClient) -> None:
        _seed()
        gate = client.get("/v1/forward/candidates").json()["gate"]
        blocked = {b["check"] for b in gate["blocked_by"]}
        degraded = {d["check"] for d in gate["degraded_by"]}
        assert not (blocked & degraded)

    def test_openness_is_decided_by_the_blocking_list_alone(
        self, client: TestClient
    ) -> None:
        """The state this whole distinction exists for: something is
        wrong, the list is empty anyway, and whether the gate is open
        depends only on whether anything in a suppressing scope failed.
        Degradation never closes it and never opens it."""
        _seed()
        gate = client.get("/v1/forward/candidates").json()["gate"]
        assert gate["degraded_by"], "the fixture has no odds key; that is degradation"
        assert gate["open"] == (not gate["blocked_by"])
