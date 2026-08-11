"""An observation cannot have happened after the moment it was recorded.

`observed_at` is the engine's own clock, not a provider's. The point-in-
time guards enforce `observed_at <= as_of_at` relative to a *cutoff*, and
that is a different question: it is satisfied trivially by a record dated
in the future, because such a record is excluded from every snapshot taken
before its timestamp. Nothing asks whether the timestamp is possible.

So a future-dated observation is invisible, and then stops being. It sits
outside every cutoff until wall-clock time passes it, at which point it
becomes an ordinary fresh quote — the youngest in the window, the one the
consensus prefers — for a game that is about to kick off. Its whole life
is spent looking correct.

The development database holds twenty-four of these: quotes stamped
2026-09-10 22:55 against a 2026-08-11 clock, exactly 100 minutes before
the kickoff of 2026_01_SF_LA. They are fixture rows, and on 10 September
they would have presented as a live capture from a hundred minutes ago.

This check reports them. It does not delete them and it does not suppress
candidates: a governance CRITICAL that closes the gate on every game
because one row somewhere is misdated is the false-blocker pattern this
codebase has already had to unwind twice. It names the games so the
reader can act on the ones affected.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session, sessionmaker

from fde_api.db.forward_models import ConsensusSnapshot, OddsQuote, ScheduleObservation
from fde_api.db.models import Base
from fde_api.forward.health import (
    CHECK_SCOPES,
    HealthScope,
    Status,
    run_health_checks,
    scope_of,
)

NOW = datetime(2026, 8, 11, 20, 0, tzinfo=UTC)
CHECK = "observation_instant_in_future"


@pytest.fixture()
def session(tmp_path, monkeypatch) -> Session:
    from fde_api.config import settings

    monkeypatch.setattr(settings, "database_url", f"sqlite:///{(tmp_path / 'h.db').as_posix()}")
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    import fde_api.db.engine as db_engine

    db_engine.get_engine.cache_clear()  # type: ignore[attr-defined]
    engine = db_engine.get_engine()
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, future=True)() as s:
        yield s
    db_engine.get_engine.cache_clear()  # type: ignore[attr-defined]


def _quote(s: Session, observed_at: datetime, game: str = "2026_01_SF_LA") -> None:
    s.add(OddsQuote(
        data_mode="LIVE_RESEARCH", canonical_game_id=game, provider="the-odds-api",
        provider_mode="LIVE", sportsbook="draftkings", market="SPREAD",
        selection="HOME", line=-2.5, american=-110, decimal_odds=1.909,
        is_live=False, observed_at=observed_at, raw_hash=f"h{observed_at.isoformat()}{game}",
    ))
    s.commit()


def _find(session: Session, now: datetime = NOW):
    for check in run_health_checks(session, now=now)["checks"]:
        if check["id"] == CHECK:
            return check
    raise AssertionError(f"{CHECK} is not in the health report")


class TestTheCheckIsRegistered:
    def test_it_declares_a_scope(self) -> None:
        assert CHECK in CHECK_SCOPES
        assert scope_of(CHECK) is HealthScope.GOVERNANCE_INTEGRITY

    def test_it_always_appears_even_when_it_passes(self, session: Session) -> None:
        """A check that only appears on failure cannot be seen to be
        watching."""
        assert _find(session)["status"] == Status.OK.value


class TestWhatItCatches:
    def test_a_quote_dated_after_now_fails_it(self, session: Session) -> None:
        _quote(session, NOW + timedelta(days=30))
        assert _find(session)["status"] != Status.OK.value

    def test_a_quote_dated_before_now_does_not(self, session: Session) -> None:
        _quote(session, NOW - timedelta(hours=2))
        assert _find(session)["status"] == Status.OK.value

    def test_the_present_instant_is_not_the_future(self, session: Session) -> None:
        _quote(session, NOW)
        assert _find(session)["status"] == Status.OK.value

    def test_a_consensus_snapshot_is_checked_too(self, session: Session) -> None:
        session.add(ConsensusSnapshot(
            data_mode="LIVE_RESEARCH", canonical_game_id="2026_01_SF_LA",
            market="SPREAD", method_version="consensus-v2", provider_mode="LIVE",
            median_line=-2.5, eligible_books=3, quote_ids={"quote_ids": [], "books": []},
            observed_at=NOW + timedelta(days=3),
        ))
        session.commit()
        assert _find(session)["status"] != Status.OK.value

    def test_a_schedule_observation_is_checked_too(self, session: Session) -> None:
        session.add(ScheduleObservation(
            canonical_game_id="2026_01_SF_LA", data_mode="LIVE_RESEARCH", season=2026,
            season_type="REG", week=1, away_team_id="SF", home_team_id="LA",
            kickoff_utc=NOW + timedelta(days=31), game_status="SCHEDULED",
            provider="nflverse", provider_game_id="nflverse:2026_01_SF_LA",
            content_hash="h1", observed_at=NOW + timedelta(days=1),
        ))
        session.commit()
        assert _find(session)["status"] != Status.OK.value

    def test_a_kickoff_in_the_future_is_not_a_violation(self, session: Session) -> None:
        """The guard that keeps this check from firing on every scheduled
        game: a kickoff is a planned event and belongs in the future. Only
        the OBSERVATION instant is constrained."""
        session.add(ScheduleObservation(
            canonical_game_id="2026_01_SF_LA", data_mode="LIVE_RESEARCH", season=2026,
            season_type="REG", week=1, away_team_id="SF", home_team_id="LA",
            kickoff_utc=NOW + timedelta(days=31), game_status="SCHEDULED",
            provider="nflverse", provider_game_id="nflverse:2026_01_SF_LA",
            content_hash="h1", observed_at=NOW - timedelta(days=10),
        ))
        session.commit()
        assert _find(session)["status"] == Status.OK.value


class TestWhatItSays:
    def test_it_names_the_affected_game(self, session: Session) -> None:
        """A count alone is unactionable — the reader cannot tell which
        screen is about to show a fixture as a live price."""
        _quote(session, NOW + timedelta(days=30), game="2026_01_SF_LA")
        check = _find(session)
        assert "2026_01_SF_LA" in str(check["detail"])

    def test_it_reports_how_far_ahead(self, session: Session) -> None:
        """"Some row is misdated" is not actionable; "by thirty days" says
        whether this is clock skew or seeded fixture data."""
        _quote(session, NOW + timedelta(days=30))
        assert "2026-09-10" in _find(session)["detail"]["furthest_ahead"]

    def test_it_counts_each_table_separately(self, session: Session) -> None:
        """Quotes, consensus snapshots and schedule rows have different
        remediations; one merged total hides which pipeline is wrong."""
        _quote(session, NOW + timedelta(days=30))
        detail = _find(session)["detail"]
        assert detail["by_table"]["odds_quotes"] == 1
        assert detail["by_table"].get("consensus_snapshots", 0) == 0


class TestAFutureRowIsNotFresh:
    """The second-order damage, seen on the real Data Health screen.

    Freshness is computed as `now - max(observed_at) < threshold`. A
    future timestamp makes that difference NEGATIVE, which is under every
    threshold, so `odds_freshness` reported OK — "newest quote
    2026-09-10T22:55:00+00:00" — with nothing captured for nine hours and
    a month-ahead fixture row supplying the maximum.

    A check that reads "OK" because a row is impossible is worse than one
    that reads FAILED, and this one is CRITICAL and gates on the answer.
    """

    def _check(self, session: Session, cid: str, now: datetime = NOW):
        for check in run_health_checks(session, now=now)["checks"]:
            if check["id"] == cid:
                return check
        raise AssertionError(cid)

    def test_a_future_quote_does_not_make_odds_look_fresh(
        self, session: Session
    ) -> None:
        _quote(session, NOW - timedelta(days=4))   # genuinely stale
        _quote(session, NOW + timedelta(days=30))  # impossible, and newest
        assert self._check(session, "odds_freshness")["status"] != Status.OK.value

    def test_the_reported_instant_is_one_that_has_happened(
        self, session: Session
    ) -> None:
        _quote(session, NOW - timedelta(minutes=30))
        _quote(session, NOW + timedelta(days=30))
        explanation = self._check(session, "odds_freshness")["explanation"]
        assert "2026-09-10" not in explanation

    def test_a_recent_quote_still_reads_fresh_beside_a_future_one(
        self, session: Session
    ) -> None:
        """The guard against over-correcting: the future row must be
        ignored, not treated as poison for the whole check."""
        _quote(session, NOW - timedelta(minutes=30))
        _quote(session, NOW + timedelta(days=30))
        assert self._check(session, "odds_freshness")["status"] == Status.OK.value

    def test_schedule_freshness_ignores_a_future_observation(
        self, session: Session
    ) -> None:
        for observed in (NOW - timedelta(days=9), NOW + timedelta(days=30)):
            session.add(ScheduleObservation(
                canonical_game_id="2026_01_SF_LA", data_mode="LIVE_RESEARCH",
                season=2026, season_type="REG", week=1, away_team_id="SF",
                home_team_id="LA", kickoff_utc=NOW + timedelta(days=31),
                game_status="SCHEDULED", provider="nflverse",
                provider_game_id=f"nflverse:{observed.isoformat()}",
                content_hash=f"h{observed.isoformat()}", observed_at=observed,
            ))
        session.commit()
        assert self._check(session, "schedule_freshness")["status"] != Status.OK.value


class TestItDoesNotCloseTheGate:
    def test_it_never_suppresses_candidates(self, session: Session) -> None:
        """One misdated row must not stop analysis of every other game.
        The check reports; the per-game inputs decide."""
        _quote(session, NOW + timedelta(days=30))
        assert _find(session)["suppresses_candidates"] is False

    def test_it_states_that_it_is_not_a_blocker(self, session: Session) -> None:
        _quote(session, NOW + timedelta(days=30))
        assert _find(session)["remediation"]
