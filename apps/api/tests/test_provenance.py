"""Where a market record came from, and whether it can lie about it.

A fixture payload and a live provider response are structurally identical.
The only thing separating them is what the capture recorded at the time,
so provenance has to be stored per row, required at the write, and derived
honestly through every step that builds on it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from fde_api.db.forward_models import ConsensusSnapshot, OddsQuote, ScheduleObservation
from fde_api.db.models import Base
from fde_api.forward.cohort import (
    CAPTURABLE_PROVIDER_MODES,
    CohortViolationError,
    ProviderMode,
    assert_capturable,
    combine_provider_modes,
    is_live_provider_data,
)
from fde_api.forward.consensus import build_all_consensus_for_game
from fde_api.forward.health import run_health_checks
from fde_api.forward.modes import DataMode
from fde_api.forward.odds import capture_odds

KICK = datetime(2026, 9, 13, 17, 0, tzinfo=UTC)
NOW = KICK - timedelta(days=2)
GAME = "2026_02_KC_BUF"


def _payload(ts: datetime, books: tuple[str, ...] = ("draftkings", "fanduel", "betmgm")) -> list[dict]:
    def book(key: str, adj: float) -> dict:
        return {"key": key, "last_update": ts.isoformat(), "markets": [
            {"key": "spreads", "outcomes": [
                {"name": "Buffalo Bills", "price": -110, "point": -2.5 + adj},
                {"name": "Kansas City Chiefs", "price": -110, "point": 2.5 - adj}]}]}
    return [{"id": "evt1", "commence_time": KICK.isoformat(),
             "home_team": "Buffalo Bills", "away_team": "Kansas City Chiefs",
             "bookmakers": [book(b, i * 0.5) for i, b in enumerate(books)]}]


@pytest.fixture()
def session(tmp_path, monkeypatch):
    from fde_api.config import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine, future=True)()
    s.add(ScheduleObservation(
        data_mode=DataMode.LIVE_RESEARCH.value, canonical_game_id=GAME,
        provider="nflverse", provider_game_id=GAME, season=2026, season_type="REG",
        week=2, home_team_id="BUF", away_team_id="KC", kickoff_utc=KICK,
        game_status="SCHEDULED", content_hash="h", observed_at=NOW - timedelta(days=5),
    ))
    s.commit()
    yield s
    s.close()


class TestCombiningProvenance:
    def test_a_single_source_keeps_its_mode(self) -> None:
        assert combine_provider_modes(["LIVE"]) is ProviderMode.LIVE
        assert combine_provider_modes(["FIXTURE"]) is ProviderMode.FIXTURE

    def test_mixed_sources_are_mixed_not_live(self) -> None:
        """The whole point: one fixture quote cannot be laundered into a
        record that reads as live market data."""
        assert combine_provider_modes(["LIVE", "FIXTURE"]) is ProviderMode.MIXED
        assert not is_live_provider_data(combine_provider_modes(["LIVE", "FIXTURE"]))

    def test_unknown_dominates_mixed(self) -> None:
        """If any input's origin is unrecorded, the conclusion is unrecorded
        rather than a confident 'mixed'."""
        assert combine_provider_modes(
            ["LIVE", "FIXTURE", "UNKNOWN_LEGACY"]
        ) is ProviderMode.UNKNOWN_LEGACY

    def test_no_sources_is_unknown_not_live(self) -> None:
        """Absence of evidence about provenance is not evidence of live
        provenance - defaulting to LIVE here is the whole failure mode."""
        assert combine_provider_modes([]) is ProviderMode.UNKNOWN_LEGACY

    def test_order_does_not_matter(self) -> None:
        assert (combine_provider_modes(["FIXTURE", "LIVE"])
                is combine_provider_modes(["LIVE", "FIXTURE"]))


class TestOnlyRealOriginsMayBeCaptured:
    def test_capturable_modes_are_the_three_real_sources(self) -> None:
        assert {
            ProviderMode.FIXTURE, ProviderMode.SANDBOX, ProviderMode.LIVE
        } == CAPTURABLE_PROVIDER_MODES

    @pytest.mark.parametrize("mode", [
        ProviderMode.MIXED, ProviderMode.UNKNOWN_LEGACY,
        ProviderMode.KEY_MISSING, ProviderMode.QUOTA_EXHAUSTED,
        ProviderMode.UNAVAILABLE,
    ])
    def test_conclusions_may_not_be_captured_directly(self, mode) -> None:
        """MIXED and UNKNOWN_LEGACY are conclusions, not origins; the
        others describe a failure to obtain data, so there is no data."""
        with pytest.raises(CohortViolationError):
            assert_capturable(mode)

    def test_capture_rejects_a_non_origin(self, session: Session) -> None:
        with pytest.raises(CohortViolationError):
            capture_odds(session, _payload(NOW), provider_mode=ProviderMode.MIXED,
                         observed_at=NOW)


class TestCaptureStoresProvenance:
    def test_fixture_capture_is_recorded_as_fixture(self, session: Session) -> None:
        capture_odds(session, _payload(NOW), provider_mode=ProviderMode.FIXTURE,
                     observed_at=NOW)
        session.commit()
        modes = set(session.scalars(select(OddsQuote.provider_mode)))
        assert modes == {"FIXTURE"}

    def test_live_capture_is_recorded_as_live(self, session: Session) -> None:
        capture_odds(session, _payload(NOW), provider_mode=ProviderMode.LIVE,
                     observed_at=NOW)
        session.commit()
        modes = set(session.scalars(select(OddsQuote.provider_mode)))
        assert modes == {"LIVE"}

    def test_provenance_has_no_default(self) -> None:
        """A default of LIVE would silently mislabel every caller that
        forgot, which is exactly the bug this column exists to prevent."""
        import inspect

        sig = inspect.signature(capture_odds)
        assert sig.parameters["provider_mode"].default is inspect.Parameter.empty


class TestConsensusInheritsProvenance:
    def _build(self, session: Session, mode: ProviderMode) -> None:
        capture_odds(session, _payload(NOW), provider_mode=mode, observed_at=NOW)
        session.commit()
        build_all_consensus_for_game(
            session, canonical_game_id=GAME, kickoff_utc=KICK,
            as_of_at=NOW + timedelta(minutes=1), data_mode=DataMode.LIVE_RESEARCH,
        )
        session.commit()

    def test_fixture_quotes_make_a_fixture_consensus(self, session: Session) -> None:
        self._build(session, ProviderMode.FIXTURE)
        modes = set(session.scalars(select(ConsensusSnapshot.provider_mode)))
        assert modes == {"FIXTURE"}, modes

    def test_live_quotes_make_a_live_consensus(self, session: Session) -> None:
        self._build(session, ProviderMode.LIVE)
        modes = set(session.scalars(select(ConsensusSnapshot.provider_mode)))
        assert modes == {"LIVE"}, modes

    def test_mixed_quotes_make_a_mixed_consensus(self, session: Session) -> None:
        """Two captures of different origin feeding one consensus must not
        produce a snapshot that reads as live."""
        capture_odds(session, _payload(NOW, ("draftkings", "fanduel")),
                     provider_mode=ProviderMode.LIVE, observed_at=NOW)
        capture_odds(session, _payload(NOW + timedelta(seconds=1), ("betmgm", "caesars")),
                     provider_mode=ProviderMode.FIXTURE, observed_at=NOW)
        session.commit()
        build_all_consensus_for_game(
            session, canonical_game_id=GAME, kickoff_utc=KICK,
            as_of_at=NOW + timedelta(minutes=1), data_mode=DataMode.LIVE_RESEARCH,
        )
        session.commit()
        modes = set(session.scalars(select(ConsensusSnapshot.provider_mode)))
        assert modes == {"MIXED"}, modes
        assert not is_live_provider_data(ProviderMode.MIXED)


class TestHealthSurfacesProvenance:
    def _checks(self, session: Session, *, provider_mode: ProviderMode) -> dict:
        report = run_health_checks(
            session, data_mode=DataMode.LIVE_RESEARCH, provider_mode=provider_mode,
            now=NOW,
        )
        return {c["id"]: c for c in report["checks"]}

    def test_live_claim_under_fixture_mode_is_critical(self, session: Session) -> None:
        """The alarming case: rows say LIVE but the service is on fixtures.
        Something captured a test payload as live market data."""
        capture_odds(session, _payload(NOW), provider_mode=ProviderMode.LIVE,
                     observed_at=NOW)
        session.commit()
        c = self._checks(session, provider_mode=ProviderMode.FIXTURE)
        check = c["provenance_live_claim_without_live_provider"]
        assert check["status"] != "OK"
        assert check["severity"] == "CRITICAL"
        assert check["suppresses_candidates"] is True

    def test_fixture_rows_under_fixture_mode_are_consistent(self, session: Session) -> None:
        capture_odds(session, _payload(NOW), provider_mode=ProviderMode.FIXTURE,
                     observed_at=NOW)
        session.commit()
        c = self._checks(session, provider_mode=ProviderMode.FIXTURE)
        assert c["provenance_live_claim_without_live_provider"]["status"] == "OK"

    def test_fixture_data_in_live_research_warns_without_suppressing(
        self, session: Session
    ) -> None:
        """Burn-in runs the live-research pipeline on fixtures by design, so
        this reports the mixture rather than gating on it."""
        capture_odds(session, _payload(NOW), provider_mode=ProviderMode.FIXTURE,
                     observed_at=NOW)
        session.commit()
        c = self._checks(session, provider_mode=ProviderMode.FIXTURE)
        check = c["provenance_non_live_in_live_research"]
        assert check["status"] == "DEGRADED"
        assert check["severity"] == "WARNING"
        assert check["suppresses_candidates"] is False

    def test_unrecorded_provenance_is_reported(self, session: Session) -> None:
        """Rows predating the column are UNKNOWN_LEGACY and must be visible
        rather than quietly counted as live."""
        capture_odds(session, _payload(NOW), provider_mode=ProviderMode.FIXTURE,
                     observed_at=NOW)
        session.commit()
        session.execute(
            OddsQuote.__table__.update().values(provider_mode="UNKNOWN_LEGACY")
        )
        session.commit()
        c = self._checks(session, provider_mode=ProviderMode.FIXTURE)
        assert c["provenance_unrecorded"]["status"] == "DEGRADED"

    def test_clean_live_capture_passes_every_provenance_check(
        self, session: Session
    ) -> None:
        capture_odds(session, _payload(NOW), provider_mode=ProviderMode.LIVE,
                     observed_at=NOW)
        session.commit()
        c = self._checks(session, provider_mode=ProviderMode.LIVE)
        for cid in ("provenance_live_claim_without_live_provider",
                    "provenance_unrecorded",
                    "provenance_non_live_in_live_research"):
            assert c[cid]["status"] == "OK", (cid, c[cid])


class TestVocabularyIsEnforcedAtTheDatabase:
    def test_an_invented_mode_is_rejected(self, session: Session) -> None:
        """The CHECK constraint is the backstop for anything that bypasses
        the Python guard."""
        from sqlalchemy.exc import IntegrityError

        capture_odds(session, _payload(NOW), provider_mode=ProviderMode.FIXTURE,
                     observed_at=NOW)
        session.commit()
        with pytest.raises(IntegrityError):
            session.execute(
                OddsQuote.__table__.update().values(provider_mode="TOTALLY_LIVE")
            )
            session.commit()
        session.rollback()
