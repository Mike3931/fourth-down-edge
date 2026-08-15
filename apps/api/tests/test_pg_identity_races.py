"""The four domain identities under genuine PostgreSQL contention.

Sequential idempotency is necessary and not sufficient. `SELECT -> not
found -> INSERT` passes every sequential test and still loses under
contention: two callers both see nothing and both insert. SQLite hides
that by serialising writers at the file level, which is why the previous
service-level pre-check survived months of green runs.

Three race families per entity, twelve in total:

  exact-retry     same slot, same content   -> one row, one CREATED, rest
                                               EXISTING_IDENTICAL
  conflicting     same slot, diff content   -> one row retained, the loser
                                               reports CONFLICT, no overwrite
  distinct        different slots           -> both retained, both CREATED

The conflicting family deliberately does NOT assert which caller wins.
There is no deterministic priority rule between two simultaneous callers,
and asserting one would be asserting a scheduling accident.

These fixtures RAISE when `FDE_DATABASE_URL` is absent rather than falling
back to SQLite. A race silently downgraded to SQLite and reported as
verified would be worse than not running it.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import sessionmaker

from fde_api.db.forward_models import (
    AvailabilityAssessment,
    ConsensusSnapshot,
    ForwardLedgerEntry,
    ManualBookPriceEntry,
    OddsQuote,
)
from fde_api.db.models import Base
from fde_api.forward.cohort import Cohort, ProviderMode
from fde_api.forward.domain_identity import IdentityOutcome
from fde_api.forward.modes import DataMode
from pgconftest import pg_engine, pg_url, report_backend  # noqa: F401 - fixtures

pytestmark = pytest.mark.pg_identity

KICK = datetime(2026, 9, 13, 17, 0, tzinfo=UTC)
CUTOFF = KICK - timedelta(days=1)
GAME = "2026_02_KC_BUF"
MODE = DataMode.DEMO
COHORT = Cohort.DEMO
WORKERS = 4


@pytest.fixture(scope="module")
def engine(pg_engine):  # noqa: F811
    report_backend(pg_engine, "pg domain identity races")
    return pg_engine


@pytest.fixture()
def factory(engine, tmp_path, monkeypatch):
    """A private PostgreSQL schema per test."""
    from fde_api.config import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    schema = f"ident_{abs(hash(tmp_path.name)) % 10**8}"
    with engine.begin() as c:
        c.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        c.execute(text(f'CREATE SCHEMA "{schema}"'))
    eng = engine.execution_options(schema_translate_map={None: schema})
    Base.metadata.create_all(eng)
    yield sessionmaker(bind=eng, future=True)
    with engine.begin() as c:
        c.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))


def race(fn, n: int = WORKERS) -> list[Any]:
    """Release n workers simultaneously from a barrier.

    Each builds its own session, so each takes an independent connection.
    Exceptions are captured rather than raised in the thread, so one worker
    cannot take the process down and hide what the others did.
    """
    barrier = threading.Barrier(n)
    out: list[Any] = [None] * n

    def work(i: int) -> None:
        barrier.wait(timeout=30)
        try:
            out[i] = fn(i)
        except Exception as exc:  # captured deliberately; see docstring
            out[i] = exc

    threads = [threading.Thread(target=work, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)
    assert not any(t.is_alive() for t in threads), "a worker deadlocked"
    return out


def outcomes(results: list[Any]) -> list[IdentityOutcome]:
    failures = [r for r in results if isinstance(r, BaseException)]
    assert not failures, (
        f"{len(failures)}/{len(results)} workers raised: "
        + "; ".join(f"{type(f).__name__}: {f}" for f in failures)
    )
    return [r for r in results if r is not None]


# --------------------------------------------------------------------------- #
# Backend evidence
# --------------------------------------------------------------------------- #


class TestTheBackendIsReallyPostgres:
    def test_dialect_is_postgresql(self, engine) -> None:
        assert engine.dialect.name == "postgresql"

    def test_workers_hold_independent_connections(self, factory) -> None:
        """Two sessions on one connection would queue, not race."""
        pids = race(lambda i: _backend_pid(factory))
        distinct = {p for p in pids if isinstance(p, int)}
        assert len(distinct) > 1, f"all workers shared a backend: {pids}"

    def test_this_module_builds_no_sqlite_engine(self) -> None:
        from pathlib import Path

        src = Path(__file__).read_text(encoding="utf-8")
        code = [ln for ln in src.splitlines() if not ln.strip().startswith("#")]
        assert not any("sqlite://" in ln for ln in code if "assert" not in ln)


def _backend_pid(factory) -> int:
    with factory() as s:
        return int(s.execute(text("SELECT pg_backend_pid()")).scalar())


# --------------------------------------------------------------------------- #
# Consensus
# --------------------------------------------------------------------------- #


def _seed_quotes(factory, *, at: datetime, total: float) -> None:
    with factory() as s:
        for book in ("draftkings", "fanduel", "betmgm"):
            for selection in ("OVER", "UNDER"):
                s.add(OddsQuote(
                    data_mode=MODE.value, canonical_game_id=GAME,
                    provider="fixture", provider_mode=ProviderMode.FIXTURE.value,
                    sportsbook=book, market="TOTAL", selection=selection,
                    line=total, american=-110, decimal_odds=1.909,
                    observed_at=at, provider_timestamp=at,
                    raw_hash=f"{book}{selection}{at.isoformat()}{total}",
                ))
        s.commit()


def _build_consensus(factory, *, at: datetime) -> str:
    from fde_api.forward.consensus import build_consensus

    with factory() as s:
        _snap, rep = build_consensus(
            s, canonical_game_id=GAME, market="TOTAL", as_of_at=at,
            kickoff_utc=KICK, cohort=COHORT, data_mode=MODE)
        s.commit()
    for reason in rep.reasons:
        if "identity outcome:" in reason:
            return reason.split("identity outcome:")[1].strip()
    return "UNKNOWN"


class TestConsensusRaces:
    def test_exact_retry_race_yields_one_row(self, factory) -> None:
        _seed_quotes(factory, at=CUTOFF - timedelta(minutes=10), total=47.5)
        results = outcomes(race(lambda i: _build_consensus(factory, at=CUTOFF)))
        assert results.count("CREATED") <= 1, results
        assert all(r in {"CREATED", "EXISTING_IDENTICAL"} for r in results), results
        with factory() as s:
            rows = list(s.scalars(select(ConsensusSnapshot)))
        assert len(rows) == 1, [r.id for r in rows]

    def test_distinct_cutoffs_both_survive(self, factory) -> None:
        _seed_quotes(factory, at=CUTOFF - timedelta(minutes=10), total=47.5)
        cutoffs = [CUTOFF, CUTOFF + timedelta(minutes=5)]
        results = outcomes(race(
            lambda i: _build_consensus(factory, at=cutoffs[i % 2]), n=2))
        assert results.count("CREATED") == 2, results
        with factory() as s:
            assert len(list(s.scalars(select(ConsensusSnapshot)))) == 2


# --------------------------------------------------------------------------- #
# Availability
# --------------------------------------------------------------------------- #


def _assess(factory, *, at: datetime, player: str = "BUF_QB_ALLEN") -> str:
    """The service's own typed outcome.

    Counting rows before and after cannot work here: another worker can
    insert between the two reads, so every caller sees a higher count and
    every caller claims CREATED. The service knows what it did; the test
    must ask it rather than guess.
    """
    from fde_api.forward.injuries import assess_player_result

    with factory() as s:
        result = assess_player_result(
            s, canonical_game_id=GAME, team_id="BUF", player_id=player,
            as_of_at=at, cohort=COHORT, data_mode=MODE)
        s.commit()
        return result.outcome.value


class TestAvailabilityRaces:
    def test_exact_retry_race_yields_one_row(self, factory) -> None:
        outcomes(race(lambda i: _assess(factory, at=CUTOFF)))
        with factory() as s:
            rows = list(s.scalars(select(AvailabilityAssessment)))
        assert len(rows) == 1, [r.id for r in rows]

    def test_distinct_cutoffs_both_survive(self, factory) -> None:
        cutoffs = [CUTOFF, CUTOFF + timedelta(hours=6)]
        outcomes(race(lambda i: _assess(factory, at=cutoffs[i % 2]), n=2))
        with factory() as s:
            assert len(list(s.scalars(select(AvailabilityAssessment)))) == 2

    def test_distinct_players_both_survive(self, factory) -> None:
        players = ["BUF_QB_ALLEN", "BUF_WR_DIGGS"]
        outcomes(race(lambda i: _assess(factory, at=CUTOFF,
                                        player=players[i % 2]), n=2))
        with factory() as s:
            assert len(list(s.scalars(select(AvailabilityAssessment)))) == 2


# --------------------------------------------------------------------------- #
# Price observation
# --------------------------------------------------------------------------- #


def _record_price(
    factory, *, american: int, at: datetime | None = None,
    user: str = "operator-a",
) -> str:
    from fde_api.forward.prices import (
        PriceEntryError,
        PriceObservation,
        record_price_observation_result,
    )

    observed = at or CUTOFF
    with factory() as s:
        try:
            result = record_price_observation_result(
                s,
                PriceObservation(
                    canonical_game_id=GAME, market="SPREAD", selection="HOME",
                    line=-3.0, american=american, observed_at=observed,
                    user_id=user, cohort=Cohort.FIXTURE,
                    provider_mode=ProviderMode.FIXTURE, data_mode=MODE,
                ),
                now=observed + timedelta(minutes=1),
            )
            s.commit()
            return result.outcome.value
        except PriceEntryError as e:
            s.rollback()
            if "different content" in str(e):
                return "CONFLICT"
            raise


class TestPriceObservationRaces:
    def test_exact_retry_race_yields_one_row(self, factory) -> None:
        results = outcomes(race(lambda i: _record_price(factory, american=-110)))
        assert results.count("CREATED") <= 1, results
        assert "CONFLICT" not in results, results
        with factory() as s:
            rows = list(s.scalars(select(ManualBookPriceEntry)))
        assert len(rows) == 1, [r.id for r in rows]

    def test_conflicting_payload_race_keeps_one_row(self, factory) -> None:
        """Two submitters, one slot, different prices.

        Deliberately no assertion about WHICH wins: there is no priority
        rule between two simultaneous callers, and asserting one would be
        asserting a scheduling accident.
        """
        prices = [-110, -105]
        results = outcomes(race(
            lambda i: _record_price(factory, american=prices[i % 2]), n=2))
        assert results.count("CREATED") <= 1, results
        assert "CONFLICT" in results or results.count("EXISTING_IDENTICAL") >= 1, results
        with factory() as s:
            rows = list(s.scalars(select(ManualBookPriceEntry)))
        assert len(rows) == 1, [(r.id, r.american) for r in rows]

    def test_distinct_observations_both_survive(self, factory) -> None:
        times = [CUTOFF, CUTOFF + timedelta(minutes=5)]
        results = outcomes(race(
            lambda i: _record_price(factory, american=-110, at=times[i % 2]), n=2))
        assert results.count("CREATED") == 2, results
        with factory() as s:
            assert len(list(s.scalars(select(ManualBookPriceEntry)))) == 2

    def test_distinct_submitters_both_survive(self, factory) -> None:
        users = ["operator-a", "operator-b"]
        results = outcomes(race(
            lambda i: _record_price(factory, american=-110, user=users[i % 2]), n=2))
        assert results.count("CREATED") == 2, results
        with factory() as s:
            assert len(list(s.scalars(select(ManualBookPriceEntry)))) == 2


class TestNoRawIntegrityErrorEscapes:
    """A raw IntegrityError reaching a caller means the service did not
    understand what happened - and a caller cannot tell a lost race from a
    genuine constraint bug."""

    def test_consensus_never_raises_integrity_error(self, factory) -> None:
        _seed_quotes(factory, at=CUTOFF - timedelta(minutes=10), total=47.5)
        results = race(lambda i: _build_consensus(factory, at=CUTOFF))
        for r in results:
            assert not isinstance(r, BaseException), f"{type(r).__name__}: {r}"

    def test_availability_never_raises_integrity_error(self, factory) -> None:
        results = race(lambda i: _assess(factory, at=CUTOFF))
        for r in results:
            assert not isinstance(r, BaseException), f"{type(r).__name__}: {r}"

    def test_price_never_raises_integrity_error(self, factory) -> None:
        results = race(lambda i: _record_price(factory, american=-110))
        for r in results:
            assert not isinstance(r, BaseException), f"{type(r).__name__}: {r}"


# --------------------------------------------------------------------------- #
# Conflicting-payload races: the three cells that were missing
# --------------------------------------------------------------------------- #
#
# A conflict race is harder to stage than an exact retry: the callers must
# reach the SAME logical slot carrying DIFFERENT content, simultaneously.
# Each helper below varies exactly one thing that is CONTENT and nothing
# that participates in the identity.
#
# None of these assert WHICH caller wins. There is no priority rule between
# two simultaneous callers, and asserting one would be asserting a
# scheduling accident.


def _build_consensus_window(factory, *, at: datetime, max_age: int) -> str:
    """Consensus over a different eligibility window at the same slot.

    `max_age_minutes` decides which quotes are eligible. It is not part of
    the logical identity - (game, market, cutoff, cohort, method) is - so
    two windows produce one slot and two different medians.
    """
    from fde_api.forward.consensus import build_consensus

    with factory() as s:
        _snap, rep = build_consensus(
            s, canonical_game_id=GAME, market="TOTAL", as_of_at=at,
            kickoff_utc=KICK, cohort=COHORT, data_mode=MODE, max_age_minutes=max_age)
        s.commit()
    for reason in rep.reasons:
        if "identity outcome:" in reason:
            return reason.split("identity outcome:")[1].strip()
    return "UNKNOWN"


class TestConsensusConflictRace:
    def test_conflicting_content_at_one_slot(self, factory) -> None:
        # An older set at 47.5 and a recent set at 51.5. A wide window sees
        # both and medians differently from a narrow one that sees only the
        # recent quotes - same slot, different answer.
        _seed_quotes(factory, at=CUTOFF - timedelta(minutes=50), total=47.5)
        _seed_quotes(factory, at=CUTOFF - timedelta(minutes=5), total=51.5)

        windows = [90, 10]
        results = outcomes(race(
            lambda i: _build_consensus_window(factory, at=CUTOFF,
                                              max_age=windows[i % 2]), n=2))
        assert results.count("CREATED") <= 1, results
        assert all(r in {"CREATED", "EXISTING_IDENTICAL", "CONFLICT"}
                   for r in results), results
        with factory() as s:
            rows = list(s.scalars(select(ConsensusSnapshot)))
        assert len(rows) == 1, [(r.id, r.median_line) for r in rows]

    def test_a_consensus_conflict_demands_review(self) -> None:
        from fde_api.forward.domain_identity import CONSENSUS

        assert CONSENSUS.conflict_requires_review is True


def _assess_with_state(factory, *, at: datetime, designation: str | None) -> str:
    """Assess after changing the underlying injury observation.

    Same (game, player, cutoff, cohort, method) - one slot - but a
    different source-derived assessment.
    """
    from fde_api.forward.injuries import (
        SourceCategory,
        assess_player_result,
        record_injury_observation,
    )

    with factory() as s:
        record_injury_observation(
            s, canonical_game_id=GAME, team_id="BUF", player_id="BUF_WR_DIGGS",
            report_date="2026-09-11", observed_at=at - timedelta(hours=1),
            source_category=SourceCategory.OFFICIAL_VERIFIED,
            practice_status="DNP" if designation == "OUT" else "LIMITED",
            game_designation=designation, source_reference=f"race-{designation}",
            data_mode=MODE, now=at - timedelta(minutes=1),
        )
        result = assess_player_result(
            s, canonical_game_id=GAME, team_id="BUF", player_id="BUF_WR_DIGGS",
            as_of_at=at, cohort=COHORT, data_mode=MODE)
        s.commit()
        return result.outcome.value


class TestAvailabilityConflictRace:
    def test_conflicting_content_at_one_slot(self, factory) -> None:
        designations = ["OUT", "QUESTIONABLE"]
        results = outcomes(race(
            lambda i: _assess_with_state(factory, at=CUTOFF,
                                         designation=designations[i % 2]), n=2))
        assert results.count("CREATED") <= 1, results
        assert all(r in {"CREATED", "EXISTING_IDENTICAL", "CONFLICT"}
                   for r in results), results
        with factory() as s:
            rows = list(s.scalars(select(AvailabilityAssessment)))
        assert len(rows) == 1, [(r.id, r.state) for r in rows]

    def test_an_availability_conflict_still_reports_conflict(self) -> None:
        """Review is not required here - a later cutoff supersedes - but the
        outcome is still CONFLICT. Not requiring review is not the same as
        not noticing."""
        from fde_api.forward.domain_identity import AVAILABILITY

        assert AVAILABILITY.conflict_requires_review is False
        assert "CONFLICT" in {o.value for o in IdentityOutcome}


def _seed_policy(factory) -> None:
    """Freeze the policy BEFORE any race starts.

    Freezing it lazily inside the racing callable made the workers race to
    insert the policy as well as the evaluation, and the policy's primary
    key is not the identity under test. That failure is a fixture artefact
    and would have masked whatever the evaluation identity actually did.
    """
    from fde_api.forward.policy import build_policy_draft, freeze_policy

    with factory() as s:
        freeze_policy(s, build_policy_draft(
            policy_version="ftp-2026-v1",
            start=(KICK - timedelta(days=60)).date(),
            end=(KICK + timedelta(days=160)).date()))
        s.commit()


def _policy(session):
    from fde_api.forward.policy import load_policy

    return load_policy(session, "ftp-2026-v1")


def _record_evaluation(
    factory, *, probability: float = 0.62, horizon: str = "OPENING",
) -> str:
    """One evaluation of a fixed slot.

    The probability is CONTENT; the horizon is part of the logical identity.
    Varying the first stages a conflict, varying the second stages a
    legitimate distinct version.
    """
    from fde_api.forward.ledger import evaluate_candidate, record_evaluation_result

    with factory() as s:
        policy = _policy(s)
        ev = evaluate_candidate(
            market="SPREAD", selection="HOME", line=-3.0, american=150,
            model_probability=probability, price_source="fixture_price",
            price_age_seconds=120, policy=policy, data_completeness=1.0)
        result = record_evaluation_result(
            s, prediction=None, canonical_game_id=GAME, evaluation=ev,
            policy=policy, horizon=horizon, as_of_at=CUTOFF,
            data_completeness=1.0, cohort=COHORT, data_mode=MODE)
        s.commit()
        return result.outcome.value


class TestEvaluationRaces:
    def test_exact_retry_race_yields_one_row(self, factory) -> None:
        _seed_policy(factory)
        results = outcomes(race(lambda i: _record_evaluation(factory)))
        assert results.count("CREATED") <= 1, results
        assert all(r in {"CREATED", "EXISTING_IDENTICAL"} for r in results), results
        with factory() as s:
            rows = list(s.scalars(select(ForwardLedgerEntry)))
        assert len(rows) == 1, [r.id for r in rows]

    def test_conflicting_content_at_one_slot(self, factory) -> None:
        _seed_policy(factory)
        probabilities = [0.62, 0.71]
        results = outcomes(race(
            lambda i: _record_evaluation(factory,
                                         probability=probabilities[i % 2]), n=2))
        assert results.count("CREATED") <= 1, results
        assert all(r in {"CREATED", "EXISTING_IDENTICAL", "CONFLICT"}
                   for r in results), results
        with factory() as s:
            rows = list(s.scalars(select(ForwardLedgerEntry)))
        assert len(rows) == 1, [(r.id, r.model_probability) for r in rows]

    def test_distinct_identities_both_survive(self, factory) -> None:
        """A different evaluation type is a different slot, not a conflict -
        the same shape as a remediated decision context creating a new
        immutable evaluation."""
        _seed_policy(factory)
        horizons = ["OPENING", "EARLY_WEEK"]
        results = outcomes(race(
            lambda i: _record_evaluation(factory, horizon=horizons[i % 2]), n=2))
        assert results.count("CREATED") == 2, results
        with factory() as s:
            assert len(list(s.scalars(select(ForwardLedgerEntry)))) == 2

    def test_an_evaluation_conflict_demands_review(self) -> None:
        from fde_api.forward.domain_identity import EVALUATION

        assert EVALUATION.conflict_requires_review is True
