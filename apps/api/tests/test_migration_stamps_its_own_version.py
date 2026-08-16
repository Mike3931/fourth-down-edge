"""A migration must stamp the version whose rules it actually used.

`b7e2f9c41a68` backfills identity hashes for rows that already existed. It
computes them with the rules of its own moment — for CONSENSUS that means
`"cohort": m["data_mode"]`, read positionally from the row so the migration
does not depend on an ORM that will have moved on.

It then stamped `logical_identity_version` by importing the LIVE constant
from `fde_api.forward.domain_identity`. That was harmless while the live
constant still said v1 and became wrong the moment it said v2: rows would
carry a hash computed under v1 rules, labelled v2.

That failure is worse than a plain mismatch. `require_known_versions`
would ACCEPT the v2 label, recompute under v2 rules, get a different hash,
and report a CONFLICT — a contradiction where there was only a mislabelled
record. A wrong version does not fail loudly; it fails as a wrong answer.

It survived `test_migration_cohort_backfill.py` because those tests insert
rows AFTER upgrading to `b7e2f9c41a68`, so its backfill ran over an empty
table and stamped nothing.

The rule this pins: a migration describes the schema and the semantics at
a point in history. It may not import a constant that a later commit can
redefine — the same argument `d8f41c6a3b92` makes for spelling out the
cohort vocabulary instead of importing the enum.
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

API_ROOT = Path(__file__).resolve().parents[1]

BEFORE_IDENTITY = "a4d81c6b0e57"
IDENTITY_REVISION = "b7e2f9c41a68"

# The versions `b7e2f9c41a68` was written against. Literals, deliberately:
# importing them from the module would make this test agree with whatever
# the module says, which is the bug.
V1_LOGICAL = "domain-logical-identity-v1"
V1_CONTENT = "domain-content-v1"

T0 = datetime(2026, 9, 13, 12, 0, tzinfo=UTC)
GAME = "2026_01_KC_BUF"


def _alembic(db_url: str, *args: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "FDE_DATABASE_URL": db_url}
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=API_ROOT, env=env, capture_output=True, text=True, timeout=300, check=False,
    )


@pytest.fixture()
def unhashed_db(tmp_path) -> str:
    """A database one revision BEFORE the identity migration, holding rows.

    This is the state the identity backfill exists for, and the state no
    other test puts it in.
    """
    db = tmp_path / "unhashed.db"
    url = f"sqlite:///{db.as_posix()}"
    r = _alembic(url, "upgrade", BEFORE_IDENTITY)
    assert r.returncode == 0, r.stderr

    engine = create_engine(url, future=True)
    with engine.begin() as conn:
        for i, market in enumerate(("SPREAD", "TOTAL")):
            conn.execute(text(
                "INSERT INTO consensus_snapshots "
                "(data_mode, canonical_game_id, market, method_version, provider_mode, "
                " median_line, eligible_books, quote_ids, is_closing_capture, observed_at) "
                "VALUES ('LIVE_RESEARCH', :g, :m, 'consensus-v2', 'LIVE', -2.5, 3, "
                " :q, 0, :at)"
            ), {"g": GAME, "m": market,
                "q": json.dumps({"quote_ids": [i], "books": ["draftkings"]}), "at": T0})
    engine.dispose()
    return url


def _rows(url: str, sql: str) -> list[tuple]:
    engine = create_engine(url, future=True)
    try:
        with engine.connect() as conn:
            return [tuple(r) for r in conn.execute(text(sql))]
    finally:
        engine.dispose()


class TestTheBackfillLabelsItsOwnRules:
    def test_the_backfill_actually_ran(self, unhashed_db: str) -> None:
        """The guard on this whole file: if the rows were already hashed,
        or the migration skipped them, every other assertion here would
        pass vacuously."""
        assert _alembic(unhashed_db, "upgrade", IDENTITY_REVISION).returncode == 0
        hashed = _rows(unhashed_db, "SELECT COUNT(*) FROM consensus_snapshots "
                                    "WHERE logical_identity_hash IS NOT NULL")
        assert hashed == [(2,)]

    def test_it_stamps_v1_because_it_hashes_by_v1_rules(
        self, unhashed_db: str
    ) -> None:
        """The defect. The backfill renders `cohort` as the row's
        `data_mode`, which is exactly what v1 meant and exactly what v2
        does not. Stamping v2 on that hash claims this code can reproduce
        it, and it cannot."""
        assert _alembic(unhashed_db, "upgrade", IDENTITY_REVISION).returncode == 0
        versions = set(_rows(
            unhashed_db,
            "SELECT DISTINCT logical_identity_version, content_hash_version "
            "FROM consensus_snapshots"))
        assert versions == {(V1_LOGICAL, V1_CONTENT)}, versions

    def test_it_does_not_borrow_the_live_constant(self, unhashed_db: str) -> None:
        """Stated as the property rather than the value, so this keeps
        biting if the live version moves again."""
        from fde_api.forward.domain_identity import (
            CONTENT_HASH_VERSION,
            LOGICAL_IDENTITY_VERSION,
        )

        assert _alembic(unhashed_db, "upgrade", IDENTITY_REVISION).returncode == 0
        stamped = set(_rows(
            unhashed_db,
            "SELECT DISTINCT logical_identity_version FROM consensus_snapshots"))
        if LOGICAL_IDENTITY_VERSION != V1_LOGICAL:
            assert stamped != {(LOGICAL_IDENTITY_VERSION,)}, (
                "the migration stamped today's version on a hash it computed "
                "with the rules of its own revision"
            )
        assert CONTENT_HASH_VERSION  # imported for the same reason; unused value

    def test_a_v1_row_is_refused_rather_than_read_as_a_conflict(
        self, unhashed_db: str
    ) -> None:
        """Why the label matters more than it looks.

        A correctly-labelled v1 row raises `UnknownHashVersion` — this
        code cannot reproduce it, said plainly. A v1 hash MISLABELLED v2
        would be accepted, recomputed, and come back different, which
        downstream reads as a contradiction about a value it treats as
        truth.
        """
        from fde_api.forward.domain_identity import (
            CONTENT_HASH_VERSION,
            LOGICAL_IDENTITY_VERSION,
            UnknownHashVersion,
            require_known_versions,
        )

        assert _alembic(unhashed_db, "upgrade", IDENTITY_REVISION).returncode == 0
        (lv, cv), = set(_rows(
            unhashed_db,
            "SELECT DISTINCT logical_identity_version, content_hash_version "
            "FROM consensus_snapshots"))

        if lv == LOGICAL_IDENTITY_VERSION and cv == CONTENT_HASH_VERSION:
            pytest.skip("live versions still equal the migration's own")
        with pytest.raises(UnknownHashVersion):
            require_known_versions(lv, cv)


# The identity spec exactly as it stood at c001063, the last commit where
# LOGICAL_IDENTITY_VERSION still read v1. Transcribed from
# `git show c001063:apps/api/src/fde_api/forward/domain_identity.py`, not
# from the current module — copying from today's definitions is the
# mistake this class exists to catch, and it is the mistake that was made
# on the first attempt at the migration fix.
V1_FIELDS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "consensus_snapshot": (
        ("canonical_game_id", "market", "cutoff", "cohort", "method_version"),
        ("median_line", "home_price_american", "away_price_american",
         "over_price_american", "under_price_american", "no_vig_home_prob",
         "no_vig_over_prob", "eligible_books", "quote_lineage",
         "line_dispersion", "price_dispersion", "provider_mode"),
    ),
    "research_evaluation": (
        ("canonical_game_id", "prediction_identity", "price_identity",
         "evaluation_type", "cohort", "policy_version", "model_version",
         "decision_context_hash"),
        ("status", "model_probability", "conservative_probability",
         "break_even_probability", "expected_value", "decision_reason_codes",
         "suppressed", "data_completeness", "execution_eligible"),
    ),
    "availability_assessment": (
        ("canonical_game_id", "player_id", "cutoff", "cohort", "method_version"),
        ("state", "active_prob_low", "active_prob_high", "snap_share_low",
         "snap_share_high", "confidence_tier", "is_starting_qb",
         "observation_lineage", "missing_data"),
    ),
    "price_observation": (
        ("canonical_game_id", "market", "selection", "observed_at", "cohort",
         "provider_mode", "source_identity"),
        ("line", "american", "decimal_odds", "break_even_probability",
         "confirmed", "source", "policy_version", "code_commit"),
    ),
}


def _migration_module():
    """Load the revision by path. `migrations/versions` is not a package."""
    import importlib.util

    path = (API_ROOT / "migrations" / "versions"
            / "b7e2f9c41a68_domain_identity_hashes.py")
    spec = importlib.util.spec_from_file_location("_rev_b7e2f9c41a68", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestTheFrozenFieldsAreTheOnesV1Used:
    """The version string alone is not enough.

    `digest()` mixes the version INTO the hash and `canonical_payload`
    decides which fields reach it, so a frozen version over drifted fields
    still produces a hash v1 never produced — labelled as though it had.
    """

    @pytest.mark.parametrize("entity", sorted(V1_FIELDS))
    def test_the_migration_hashes_the_fields_v1_hashed(self, entity: str) -> None:
        frozen = _migration_module()._FIELDS[entity]
        assert frozen == V1_FIELDS[entity], (
            f"{entity}: the migration's frozen field set no longer matches "
            "what v1 actually hashed"
        )

    def test_it_covers_every_entity_the_migration_backfills(self) -> None:
        mod = _migration_module()
        assert {e for _, e in mod._TABLES} == set(V1_FIELDS)

    def test_the_frozen_versions_are_v1(self) -> None:
        mod = _migration_module()
        assert mod._V1_LOGICAL == V1_LOGICAL
        assert mod._V1_CONTENT == V1_CONTENT

    def test_the_live_spec_has_since_moved(self) -> None:
        """Not a requirement — an explanation. If this ever fails, the
        live version was reverted to v1 and the tests above stop proving
        anything, because agreeing with today would agree with v1 too."""
        from fde_api.forward.domain_identity import LOGICAL_IDENTITY_VERSION

        assert LOGICAL_IDENTITY_VERSION != V1_LOGICAL, (
            "live version is back at v1; these tests no longer discriminate"
        )
