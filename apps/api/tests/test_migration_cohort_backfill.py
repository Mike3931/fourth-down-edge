"""The cohort backfill, against rows that already exist.

A fresh-database migration proves the DDL parses. It proves nothing about
what happens to the records already in the table, which is the only part
that can lose information.

These tests build a database at the revision before `d8f41c6a3b92`,
insert consensus, ledger and availability rows the way the old code
wrote them — with a `data_mode` and no cohort at all — then upgrade and
assert what the rows say afterwards.

The assertion that matters is the one about `unknown_legacy`. The
convenient backfill is `burn_in`, because that is what a LIVE_RESEARCH
row probably was and it would let the rows be re-hashed and reused. It is
also wrong: nothing in this database records the cohort those rows were
written into — `scheduled_job_runs` carries `data_mode` and provider
mode but not cohort — so `burn_in` would be an invention, and it is
precisely the value that would collide with the burn-in cohort about to
start.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

API_ROOT = Path(__file__).resolve().parents[1]

PRIOR_REVISION = "b7e2f9c41a68"
COHORT_REVISION = "d8f41c6a3b92"
LEGACY = "unknown_legacy"

T0 = datetime(2026, 9, 13, 12, 0, tzinfo=UTC)
GAME = "2026_01_KC_BUF"


def _alembic(db_url: str, *args: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "FDE_DATABASE_URL": db_url}
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=API_ROOT, env=env, capture_output=True, text=True, timeout=300, check=False,
    )


@pytest.fixture()
def prior_db(tmp_path) -> str:
    """A database at `b7e2f9c41a68`, holding rows written without a cohort."""
    db = tmp_path / "prior.db"
    url = f"sqlite:///{db.as_posix()}"
    r = _alembic(url, "upgrade", PRIOR_REVISION)
    assert r.returncode == 0, r.stderr

    engine = create_engine(url, future=True)
    with engine.begin() as conn:
        for i, (mode, market) in enumerate(
            (("LIVE_RESEARCH", "SPREAD"), ("LIVE_RESEARCH", "TOTAL"), ("DEMO", "SPREAD"))
        ):
            conn.execute(text(
                "INSERT INTO consensus_snapshots "
                "(data_mode, canonical_game_id, market, method_version, provider_mode, "
                " median_line, eligible_books, quote_ids, is_closing_capture, observed_at) "
                "VALUES (:dm, :g, :m, 'consensus-v2', 'LIVE', -2.5, 3, :q, 0, :at)"
            ), {"dm": mode, "g": GAME, "m": market,
                "q": json.dumps({"quote_ids": [i], "books": ["draftkings"]}), "at": T0})

        conn.execute(text(
            "INSERT INTO forward_ledger "
            "(data_mode, canonical_game_id, policy_version, model_version, horizon, "
            " market, status, reasons, as_of_at, created_at) "
            "VALUES ('LIVE_RESEARCH', :g, 'ftp-2026-v1', 'm1', 'T-24h', 'SPREAD', "
            " 'PASS', :r, :at, :at)"
        ), {"g": GAME, "r": json.dumps({"codes": []}), "at": T0})

        conn.execute(text(
            "INSERT INTO availability_assessments "
            "(data_mode, canonical_game_id, team_id, player_id, state, active_prob_low, "
            " active_prob_high, confidence_tier, reason, missing_data, is_starting_qb, "
            " as_of_at, created_at) "
            "VALUES ('LIVE_RESEARCH', :g, 'KC', 'p1', 'UNKNOWN', 0.0, 1.0, 'NONE', "
            " 'no observation', 1, 0, :at, :at)"
        ), {"g": GAME, "at": T0})
    engine.dispose()
    return url


def _rows(url: str, sql: str) -> list[tuple]:
    engine = create_engine(url, future=True)
    try:
        with engine.connect() as conn:
            return [tuple(r) for r in conn.execute(text(sql))]
    finally:
        engine.dispose()


class TestTheUpgradeSucceedsOnPopulatedTables:
    def test_upgrade_succeeds(self, prior_db: str) -> None:
        r = _alembic(prior_db, "upgrade", "head")
        assert r.returncode == 0, r.stderr

    def test_no_row_is_lost(self, prior_db: str) -> None:
        assert _alembic(prior_db, "upgrade", "head").returncode == 0
        counts = dict(_rows(prior_db, """
            SELECT 'consensus', COUNT(*) FROM consensus_snapshots
            UNION ALL SELECT 'ledger', COUNT(*) FROM forward_ledger
            UNION ALL SELECT 'availability', COUNT(*) FROM availability_assessments
        """))
        assert counts == {"consensus": 3, "ledger": 1, "availability": 1}


class TestWhatTheBackfilledRowsSay:
    def test_every_existing_row_is_marked_legacy(self, prior_db: str) -> None:
        assert _alembic(prior_db, "upgrade", "head").returncode == 0
        for table in ("consensus_snapshots", "forward_ledger",
                      "availability_assessments"):
            values = {c for (c,) in _rows(prior_db, f"SELECT cohort FROM {table}")}
            assert values == {LEGACY}, (table, values)

    def test_a_live_research_row_is_not_called_burn_in(self, prior_db: str) -> None:
        """The convenient answer, refused on purpose.

        Nothing in this schema records which cohort wrote these rows;
        `scheduled_job_runs` carries data mode and provider mode and not
        cohort. Backfilling `burn_in` would manufacture that provenance,
        and would put legacy rows in the same identity space as the
        burn-in cohort that is about to start writing.
        """
        assert _alembic(prior_db, "upgrade", "head").returncode == 0
        live = _rows(prior_db, "SELECT cohort FROM consensus_snapshots "
                               "WHERE data_mode = 'LIVE_RESEARCH'")
        assert live and all(c == LEGACY for (c,) in live)
        assert not any(c == "burn_in" for (c,) in live)

    def test_a_demo_row_is_not_called_demo_either(self, prior_db: str) -> None:
        """DEMO -> `demo` looks safe and is the same mistake in a smaller
        coat: the DEMO mode is a data mode, and the `demo` cohort is an
        experiment. The rows may well have been the demo cohort. Nothing
        records that they were."""
        assert _alembic(prior_db, "upgrade", "head").returncode == 0
        demo = _rows(prior_db, "SELECT cohort FROM consensus_snapshots "
                               "WHERE data_mode = 'DEMO'")
        assert demo and all(c == LEGACY for (c,) in demo)

    def test_the_data_mode_is_untouched(self, prior_db: str) -> None:
        """A cohort column is an addition, not a reinterpretation."""
        assert _alembic(prior_db, "upgrade", "head").returncode == 0
        modes = sorted(m for (m,) in _rows(
            prior_db, "SELECT data_mode FROM consensus_snapshots"))
        assert modes == ["DEMO", "LIVE_RESEARCH", "LIVE_RESEARCH"]

    def test_the_book_minimum_is_backfilled_to_what_the_old_code_required(
        self, prior_db: str
    ) -> None:
        """Not a default: `build_consensus` required three books for every
        row that can be in this table, because the per-cohort minimum did
        not exist when they were written."""
        assert _alembic(prior_db, "upgrade", "head").returncode == 0
        mins = {m for (m,) in _rows(
            prior_db, "SELECT min_books_applied FROM consensus_snapshots")}
        assert mins == {3}


class TestTheColumnRefusesWhatTheEnumForbids:
    def test_an_invented_cohort_is_rejected(self, prior_db: str) -> None:
        assert _alembic(prior_db, "upgrade", "head").returncode == 0
        engine = create_engine(prior_db, future=True)
        try:
            with engine.begin() as conn, pytest.raises(IntegrityError) as e:
                conn.execute(text(
                    "UPDATE consensus_snapshots SET cohort = 'production'"))
        finally:
            engine.dispose()
        # Named, so the test cannot pass because the UPDATE failed for
        # some unrelated reason.
        assert "ck_consensus_snapshots_cohort_vocabulary" in str(e.value)

    def test_a_real_cohort_is_accepted(self, prior_db: str) -> None:
        assert _alembic(prior_db, "upgrade", "head").returncode == 0
        engine = create_engine(prior_db, future=True)
        try:
            with engine.begin() as conn:
                conn.execute(text(
                    "UPDATE consensus_snapshots SET cohort = 'official_forward_test'"))
        finally:
            engine.dispose()

    def test_a_row_may_not_be_written_without_a_cohort(self, prior_db: str) -> None:
        """No server default, deliberately. A write that forgets the
        cohort must fail rather than be filed as legacy."""
        assert _alembic(prior_db, "upgrade", "head").returncode == 0
        engine = create_engine(prior_db, future=True)
        try:
            with engine.begin() as conn, pytest.raises(IntegrityError) as e:
                conn.execute(text(
                    "INSERT INTO forward_ledger (data_mode, canonical_game_id, "
                    " policy_version, model_version, horizon, market, status, "
                    " reasons, as_of_at, created_at) "
                    "VALUES ('LIVE_RESEARCH', :g, 'p', 'm', 'T-24h', 'SPREAD', "
                    " 'PASS', '{}', :at, :at)"
                ), {"g": GAME, "at": T0})
        finally:
            engine.dispose()
        assert "NOT NULL" in str(e.value) and "cohort" in str(e.value)


class TestItGoesBack:
    def test_downgrade_then_upgrade_restores_the_backfill(self, prior_db: str) -> None:
        assert _alembic(prior_db, "upgrade", "head").returncode == 0
        assert _alembic(prior_db, "downgrade", PRIOR_REVISION).returncode == 0
        cols = {c for (_, c, *_r) in _rows(
            prior_db, "PRAGMA table_info(consensus_snapshots)")}
        assert "cohort" not in cols and "min_books_applied" not in cols
        assert _rows(prior_db, "SELECT COUNT(*) FROM consensus_snapshots") == [(3,)]

        r = _alembic(prior_db, "upgrade", COHORT_REVISION)
        assert r.returncode == 0, r.stderr
        assert {c for (c,) in _rows(
            prior_db, "SELECT cohort FROM consensus_snapshots")} == {LEGACY}
