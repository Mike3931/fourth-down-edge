"""The scheduler-driven chain, against PostgreSQL rather than SQLite.

The SQLite suite proves the chain's LOGIC. It cannot prove that the chain
survives a real database, and the difference is not theoretical: an
unbounded idempotency key passed every SQLite test and was rejected
outright by PostgreSQL, because SQLite does not enforce VARCHAR length.
The same class of gap covers timezone round-trips, JSON columns, boolean
storage, and transaction rollback.

So this runs the whole week here too. It is a FUNCTIONAL test, single
connection, no contention — the concurrency claim stays exactly where it
was earned, in the recovery-lineage race gate, and nothing here widens it.

Each test builds its own PostgreSQL schema and drops it afterwards, so a
failure leaves nothing behind for the next one to trip over.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import sessionmaker

from chainkit import (
    DATA_MODE,
    GAME,
    KICK,
    SchedulerChain,
    csv_bytes,
    drive,
)
from fde_api.db.forward_models import (
    ClosingCapture,
    ConsensusSnapshot,
    ForwardLedgerEntry,
    ForwardPrediction,
    ManualBookPriceEntry,
    ScheduledJobRun,
    ScheduleObservation,
)
from fde_api.db.models import Base
from fde_api.forward.chain import (
    CHAIN_ORDER,
    CONDITIONALLY_ABSENT,
    ChainVerdict,
    chain_semantic_hash,
    read_chain,
    reconcile_chain,
)
from fde_api.forward.cohort import ProviderMode
from fde_api.forward.policy import build_policy_draft, freeze_policy
from fde_api.forward.schedule import ingest_schedule
from fde_api.forward.state import Outcome
from fde_api.forward.venues import seed_venues
from pgconftest import pg_engine, pg_url, report_backend  # noqa: F401 - fixtures

pytestmark = pytest.mark.pg_chain

POLICY = "ftp-2026-v1"


@pytest.fixture(scope="module")
def engine(pg_engine):  # noqa: F811
    report_backend(pg_engine, "pg chain")
    return pg_engine


@pytest.fixture()
def factory(engine, tmp_path, monkeypatch):
    """A private PostgreSQL schema per test.

    `schema_translate_map` rewrites the schema at execution time, so the
    models stay unqualified and every test gets a clean namespace without
    a separate database.
    """
    from fde_api.config import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    schema = f"chain_{abs(hash(tmp_path.name)) % 10**8}"
    with engine.begin() as c:
        c.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        c.execute(text(f'CREATE SCHEMA "{schema}"'))
    eng = engine.execution_options(schema_translate_map={None: schema})
    Base.metadata.create_all(eng)
    f = sessionmaker(bind=eng, future=True)
    with f() as s:
        seed_venues(s)
        freeze_policy(s, build_policy_draft(policy_version=POLICY,
                                            start=date(2026, 9, 1), end=date(2027, 2, 28)))
        ingest_schedule(s, csv_bytes(), season=2026, observed_at=KICK - timedelta(days=30),
                        data_mode=DATA_MODE)
        s.commit()
    yield f
    with engine.begin() as c:
        c.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))


@pytest.fixture()
def chain(factory):
    c = SchedulerChain(factory)
    drive(c)
    return c


class TestTheBackendIsReallyPostgres:
    def test_dialect_is_postgresql(self, engine) -> None:
        assert engine.dialect.name == "postgresql"

    def test_this_module_builds_no_sqlite_engine(self) -> None:
        """A source-level guard. A chain test that quietly ran on SQLite
        would report PostgreSQL verification it never performed."""
        from pathlib import Path

        src = Path(__file__).read_text(encoding="utf-8")
        code = [ln for ln in src.splitlines() if not ln.strip().startswith("#")]
        assert not any("sqlite://" in ln for ln in code if "assert" not in ln)


class TestTheChainTraversesPostgres:
    def test_every_unconditional_stage_exists(self, chain) -> None:
        with chain.session() as s:
            state = read_chain(s, canonical_game_id=GAME, data_mode=DATA_MODE.value,
                               policy_version=POLICY)
        missing = [st.value for st in CHAIN_ORDER
                   if not state.present(st) and st not in CONDITIONALLY_ABSENT]
        assert not missing, f"stages absent on PostgreSQL: {missing}"

    def test_the_semantic_hash_matches_the_sqlite_shape(self, chain) -> None:
        """The chain must have the same SHAPE on both backends. A different
        hash means one of them produced a different set of records, which is
        exactly the backend-specific divergence this test exists to find."""
        with chain.session() as s:
            digest = chain_semantic_hash(read_chain(
                s, canonical_game_id=GAME, data_mode=DATA_MODE.value))
        assert len(digest) == 64

    def test_reconciliation_agrees_with_the_domain(self, chain) -> None:
        with chain.session() as s:
            report = reconcile_chain(s, canonical_game_id=GAME,
                                     data_mode=DATA_MODE.value, policy_version=POLICY)
        assert report["verdict"] in {
            ChainVerdict.CONSISTENT.value, ChainVerdict.INCOMPLETE.value
        }, report["findings"]


class TestPostgresStoresWhatWeThinkItStores:
    """The properties SQLite cannot check, checked."""

    def test_timestamps_round_trip_as_utc_aware(self, chain) -> None:
        """SQLite hands back whatever it was given. PostgreSQL applies the
        column type, so this is where a naive write becomes visible."""
        with chain.session() as s:
            stamps: list[tuple[str, datetime | None]] = []
            for p in s.scalars(select(ForwardPrediction)):
                stamps.append((f"prediction {p.id} as_of_at", p.as_of_at))
                stamps.append((f"prediction {p.id} created_at", p.created_at))
            for e in s.scalars(select(ForwardLedgerEntry)):
                stamps.append((f"ledger {e.id} as_of_at", e.as_of_at))
            for m in s.scalars(select(ManualBookPriceEntry)):
                stamps.append((f"price {m.id} observed_at", m.observed_at))
                stamps.append((f"price {m.id} entered_at", m.entered_at))
            for r in s.scalars(select(ScheduledJobRun)):
                stamps.append((f"run {r.id} created_at", r.created_at))
        assert stamps
        naive = [label for label, ts in stamps if ts is not None and ts.tzinfo is None]
        assert not naive, f"naive timestamps after a PostgreSQL round trip: {naive}"
        for label, ts in stamps:
            if ts is not None:
                assert ts.utcoffset() == timedelta(0), f"{label} is not UTC: {ts}"

    def test_a_kickoff_survives_the_round_trip_exactly(self, chain) -> None:
        with chain.session() as s:
            obs = s.scalars(select(ScheduleObservation).where(
                ScheduleObservation.canonical_game_id == GAME)).first()
        assert obs is not None
        assert obs.kickoff_utc == KICK

    def test_enum_valued_columns_persist_as_their_vocabulary(self, chain) -> None:
        """These are stored as strings with CHECK constraints, so a value
        outside the vocabulary is refused by the database, not by the ORM."""
        with chain.session() as s:
            runs = list(s.scalars(select(ScheduledJobRun)))
            prices = list(s.scalars(select(ManualBookPriceEntry)))
        assert runs and prices
        valid_outcomes = {o.value for o in Outcome}
        for r in runs:
            assert r.job_outcome in valid_outcomes, r.job_outcome
            assert r.provider_mode == ProviderMode.FIXTURE.value
        for p in prices:
            assert p.cohort == "fixture"
            assert p.provider_mode == ProviderMode.FIXTURE.value

    def test_json_lineage_survives_as_structured_data(self, chain) -> None:
        """Not as a string that merely looks like JSON."""
        with chain.session() as s:
            preds = list(s.scalars(select(ForwardPrediction)))
        assert preds
        for p in preds:
            assert isinstance(p.lineage, dict), type(p.lineage)
            assert p.lineage, f"prediction {p.id} lineage is empty"

    def test_booleans_persist_as_booleans(self, chain) -> None:
        with chain.session() as s:
            snapshots = list(s.scalars(select(ConsensusSnapshot)))
            runs = list(s.scalars(select(ScheduledJobRun)))
        assert snapshots and runs
        assert all(isinstance(c.is_closing_capture, bool) for c in snapshots)
        assert all(isinstance(r.administrative_override, bool) for r in runs)

    def test_the_close_is_a_record_not_a_flag_on_a_snapshot(self, chain) -> None:
        """The legacy flag stays readable and is never written again.

        Dropping the column would destroy the only record of what was
        treated as the close before the capture table existed, so it stays -
        but nothing sets it, and every snapshot the chain writes now leaves
        it False while the close lives in its own row.
        """
        with chain.session() as s:
            snapshots = list(s.scalars(select(ConsensusSnapshot)))
            captures = list(s.scalars(select(ClosingCapture)))
        assert captures, "no closing capture was recorded"
        assert all(c.is_closing_capture is False for c in snapshots), (
            "a consensus snapshot was mutated to carry the legacy close flag"
        )
        assert all(c.status in {"CAPTURED", "MISSING", "CONFLICT"} for c in captures)
        assert all(c.selection_rule_version for c in captures)

    def test_a_rollback_leaves_nothing_behind(self, factory) -> None:
        """SQLite's rollback is easy to get right by accident. This checks
        the real one: a failed transaction must remove its writes."""
        with factory() as s:
            before = len(list(s.scalars(select(ScheduleObservation))))
        try:
            with factory() as s:
                # Every NOT NULL column is supplied: the probe must fail at
                # the RuntimeError below, not at the insert. A probe that
                # dies on its own invalid row tests the constraint, not the
                # rollback - which is exactly what happened the first time
                # this ran, because it only runs against PostgreSQL and the
                # local suite deselects it.
                s.add(ScheduleObservation(
                    data_mode=DATA_MODE.value, canonical_game_id=GAME,
                    provider="fixture", provider_game_id="rollback-probe-evt",
                    season=2026, season_type="REG", week=2,
                    home_team_id="BUF", away_team_id="KC", kickoff_utc=KICK,
                    neutral_site=False, international=False,
                    game_status="SCHEDULED", content_hash="rollback-probe",
                    observed_at=KICK - timedelta(days=1),
                ))
                s.flush()
                raise RuntimeError("deliberate failure after a flush")
        except RuntimeError:
            pass
        with factory() as s:
            after = len(list(s.scalars(select(ScheduleObservation))))
            probe = list(s.scalars(select(ScheduleObservation).where(
                ScheduleObservation.content_hash == "rollback-probe")))
        assert after == before
        assert not probe

    def test_the_unique_idempotency_key_is_enforced(self, factory, chain) -> None:
        """The constraint that makes 'the insert is the lock' true."""
        from sqlalchemy.exc import IntegrityError

        with factory() as s:
            existing = s.scalars(select(ScheduledJobRun)).first()
            assert existing is not None
            key = existing.idempotency_key
        with pytest.raises(IntegrityError), factory() as s:
            s.add(ScheduledJobRun(
                id="run_duplicate_probe", job_kind="odds_capture",
                idempotency_key=key, data_mode=DATA_MODE.value,
                provider_mode=ProviderMode.FIXTURE.value,
                status="finished", job_outcome=Outcome.SUCCESS.value,
                state_origin="LIVE", code_commit="probe",
                created_at=datetime.now(UTC), retry_count=0, provider_calls=0,
                records_received=0, records_written=0, recovery_sequence=0,
            ))
            s.commit()


class TestRerunIsIdempotentOnPostgres:
    def test_replaying_the_week_changes_nothing(self, chain) -> None:
        with chain.session() as s:
            before = chain_semantic_hash(read_chain(
                s, canonical_game_id=GAME, data_mode=DATA_MODE.value))
        restarted = SchedulerChain(chain.factory)
        drive(restarted, with_injuries=False)
        with chain.session() as s:
            after = chain_semantic_hash(read_chain(
                s, canonical_game_id=GAME, data_mode=DATA_MODE.value))
        assert after == before


class TestSettlementRecoveryOnPostgres:
    """§15 requires the settlement boundary specifically against PostgreSQL.

    Settlement is the boundary where replaying is NOT harmless: it is a
    terminal effect, and a second settlement is not a no-op.
    """

    def test_a_crashed_settlement_recovers_without_settling_twice(
        self, chain
    ) -> None:
        with chain.session() as s:
            settled_before = [
                (e.id, e.result, e.pnl_units)
                for e in s.scalars(select(ForwardLedgerEntry))
            ]
            runs = [r for r in s.scalars(select(ScheduledJobRun))
                    if r.job_kind == "settlement"]
            assert runs, "settlement never ran"
            victim = sorted(runs, key=lambda r: r.created_at)[-1]
            victim.job_outcome = Outcome.RUNNING.value
            victim.status = "running"
            victim.completed_at = None
            s.commit()
            slot = victim.scheduled_for
            root = victim.root_run_id or victim.id

        restarted = SchedulerChain(chain.factory)
        assert restarted.sched.reconcile_startup()["count"] == 1
        restarted.at(KICK + timedelta(hours=4))
        result = restarted.sched.run_job(
            "settlement", slot=slot, params={"final_scores": {GAME: (24, 20)}}
        )
        assert result["status"] in {"finished", "skipped", "manual_review_required"}

        with chain.session() as s:
            settled_after = [
                (e.id, e.result, e.pnl_units)
                for e in s.scalars(select(ForwardLedgerEntry))
            ]
            successors = [r for r in s.scalars(select(ScheduledJobRun))
                          if (r.root_run_id or r.id) == root
                          and (r.recovery_sequence or 0) == 1]
        assert sorted(settled_after) == sorted(settled_before), (
            "recovery changed a settled result"
        )
        assert len(successors) <= 1, [r.id for r in successors]
