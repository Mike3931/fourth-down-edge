"""Phase 3 — forward capture tests.

Covers policy immutability, schedule observation history, odds
idempotence and consensus eligibility, weather vintages, injury
point-in-time rules, availability v0, vintage immutability, CLV, and
mode separation.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker

from fde_api.db.forward_models import (
    ForwardLedgerEntry,
    OddsQuote,
    ScheduleObservation,
)
from fde_api.db.models import Base
from fde_api.forward.consensus import (
    RECOGNIZED_BOOKS,
    build_consensus,
    closing_consensus,
    latest_consensus_at,
    no_vig_two_way,
    select_eligible_quotes,
)
from fde_api.forward.injuries import (
    AvailabilityState,
    InjuryValidationError,
    SourceCategory,
    classify,
    observations_as_of,
    record_injury_observation,
    resolve_starting_qb,
)
from fde_api.forward.ledger import evaluate_candidate, prob_to_american, record_evaluation
from fde_api.forward.modes import DataMode, ModeMixingError, assert_single_mode, mode_banner
from fde_api.forward.odds import american_to_decimal, capture_odds
from fde_api.forward.policy import (
    PolicyImmutabilityError,
    build_default_policy,
    freeze_policy,
    load_policy,
    verify_all_policies,
)
from fde_api.forward.schedule import (
    current_schedule_state,
    ingest_schedule,
    record_status_change,
    schedule_history,
    schedule_state_as_of,
)
from fde_api.forward.venues import resolve_venue, seed_venues
from fde_api.forward.vintages import (
    VintageImmutabilityError,
    generate_vintage,
    horizon_cutoff,
)
from fde_api.pit.guards import LookaheadError

KICK = datetime(2026, 9, 13, 17, 0, tzinfo=UTC)


@pytest.fixture()
def fsession(tmp_path, monkeypatch) -> Session:
    from fde_api.config import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    engine = create_engine("sqlite://", future=True)

    @event.listens_for(engine, "connect")
    def _fk(dbapi_conn, _rec):
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine, future=True)()
    seed_venues(s)
    return s


def _policy(fsession: Session, version: str = "test-policy-v1"):
    p = build_default_policy(
        policy_version=version, start=date(2026, 9, 1), end=date(2027, 2, 28)
    )
    freeze_policy(fsession, p)
    return p


SCHEDULE_HEADER = (
    "game_id,season,game_type,week,gameday,weekday,gametime,away_team,away_score,home_team,"
    "home_score,location,result,total,overtime,old_game_id,gsis,nfl_detail_id,pfr,pff,espn,ftn,"
    "away_rest,home_rest,away_moneyline,home_moneyline,spread_line,away_spread_odds,"
    "home_spread_odds,total_line,under_odds,over_odds,div_game,roof,surface,temp,wind,"
    "away_qb_id,home_qb_id,away_qb_name,home_qb_name,away_coach,home_coach,referee,stadium_id,stadium"
)


def _sched_row(**over: str) -> str:
    base = {
        "game_id": "2026_02_KC_BUF", "season": "2026", "game_type": "REG", "week": "2",
        "gameday": "2026-09-13", "weekday": "Sunday", "gametime": "13:00",
        "away_team": "KC", "away_score": "", "home_team": "BUF", "home_score": "",
        "location": "Home", "result": "", "total": "", "overtime": "", "old_game_id": "",
        "gsis": "", "nfl_detail_id": "", "pfr": "", "pff": "", "espn": "", "ftn": "",
        "away_rest": "7", "home_rest": "7", "away_moneyline": "", "home_moneyline": "",
        "spread_line": "", "away_spread_odds": "", "home_spread_odds": "", "total_line": "",
        "under_odds": "", "over_odds": "", "div_game": "0", "roof": "outdoors",
        "surface": "a_turf", "temp": "", "wind": "", "away_qb_id": "", "home_qb_id": "",
        "away_qb_name": "", "home_qb_name": "", "away_coach": "", "home_coach": "",
        "referee": "", "stadium_id": "BUF00", "stadium": "Highmark Stadium",
    }
    base.update(over)
    return ",".join(base[k] for k in SCHEDULE_HEADER.split(","))


def _sched_csv(*rows: str) -> bytes:
    return ("\n".join([SCHEDULE_HEADER, *rows]) + "\n").encode()


# --------------------------------------------------------------------------- #
# Policy
# --------------------------------------------------------------------------- #


class TestPolicy:
    def test_hash_is_deterministic_and_content_sensitive(self) -> None:
        a = build_default_policy(policy_version="p1", start=date(2026, 9, 1), end=date(2027, 2, 1))
        b = build_default_policy(policy_version="p1", start=date(2026, 9, 1), end=date(2027, 2, 1))
        assert a.policy_hash() == b.policy_hash()
        c = a.model_copy(update={"research_candidate_edge_threshold": 0.09})
        assert c.policy_hash() != a.policy_hash()

    def test_refreeze_identical_is_noop(self, fsession: Session) -> None:
        p = _policy(fsession)
        rec = freeze_policy(fsession, p)
        assert rec.policy_hash == p.policy_hash()

    def test_modifying_frozen_policy_is_refused(self, fsession: Session) -> None:
        p = _policy(fsession)
        tampered = p.model_copy(update={"research_candidate_edge_threshold": 0.01})
        with pytest.raises(PolicyImmutabilityError, match="already frozen"):
            freeze_policy(fsession, tampered)

    def test_stored_policy_round_trips_and_verifies(self, fsession: Session) -> None:
        p = _policy(fsession)
        loaded = load_policy(fsession, p.policy_version)
        assert loaded.policy_hash() == p.policy_hash()
        assert all(r["intact"] for r in verify_all_policies(fsession))

    def test_policy_carries_every_required_field(self, fsession: Session) -> None:
        p = _policy(fsession)
        fields = p.model_dump()
        # calibration_version is legitimately nullable: the frozen Phase 2
        # calibration targets spread-cover only, so a policy may declare none.
        required_present = (
            "model_version", "feature_set_version", "calibration_version",
            "recommendation_rule_version", "prediction_horizons", "market_selection",
            "research_candidate_edge_threshold", "price_staleness", "execution",
            "closing_line", "missing_data", "settlement", "exclusions", "bankroll",
            "start_date", "end_date", "code_commit", "dependency_lock_hash",
        )
        for f in required_present:
            assert f in fields, f
        required_non_null = tuple(x for x in required_present if x != "calibration_version")
        for f in required_non_null:
            assert getattr(p, f) is not None, f


# --------------------------------------------------------------------------- #
# Modes
# --------------------------------------------------------------------------- #


class TestModes:
    def test_banners_are_distinct(self) -> None:
        assert "DEMONSTRATION DATA" in mode_banner(DataMode.DEMO)
        assert "LIVE RESEARCH MODE" in mode_banner(DataMode.LIVE_RESEARCH)
        assert mode_banner(DataMode.DEMO) != mode_banner(DataMode.LIVE_RESEARCH)

    def test_mixing_modes_raises(self) -> None:
        with pytest.raises(ModeMixingError, match="never be combined"):
            assert_single_mode(["DEMO", "LIVE_RESEARCH"], "slate")

    def test_single_mode_passes(self) -> None:
        assert assert_single_mode(["LIVE_RESEARCH", "LIVE_RESEARCH"]) is DataMode.LIVE_RESEARCH

    def test_empty_is_rejected(self) -> None:
        with pytest.raises(ModeMixingError):
            assert_single_mode([])


# --------------------------------------------------------------------------- #
# Schedule
# --------------------------------------------------------------------------- #


class TestSchedule:
    def test_new_game_recorded(self, fsession: Session) -> None:
        r = ingest_schedule(fsession, _sched_csv(_sched_row()), season=2026)
        assert r.new_games == 1 and r.revisions == 0
        st = current_schedule_state(fsession, "2026_02_KC_BUF")
        assert st.home_team_id == "BUF" and st.away_team_id == "KC"
        assert st.season_type == "REG" and st.game_status == "SCHEDULED"
        assert st.venue_timezone == "America/New_York"

    def test_unchanged_reingest_writes_nothing(self, fsession: Session) -> None:
        payload = _sched_csv(_sched_row())
        ingest_schedule(fsession, payload, season=2026)
        r2 = ingest_schedule(fsession, payload, season=2026)
        assert r2.new_games == 0 and r2.revisions == 0 and r2.unchanged == 1

    def test_kickoff_revision_appends_and_preserves_original(self, fsession: Session) -> None:
        ingest_schedule(fsession, _sched_csv(_sched_row()), season=2026)
        original = current_schedule_state(fsession, "2026_02_KC_BUF").kickoff_utc
        ingest_schedule(
            fsession, _sched_csv(_sched_row(gametime="20:20")), season=2026,
            observed_at=datetime(2026, 9, 1, tzinfo=UTC),
        )
        hist = schedule_history(fsession, "2026_02_KC_BUF")
        assert len(hist) == 2
        assert hist[0].kickoff_utc == original  # untouched
        assert hist[1].supersedes_id == hist[0].id
        assert "kickoff" in hist[1].change_summary

    def test_postponement_recorded_as_new_observation(self, fsession: Session) -> None:
        ingest_schedule(fsession, _sched_csv(_sched_row()), season=2026)
        record_status_change(
            fsession, canonical_game_id="2026_02_KC_BUF", new_status="POSTPONED",
            reason="weather", observed_at=datetime(2026, 9, 12, tzinfo=UTC),
        )
        assert current_schedule_state(fsession, "2026_02_KC_BUF").game_status == "POSTPONED"
        assert schedule_history(fsession, "2026_02_KC_BUF")[0].game_status == "SCHEDULED"

    def test_cancellation_recorded(self, fsession: Session) -> None:
        ingest_schedule(fsession, _sched_csv(_sched_row()), season=2026)
        record_status_change(
            fsession, canonical_game_id="2026_02_KC_BUF", new_status="CANCELLED",
            reason="unplayable", observed_at=datetime(2026, 9, 12, tzinfo=UTC),
        )
        assert current_schedule_state(fsession, "2026_02_KC_BUF").game_status == "CANCELLED"

    def test_point_in_time_state_excludes_later_revision(self, fsession: Session) -> None:
        ingest_schedule(
            fsession, _sched_csv(_sched_row()), season=2026,
            observed_at=datetime(2026, 8, 1, tzinfo=UTC),
        )
        record_status_change(
            fsession, canonical_game_id="2026_02_KC_BUF", new_status="POSTPONED",
            reason="later", observed_at=datetime(2026, 9, 12, tzinfo=UTC),
        )
        earlier = schedule_state_as_of(
            fsession, "2026_02_KC_BUF", datetime(2026, 8, 15, tzinfo=UTC)
        )
        assert earlier.game_status == "SCHEDULED"  # postponement not yet known

    def test_neutral_site_ignores_home_stadium_id(self, fsession: Session) -> None:
        """The source puts the HOME club's stadium on international games."""
        ingest_schedule(
            fsession,
            _sched_csv(_sched_row(location="Neutral", stadium="Wembley Stadium", stadium_id="BUF00")),
            season=2026,
        )
        st = current_schedule_state(fsession, "2026_02_KC_BUF")
        assert st.neutral_site is True
        assert st.international is True
        assert st.stadium_id == "INT_WEMBLEY"  # NOT BUF00
        assert st.venue_timezone == "Europe/London"

    def test_canonical_id_is_stable_across_revisions(self, fsession: Session) -> None:
        ingest_schedule(fsession, _sched_csv(_sched_row()), season=2026)
        ingest_schedule(
            fsession, _sched_csv(_sched_row(gametime="16:25")), season=2026,
            observed_at=datetime(2026, 9, 2, tzinfo=UTC),
        )
        ids = {o.canonical_game_id for o in schedule_history(fsession, "2026_02_KC_BUF")}
        assert ids == {"2026_02_KC_BUF"}

    def test_venue_resolution_rules(self) -> None:
        assert resolve_venue(stadium_id="BUF00", stadium_name="Highmark", neutral_site=False).id == "BUF00"
        v = resolve_venue(stadium_id="BUF00", stadium_name="Wembley Stadium", neutral_site=True)
        assert v is not None and v.country == "GB"
        assert resolve_venue(stadium_id="BUF00", stadium_name="Nowhere", neutral_site=True) is None


# --------------------------------------------------------------------------- #
# Odds & consensus
# --------------------------------------------------------------------------- #


def _seed_game(fsession: Session) -> str:
    ingest_schedule(fsession, _sched_csv(_sched_row()), season=2026)
    return "2026_02_KC_BUF"


def _event(ts: str, books: list[tuple[str, float, int, int]]) -> list[dict]:
    return [{
        "id": "evt1",
        "commence_time": KICK.isoformat().replace("+00:00", "Z"),
        "home_team": "Buffalo Bills",
        "away_team": "Kansas City Chiefs",
        "bookmakers": [
            {"key": k, "last_update": ts, "markets": [
                {"key": "spreads", "outcomes": [
                    {"name": "Buffalo Bills", "price": hp, "point": sp},
                    {"name": "Kansas City Chiefs", "price": ap, "point": -sp}]}]}
            for k, sp, hp, ap in books
        ],
    }]


class TestOdds:
    def test_american_to_decimal(self) -> None:
        assert american_to_decimal(-110) == pytest.approx(1 + 100 / 110)
        assert american_to_decimal(150) == pytest.approx(2.5)

    def test_valid_quotes_written(self, fsession: Session) -> None:
        _seed_game(fsession)
        obs = KICK - timedelta(days=1)
        r = capture_odds(fsession, _event(obs.isoformat(), [("draftkings", -2.5, -110, -110)]), observed_at=obs)
        assert r.quotes_written == 2 and r.unmapped_events == []

    def test_duplicate_quote_not_rewritten(self, fsession: Session) -> None:
        _seed_game(fsession)
        obs = KICK - timedelta(days=1)
        payload = _event(obs.isoformat(), [("draftkings", -2.5, -110, -110)])
        capture_odds(fsession, payload, observed_at=obs)
        r2 = capture_odds(fsession, payload, observed_at=obs)
        assert r2.quotes_written == 0 and r2.duplicates_skipped == 2

    def test_invalid_price_rejected(self, fsession: Session) -> None:
        _seed_game(fsession)
        obs = KICK - timedelta(days=1)
        r = capture_odds(fsession, _event(obs.isoformat(), [("draftkings", -2.5, 5, -110)]), observed_at=obs)
        assert r.invalid_skipped >= 1

    def test_unmapped_event_recorded_not_guessed(self, fsession: Session) -> None:
        _seed_game(fsession)
        payload = _event((KICK - timedelta(days=1)).isoformat(), [("draftkings", -2.5, -110, -110)])
        payload[0]["home_team"] = "Springfield Atoms"
        r = capture_odds(fsession, payload)
        assert r.quotes_written == 0 and r.unmapped_events

    def test_spread_line_stored_home_relative(self, fsession: Session) -> None:
        gid = _seed_game(fsession)
        obs = KICK - timedelta(days=1)
        capture_odds(fsession, _event(obs.isoformat(), [("draftkings", -2.5, -110, -110)]), observed_at=obs)
        rows = fsession.scalars(
            select(OddsQuote).where(OddsQuote.canonical_game_id == gid)
        ).all()
        assert {r.selection: r.line for r in rows} == {"HOME": -2.5, "AWAY": -2.5}


class TestConsensus:
    def _capture(self, fsession: Session, obs: datetime, books: list[tuple[str, float, int, int]]) -> str:
        gid = _seed_game(fsession)
        capture_odds(fsession, _event(obs.isoformat(), books), observed_at=obs)
        return gid

    def test_no_vig_normalizes(self) -> None:
        a, b = no_vig_two_way(-110, -110)
        assert a == pytest.approx(0.5) and b == pytest.approx(0.5)
        assert sum(no_vig_two_way(-200, 170)) == pytest.approx(1.0)

    def test_median_line_and_book_count(self, fsession: Session) -> None:
        obs = KICK - timedelta(hours=2)
        gid = self._capture(fsession, obs, [
            ("draftkings", -2.5, -110, -110), ("fanduel", -2.5, -108, -112),
            ("betmgm", -3.0, -110, -110), ("caesars", -2.5, -112, -108)])
        snap, rep = build_consensus(
            fsession, canonical_game_id=gid, market="SPREAD",
            as_of_at=obs + timedelta(minutes=1), kickoff_utc=KICK)
        assert snap is not None
        assert snap.median_line == -2.5 and snap.eligible_books == 4
        assert rep.eligible == 8

    def test_unrecognized_book_excluded(self, fsession: Session) -> None:
        obs = KICK - timedelta(hours=2)
        gid = self._capture(fsession, obs, [
            ("draftkings", -2.5, -110, -110), ("fanduel", -2.5, -110, -110),
            ("betmgm", -2.5, -110, -110), ("totally_unknown_book", -9.0, -110, -110)])
        snap, rep = build_consensus(
            fsession, canonical_game_id=gid, market="SPREAD",
            as_of_at=obs + timedelta(minutes=1), kickoff_utc=KICK)
        assert snap.eligible_books == 3 and rep.rejected_unknown_book == 2
        assert "totally_unknown_book" not in snap.quote_ids["books"]

    def test_too_few_books_yields_none(self, fsession: Session) -> None:
        obs = KICK - timedelta(hours=2)
        gid = self._capture(fsession, obs, [("draftkings", -2.5, -110, -110)])
        snap, rep = build_consensus(
            fsession, canonical_game_id=gid, market="SPREAD",
            as_of_at=obs + timedelta(minutes=1), kickoff_utc=KICK, min_books=3)
        assert snap is None and rep.reasons

    def test_stale_quotes_rejected(self, fsession: Session) -> None:
        obs = KICK - timedelta(days=3)
        gid = self._capture(fsession, obs, [
            ("draftkings", -2.5, -110, -110), ("fanduel", -2.5, -110, -110),
            ("betmgm", -2.5, -110, -110)])
        snap, rep = build_consensus(
            fsession, canonical_game_id=gid, market="SPREAD",
            as_of_at=KICK - timedelta(hours=1), kickoff_utc=KICK, max_age_minutes=60)
        assert snap is None and rep.rejected_stale > 0

    def test_future_quotes_never_enter_earlier_consensus(self, fsession: Session) -> None:
        late = KICK - timedelta(minutes=30)
        gid = self._capture(fsession, late, [
            ("draftkings", -2.5, -110, -110), ("fanduel", -2.5, -110, -110),
            ("betmgm", -2.5, -110, -110)])
        snap, _ = build_consensus(
            fsession, canonical_game_id=gid, market="SPREAD",
            as_of_at=KICK - timedelta(days=2), kickoff_utc=KICK)
        assert snap is None

    def test_consensus_does_not_replace_raw_quotes(self, fsession: Session) -> None:
        obs = KICK - timedelta(hours=2)
        gid = self._capture(fsession, obs, [
            ("draftkings", -2.5, -110, -110), ("fanduel", -2.5, -110, -110),
            ("betmgm", -2.5, -110, -110)])
        before = len(fsession.scalars(select(OddsQuote)).all())
        build_consensus(fsession, canonical_game_id=gid, market="SPREAD",
                        as_of_at=obs + timedelta(minutes=1), kickoff_utc=KICK)
        assert len(fsession.scalars(select(OddsQuote)).all()) == before

    def test_closing_selected_by_rule_not_by_outcome(self, fsession: Session) -> None:
        gid = _seed_game(fsession)
        for mins, line in ((120, -2.5), (20, -3.5), (5, -4.0)):
            obs = KICK - timedelta(minutes=mins)
            capture_odds(fsession, _event(obs.isoformat(), [
                ("draftkings", line, -110, -110), ("fanduel", line, -110, -110),
                ("betmgm", line, -110, -110)]), observed_at=obs)
            build_consensus(fsession, canonical_game_id=gid, market="SPREAD",
                            as_of_at=obs, kickoff_utc=KICK)
        close = closing_consensus(fsession, canonical_game_id=gid, market="SPREAD",
                                  kickoff_utc=KICK, max_age_before_kickoff_minutes=30)
        assert close is not None and close.median_line == -4.0  # last within window

    def test_closing_capture_excluded_from_prediction_inputs(self, fsession: Session) -> None:
        gid = _seed_game(fsession)
        obs = KICK - timedelta(minutes=10)
        capture_odds(fsession, _event(obs.isoformat(), [
            ("draftkings", -6.0, -110, -110), ("fanduel", -6.0, -110, -110),
            ("betmgm", -6.0, -110, -110)]), observed_at=obs)
        build_consensus(fsession, canonical_game_id=gid, market="SPREAD", as_of_at=obs,
                        kickoff_utc=KICK, is_closing_capture=True)
        assert latest_consensus_at(fsession, canonical_game_id=gid, market="SPREAD",
                                   as_of_at=KICK) is None

    def test_eligibility_counts_are_reported(self, fsession: Session) -> None:
        quotes: list[OddsQuote] = []
        eligible, rep = select_eligible_quotes(
            quotes, as_of_at=KICK, max_age_minutes=60, kickoff_utc=KICK)
        assert eligible == [] and rep.considered == 0

    def test_recognized_books_is_nonempty(self) -> None:
        assert "draftkings" in RECOGNIZED_BOOKS and len(RECOGNIZED_BOOKS) > 5


# --------------------------------------------------------------------------- #
# Injuries & availability
# --------------------------------------------------------------------------- #


class TestInjuries:
    def _obs(self, fsession: Session, **kw):
        base = {
            "canonical_game_id": "2026_02_KC_BUF", "team_id": "BUF", "player_id": "00-0011111",
            "report_date": "2026-09-11", "observed_at": KICK - timedelta(days=2),
            "source_category": SourceCategory.OFFICIAL_VERIFIED, "practice_status": "FULL",
            "game_designation": "NONE", "now": KICK - timedelta(days=1),
        }
        base.update(kw)
        return record_injury_observation(fsession, **base)

    def test_new_report_recorded_verified(self, fsession: Session) -> None:
        o = self._obs(fsession)
        assert o.verification_status == "VERIFIED"

    def test_revision_supersedes_without_overwriting(self, fsession: Session) -> None:
        first = self._obs(fsession)
        second = self._obs(fsession, game_designation="QUESTIONABLE",
                           observed_at=KICK - timedelta(days=1))
        assert first.superseded_by_id == second.id
        assert first.game_designation == "NONE"  # original intact

    def test_future_observation_rejected(self, fsession: Session) -> None:
        with pytest.raises(LookaheadError, match="after now"):
            self._obs(fsession, observed_at=KICK, now=KICK - timedelta(days=3))

    def test_player_must_be_id_not_name(self, fsession: Session) -> None:
        with pytest.raises(InjuryValidationError, match="never matched by name"):
            self._obs(fsession, player_id="  ")

    def test_invalid_designation_rejected(self, fsession: Session) -> None:
        with pytest.raises(InjuryValidationError):
            self._obs(fsession, game_designation="PROBABLY_FINE")

    def test_unverified_excluded_from_production_features(self, fsession: Session) -> None:
        self._obs(fsession, source_category=SourceCategory.UNVERIFIED, player_id="00-0022222")
        prod = observations_as_of(
            fsession, canonical_game_id="2026_02_KC_BUF", as_of_at=KICK, production_only=True)
        allobs = observations_as_of(
            fsession, canonical_game_id="2026_02_KC_BUF", as_of_at=KICK, production_only=False)
        assert prod == [] and len(allobs) == 1

    def test_observation_after_cutoff_invisible(self, fsession: Session) -> None:
        self._obs(fsession, observed_at=KICK - timedelta(hours=1), now=KICK)
        assert observations_as_of(
            fsession, canonical_game_id="2026_02_KC_BUF",
            as_of_at=KICK - timedelta(days=2)) == []

    @pytest.mark.parametrize(
        ("designation", "practice", "expected"),
        [
            ("OUT", "DNP", AvailabilityState.CONFIRMED_INACTIVE),
            ("DOUBTFUL", "DNP", AvailabilityState.DOUBTFUL),
            ("QUESTIONABLE", "DNP", AvailabilityState.GAME_TIME_DECISION),
            ("QUESTIONABLE", "LIMITED", AvailabilityState.ACTIVE_WITH_RESTRICTION_RISK),
            ("NONE", "FULL", AvailabilityState.EXPECTED_ACTIVE),
            ("NONE", "DNP", AvailabilityState.GAME_TIME_DECISION),
        ],
    )
    def test_classification(self, fsession: Session, designation, practice, expected) -> None:
        o = self._obs(fsession, game_designation=designation, practice_status=practice)
        assert classify(o) is expected

    def test_unknown_when_no_observation(self) -> None:
        assert classify(None) is AvailabilityState.UNKNOWN

    def test_qb_unresolved_when_game_time_decision(self, fsession: Session) -> None:
        self._obs(fsession, player_id="00-0099999", game_designation="QUESTIONABLE",
                  practice_status="DNP")
        r = resolve_starting_qb(
            fsession, canonical_game_id="2026_02_KC_BUF", team_id="BUF",
            expected_qb_id="00-0099999", as_of_at=KICK)
        assert r.resolved is False and "not modeled" in r.reason

    def test_qb_unresolved_when_no_expected_starter(self, fsession: Session) -> None:
        r = resolve_starting_qb(
            fsession, canonical_game_id="2026_02_KC_BUF", team_id="BUF",
            expected_qb_id=None, as_of_at=KICK)
        assert r.resolved is False

    def test_qb_resolved_when_healthy(self, fsession: Session) -> None:
        self._obs(fsession, player_id="00-0088888", game_designation="NONE", practice_status="FULL")
        r = resolve_starting_qb(
            fsession, canonical_game_id="2026_02_KC_BUF", team_id="BUF",
            expected_qb_id="00-0088888", as_of_at=KICK)
        assert r.resolved is True

    def test_availability_ranges_never_false_precision(self, fsession: Session) -> None:
        from fde_api.forward.injuries import assess_player

        self._obs(fsession, player_id="00-0077777", game_designation="QUESTIONABLE",
                  practice_status="LIMITED")
        a = assess_player(
            fsession, canonical_game_id="2026_02_KC_BUF", team_id="BUF",
            player_id="00-0077777", as_of_at=KICK, position="WR")
        assert a.active_prob_low < a.active_prob_high  # a range, not a point estimate
        assert a.confidence_tier in ("LOW", "MEDIUM", "HIGH", "NONE")
        assert a.reason


# --------------------------------------------------------------------------- #
# Vintages & ledger
# --------------------------------------------------------------------------- #


class TestVintages:
    def test_horizon_cutoffs_are_ordered(self) -> None:
        cuts = [
            horizon_cutoff(KICK, h)
            for h in ("OPENING", "EARLY_WEEK", "PRACTICE_UPDATE", "FINAL_INJURY_REPORT", "PREGAME")
        ]
        assert cuts == sorted(cuts)
        assert all(c < KICK for c in cuts)

    def test_closing_capture_cutoff_is_kickoff(self) -> None:
        assert horizon_cutoff(KICK, "CLOSING_CAPTURE_EVALUATION_ONLY") == KICK

    def test_future_cutoff_not_generatable(self, fsession: Session) -> None:
        p = _policy(fsession)
        _seed_game(fsession)
        game = current_schedule_state(fsession, "2026_02_KC_BUF")
        pred, inputs = generate_vintage(
            fsession, game=game, horizon="PREGAME", policy=p,
            moments=(3.0, 13.0, 44.0, 10.0), now=KICK - timedelta(days=30))
        assert pred is None and "has not arrived" in inputs.warnings[0]

    def test_vintage_is_immutable(self, fsession: Session) -> None:
        p = _policy(fsession)
        _seed_game(fsession)
        game = current_schedule_state(fsession, "2026_02_KC_BUF")
        after = horizon_cutoff(KICK, "OPENING") + timedelta(minutes=1)
        generate_vintage(fsession, game=game, horizon="OPENING", policy=p,
                         moments=(3.0, 13.0, 44.0, 10.0), now=after)
        with pytest.raises(VintageImmutabilityError, match="immutable"):
            generate_vintage(fsession, game=game, horizon="OPENING", policy=p,
                             moments=(9.0, 13.0, 44.0, 10.0), now=after)

    def test_identical_regeneration_is_idempotent(self, fsession: Session) -> None:
        p = _policy(fsession)
        _seed_game(fsession)
        game = current_schedule_state(fsession, "2026_02_KC_BUF")
        after = horizon_cutoff(KICK, "OPENING") + timedelta(minutes=1)
        a, _ = generate_vintage(fsession, game=game, horizon="OPENING", policy=p,
                                moments=(3.0, 13.0, 44.0, 10.0), now=after)
        b, _ = generate_vintage(fsession, game=game, horizon="OPENING", policy=p,
                                moments=(3.0, 13.0, 44.0, 10.0), now=after)
        assert a.id == b.id and a.artifact_hash == b.artifact_hash

    def test_vintage_records_lineage_and_warnings(self, fsession: Session) -> None:
        p = _policy(fsession)
        _seed_game(fsession)
        game = current_schedule_state(fsession, "2026_02_KC_BUF")
        after = horizon_cutoff(KICK, "OPENING") + timedelta(minutes=1)
        pred, _ = generate_vintage(fsession, game=game, horizon="OPENING", policy=p,
                                   moments=(3.0, 13.0, 44.0, 10.0), now=after)
        assert "schedule_observation_id" in pred.lineage
        assert pred.warnings["warnings"]  # missing inputs are declared, not hidden
        assert pred.data_completeness < 1.0

    def test_later_vintage_does_not_mutate_earlier(self, fsession: Session) -> None:
        p = _policy(fsession)
        _seed_game(fsession)
        game = current_schedule_state(fsession, "2026_02_KC_BUF")
        opening, _ = generate_vintage(
            fsession, game=game, horizon="OPENING", policy=p, moments=(3.0, 13.0, 44.0, 10.0),
            now=horizon_cutoff(KICK, "OPENING") + timedelta(minutes=1))
        opening_hash = opening.artifact_hash
        generate_vintage(
            fsession, game=game, horizon="PREGAME", policy=p, moments=(5.0, 13.0, 44.0, 10.0),
            now=horizon_cutoff(KICK, "PREGAME") + timedelta(minutes=1))
        fsession.refresh(opening)
        assert opening.artifact_hash == opening_hash


class TestLedgerAndCandidates:
    def test_prob_to_american_roundtrip(self) -> None:
        assert prob_to_american(0.5) in (-100, 100)
        assert prob_to_american(0.75) == -300
        assert prob_to_american(0.25) == 300

    def test_statuses_are_research_only(self, fsession: Session) -> None:
        p = _policy(fsession)
        for prob in (0.75, 0.545, 0.40):
            ev = evaluate_candidate(
                market="SPREAD", selection="HOME", line=-2.5, american=-110,
                model_probability=prob, price_source="consensus", price_age_seconds=60,
                policy=p, data_completeness=1.0)
            assert ev.status in ("RESEARCH_CANDIDATE", "WATCH", "PASS", "DATA_INCOMPLETE")
            assert ev.status != "BET"

    def test_high_edge_becomes_research_candidate(self, fsession: Session) -> None:
        p = _policy(fsession)
        ev = evaluate_candidate(
            market="SPREAD", selection="HOME", line=-2.5, american=-110,
            model_probability=0.70, price_source="consensus", price_age_seconds=60,
            policy=p, data_completeness=1.0)
        assert ev.status == "RESEARCH_CANDIDATE"
        assert ev.expected_value > 0

    def test_low_completeness_forces_data_incomplete(self, fsession: Session) -> None:
        p = _policy(fsession)
        ev = evaluate_candidate(
            market="SPREAD", selection="HOME", line=-2.5, american=-110,
            model_probability=0.90, price_source="consensus", price_age_seconds=60,
            policy=p, data_completeness=0.10)
        assert ev.status == "DATA_INCOMPLETE"

    def test_stale_price_forces_data_incomplete(self, fsession: Session) -> None:
        p = _policy(fsession)
        ev = evaluate_candidate(
            market="SPREAD", selection="HOME", line=-2.5, american=-110,
            model_probability=0.90, price_source="consensus",
            price_age_seconds=99_999, policy=p, data_completeness=1.0)
        assert ev.status == "DATA_INCOMPLETE"

    def test_passes_are_retained_in_ledger(self, fsession: Session) -> None:
        p = _policy(fsession)
        ev = evaluate_candidate(
            market="SPREAD", selection="HOME", line=-2.5, american=-110,
            model_probability=0.40, price_source="consensus", price_age_seconds=60,
            policy=p, data_completeness=1.0)
        record_evaluation(
            fsession, prediction=None, canonical_game_id="2026_02_KC_BUF", evaluation=ev,
            policy=p, horizon="PREGAME", as_of_at=KICK - timedelta(hours=2),
            data_completeness=1.0)
        rows = fsession.scalars(select(ForwardLedgerEntry)).all()
        assert len(rows) == 1 and rows[0].status == "PASS"

    def test_candidate_discloses_required_fields(self, fsession: Session) -> None:
        p = _policy(fsession)
        ev = evaluate_candidate(
            market="SPREAD", selection="HOME", line=-2.5, american=-110,
            model_probability=0.70, price_source="consensus", price_age_seconds=60,
            policy=p, data_completeness=1.0)
        d = ev.as_dict()
        for f in ("price_source", "price_age_seconds", "break_even_probability",
                  "model_probability", "conservative_probability", "fair_american",
                  "target_american", "invalidation_american", "expected_value", "reasons"):
            assert d[f] is not None, f

    def test_forward_ledger_is_separate_from_backtests(self, fsession: Session) -> None:
        from fde_api.db.models import BacktestRecommendation

        p = _policy(fsession)
        ev = evaluate_candidate(
            market="SPREAD", selection="HOME", line=-2.5, american=-110,
            model_probability=0.70, price_source="consensus", price_age_seconds=60,
            policy=p, data_completeness=1.0)
        record_evaluation(
            fsession, prediction=None, canonical_game_id="2026_02_KC_BUF", evaluation=ev,
            policy=p, horizon="PREGAME", as_of_at=KICK, data_completeness=1.0)
        assert fsession.scalars(select(BacktestRecommendation)).all() == []
        assert len(fsession.scalars(select(ForwardLedgerEntry)).all()) == 1


class TestModeSeparation:
    def test_records_carry_mode_and_do_not_mix(self, fsession: Session) -> None:
        ingest_schedule(fsession, _sched_csv(_sched_row()), season=2026,
                        data_mode=DataMode.LIVE_RESEARCH)
        ingest_schedule(fsession, _sched_csv(_sched_row(game_id="2026_02_DEMO_X")), season=2026,
                        data_mode=DataMode.DEMO)
        live = fsession.scalars(
            select(ScheduleObservation).where(
                ScheduleObservation.data_mode == DataMode.LIVE_RESEARCH.value)).all()
        demo = fsession.scalars(
            select(ScheduleObservation).where(
                ScheduleObservation.data_mode == DataMode.DEMO.value)).all()
        assert live and demo
        assert_single_mode([o.data_mode for o in live])
        assert_single_mode([o.data_mode for o in demo])
        with pytest.raises(ModeMixingError):
            assert_single_mode([o.data_mode for o in (*live, *demo)])

    def test_consensus_query_is_mode_scoped(self, fsession: Session) -> None:
        gid = _seed_game(fsession)
        obs = KICK - timedelta(hours=2)
        capture_odds(fsession, _event(obs.isoformat(), [
            ("draftkings", -2.5, -110, -110), ("fanduel", -2.5, -110, -110),
            ("betmgm", -2.5, -110, -110)]), observed_at=obs, data_mode=DataMode.LIVE_RESEARCH)
        build_consensus(fsession, canonical_game_id=gid, market="SPREAD",
                        as_of_at=obs + timedelta(minutes=1), kickoff_utc=KICK,
                        data_mode=DataMode.LIVE_RESEARCH)
        assert latest_consensus_at(fsession, canonical_game_id=gid, market="SPREAD",
                                   as_of_at=KICK, data_mode=DataMode.DEMO) is None
        assert latest_consensus_at(fsession, canonical_game_id=gid, market="SPREAD",
                                   as_of_at=KICK, data_mode=DataMode.LIVE_RESEARCH) is not None
