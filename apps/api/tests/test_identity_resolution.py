"""Upgrading a database that already contains duplicates.

Three conditions, and the migration must behave differently in each:

  no manifest              refuse, change nothing, constrain nothing
  MANUAL_REVIEW_UNRESOLVED refuse, change nothing, constrain nothing
  a governed resolution    apply it, then constrain

The second is the one that matters most. It is tempting to treat "a human
looked at it" as approval; it is not. Recording that somebody looked and
could not decide has to leave the database exactly as refused as silence
does, or the disposition becomes a way to wave things through.
"""

from __future__ import annotations

import json

import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine, text

from fde_api.forward.identity_resolution import (
    ARCHIVED_NAMESPACE,
    CURRENT_NAMESPACE,
    SUPERSEDED_NAMESPACE,
    Disposition,
    ResolutionManifest,
    ResolutionRefused,
    apply_plan,
    plan_group,
)

LID = "a" * 64


def _manifest(**overrides):
    entry = {
        "table": "consensus_snapshots",
        "logical_identity_hash": LID,
        "row_ids": [1, 2],
        "disposition": "CANONICALIZE_EXACT_DUPLICATES",
        "rationale": "The scheduler retried after a timeout; one write, recorded twice.",
        "authorised_by": "data-owner",
        "content_hashes_at_authoring": ["c" * 64, "c" * 64],
        "retain_row_id": 1,
    }
    entry.update(overrides)
    return ResolutionManifest.from_dict({
        "artifact_schema_version": "identity-resolution-manifest-v1",
        "resolutions": [entry],
    })


def _plan(manifest, rows=((1, "c" * 64), (2, "c" * 64))):
    return plan_group(table="consensus_snapshots", logical_identity_hash=LID,
                      rows=list(rows), manifest=manifest)


# --------------------------------------------------------------------------- #
# The three upgrade conditions
# --------------------------------------------------------------------------- #

class TestTheThreeUpgradeConditions:
    def test_no_manifest_refuses(self) -> None:
        plan = _plan(ResolutionManifest.empty())
        assert not plan.resolved
        assert "no resolution in the manifest" in " ".join(plan.problems)

    def test_manual_review_unresolved_refuses_just_as_hard(self) -> None:
        """A human looked and could not decide. That is not approval."""
        plan = _plan(_manifest(disposition="MANUAL_REVIEW_UNRESOLVED",
                               rationale="Three probabilities, no way to tell which "
                                         "model run produced which."))
        assert not plan.resolved
        assert "MANUAL_REVIEW_UNRESOLVED" in " ".join(plan.problems)
        # And it must not be quietly promoted by the presence of the other
        # required fields - a fully-formed entry is still a refusal.
        assert plan.entry is not None
        assert plan.entry.disposition is Disposition.MANUAL_REVIEW_UNRESOLVED

    def test_a_governed_resolution_is_allowed_through(self) -> None:
        plan = _plan(_manifest())
        assert plan.resolved, plan.problems


# --------------------------------------------------------------------------- #
# What the manifest will not authorise
# --------------------------------------------------------------------------- #

class TestTheManifestRefusesToBeAShortcut:
    def test_a_resolution_without_a_rationale_is_refused(self) -> None:
        assert not _plan(_manifest(rationale="  ")).resolved

    def test_a_resolution_without_an_authorising_party_is_refused(self) -> None:
        assert not _plan(_manifest(authorised_by="")).resolved

    def test_canonicalize_is_refused_when_the_rows_disagree(self) -> None:
        """The disposition asserts the rows are the same record written
        twice. If their content differs, that assertion is false and
        applying it discards one of them."""
        rows = [(1, "c" * 64), (2, "d" * 64)]
        plan = _plan(_manifest(content_hashes_at_authoring=["c" * 64, "d" * 64]),
                     rows=rows)
        assert not plan.resolved
        assert any("not identical" in p or "disagree" in p for p in plan.problems)

    def test_a_stale_manifest_is_refused(self) -> None:
        """The author consented to a database that no longer exists."""
        plan = _plan(_manifest(), rows=[(1, "c" * 64), (2, "e" * 64)])
        assert not plan.resolved
        assert any("no longer exists" in p for p in plan.problems)

    def test_a_manifest_that_misses_a_row_is_refused(self) -> None:
        plan = _plan(_manifest(), rows=[(1, "c" * 64), (2, "c" * 64), (3, "c" * 64)])
        assert not plan.resolved
        assert any("database has" in p for p in plan.problems)

    def test_selecting_an_authority_requires_evidence(self) -> None:
        plan = _plan(
            _manifest(disposition="SELECT_AUTHORITATIVE_AND_SUPERSEDE",
                      content_hashes_at_authoring=["c" * 64, "d" * 64]),
            rows=[(1, "c" * 64), (2, "d" * 64)])
        assert not plan.resolved
        assert any("evidence" in p for p in plan.problems)

    def test_keep_distinct_is_refused_on_identical_rows(self) -> None:
        """Identical rows are not two records the identity failed to tell
        apart. They are one record written twice."""
        plan = _plan(_manifest(
            disposition="KEEP_DISTINCT_AFTER_IDENTITY_CORRECTION",
            identity_corrections={"1": "run-a", "2": "run-b"},
            retain_row_id=None))
        assert not plan.resolved
        assert any("nothing to distinguish" in p for p in plan.problems)

    def test_keep_distinct_corrections_must_actually_differ(self) -> None:
        plan = _plan(
            _manifest(disposition="KEEP_DISTINCT_AFTER_IDENTITY_CORRECTION",
                      content_hashes_at_authoring=["c" * 64, "d" * 64],
                      identity_corrections={"1": "same", "2": "same"},
                      retain_row_id=None),
            rows=[(1, "c" * 64), (2, "d" * 64)])
        assert not plan.resolved
        assert any("still collide" in p for p in plan.problems)

    def test_an_unknown_disposition_is_refused_not_ignored(self) -> None:
        with pytest.raises(ResolutionRefused):
            ResolutionManifest.from_dict({
                "artifact_schema_version": "identity-resolution-manifest-v1",
                "resolutions": [{"table": "t", "logical_identity_hash": LID,
                                 "row_ids": [1, 2], "disposition": "JUST_FIX_IT"}],
            })

    def test_a_manifest_of_the_wrong_schema_is_refused(self) -> None:
        with pytest.raises(ResolutionRefused):
            ResolutionManifest.from_dict({"artifact_schema_version": "v0",
                                          "resolutions": []})

    def test_applying_an_unresolved_plan_raises(self) -> None:
        """Belt and braces: even if a caller ignores `resolved`."""
        with pytest.raises(ResolutionRefused):
            apply_plan(None, _plan(ResolutionManifest.empty()))


# --------------------------------------------------------------------------- #
# What applying one actually does to rows
# --------------------------------------------------------------------------- #

@pytest.fixture
def db():
    engine = create_engine("sqlite://", future=True)
    with engine.begin() as c:
        c.execute(text(
            "CREATE TABLE consensus_snapshots (id INTEGER PRIMARY KEY, "
            "logical_identity_version TEXT, logical_identity_hash TEXT, "
            "content_hash TEXT)"))
        c.execute(text(
            "CREATE TABLE closing_captures (id INTEGER PRIMARY KEY, "
            "consensus_snapshot_id INTEGER)"))
    return engine


def _seed(engine, hashes):
    with engine.begin() as c:
        for i, h in enumerate(hashes, start=1):
            c.execute(text(
                "INSERT INTO consensus_snapshots VALUES (:i, :v, :l, :h)"),
                {"i": i, "v": CURRENT_NAMESPACE, "l": LID, "h": h})
        c.execute(text("INSERT INTO closing_captures VALUES (1, 2)"))


class TestApplyingAResolution:
    def test_canonicalize_repoints_lineage_and_deletes_nothing(self, db) -> None:
        _seed(db, ["c" * 64, "c" * 64])
        with db.begin() as c:
            action = apply_plan(c, _plan(_manifest()))
        with db.connect() as c:
            rows = dict(c.execute(text(
                "SELECT id, logical_identity_version FROM consensus_snapshots")).all())
            capture = c.execute(text(
                "SELECT consensus_snapshot_id FROM closing_captures")).scalar_one()
        assert action["rows_deleted"] == 0
        assert set(rows) == {1, 2}, "a row was deleted"
        assert rows[1] == CURRENT_NAMESPACE
        assert rows[2] == ARCHIVED_NAMESPACE
        # The capture pointed at the archived row; it now points at the
        # retained one, which says the same thing.
        assert capture == 1

    def test_supersession_leaves_lineage_pointing_where_it_pointed(self, db) -> None:
        """The capture recorded which snapshot was USED. If we repointed it,
        the record would claim a snapshot was used that never was."""
        _seed(db, ["c" * 64, "d" * 64])
        manifest = _manifest(
            disposition="SELECT_AUTHORITATIVE_AND_SUPERSEDE",
            content_hashes_at_authoring=["c" * 64, "d" * 64],
            evidence="Provider re-issued the 14:02 quote set; row 1 matches the "
                     "re-issued file, row 2 matches the withdrawn one.")
        with db.begin() as c:
            action = apply_plan(c, _plan(manifest, rows=[(1, "c" * 64), (2, "d" * 64)]))
        with db.connect() as c:
            rows = dict(c.execute(text(
                "SELECT id, logical_identity_version FROM consensus_snapshots")).all())
            capture = c.execute(text(
                "SELECT consensus_snapshot_id FROM closing_captures")).scalar_one()
        assert action["rows_deleted"] == 0
        assert rows[2] == SUPERSEDED_NAMESPACE
        assert capture == 2, "supersession rewrote history"

    def test_keep_distinct_leaves_every_row_current(self, db) -> None:
        _seed(db, ["c" * 64, "d" * 64])
        manifest = _manifest(
            disposition="KEEP_DISTINCT_AFTER_IDENTITY_CORRECTION",
            content_hashes_at_authoring=["c" * 64, "d" * 64],
            identity_corrections={"1": "a", "2": "b"},
            retain_row_id=None)
        with db.begin() as c:
            apply_plan(c, _plan(manifest, rows=[(1, "c" * 64), (2, "d" * 64)]))
        with db.connect() as c:
            rows = c.execute(text(
                "SELECT logical_identity_version, logical_identity_hash "
                "FROM consensus_snapshots ORDER BY id")).all()
        assert [r[0] for r in rows] == [CURRENT_NAMESPACE, CURRENT_NAMESPACE]
        assert rows[0][1] != rows[1][1], "the correction did not separate them"
        assert len({r[1] for r in rows}) == 2

    def test_the_unique_constraint_is_satisfiable_afterwards(self, db) -> None:
        """The point of all three: whatever the disposition, a unique index
        on (version, hash) can be created when it is done."""
        _seed(db, ["c" * 64, "c" * 64])
        with db.begin() as c:
            apply_plan(c, _plan(_manifest()))
        with db.begin() as c:
            c.execute(text(
                "CREATE UNIQUE INDEX uq_test ON consensus_snapshots "
                "(logical_identity_version, logical_identity_hash)"))
        with db.connect() as c:
            assert c.execute(text("SELECT COUNT(*) FROM consensus_snapshots")).scalar() == 2


class TestTheManifestIsSerialisable:
    def test_a_manifest_round_trips_through_json(self, tmp_path) -> None:
        payload = {
            "artifact_schema_version": "identity-resolution-manifest-v1",
            "resolutions": [{
                "table": "consensus_snapshots", "logical_identity_hash": LID,
                "row_ids": [1, 2], "disposition": "CANONICALIZE_EXACT_DUPLICATES",
                "rationale": "retry", "authorised_by": "data-owner",
                "content_hashes_at_authoring": ["c" * 64, "c" * 64],
                "retain_row_id": 1,
            }],
        }
        path = tmp_path / "m.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        manifest = ResolutionManifest.load(path)
        assert manifest.for_group("consensus_snapshots", LID) is not None
        assert manifest.source == str(path)

    def test_an_absent_path_is_an_empty_manifest_not_an_error(self, tmp_path) -> None:
        """Absent means "nothing is authorised", which is the safe default -
        and it must not be confused with a manifest that failed to parse."""
        assert ResolutionManifest.load(tmp_path / "nope.json").entries == {}
        assert ResolutionManifest.load(None).entries == {}

    def test_two_resolutions_for_one_group_are_refused(self) -> None:
        one = {"table": "t", "logical_identity_hash": LID, "row_ids": [1, 2],
               "disposition": "MANUAL_REVIEW_UNRESOLVED"}
        with pytest.raises(ResolutionRefused):
            ResolutionManifest.from_dict({
                "artifact_schema_version": "identity-resolution-manifest-v1",
                "resolutions": [one, dict(one)],
            })


def test_no_disposition_emits_a_delete() -> None:
    """A source-level check to go with the behavioural ones: the module
    must contain no DELETE. The moment a destructive path exists it becomes
    the convenient one."""
    import pathlib
    import re

    import fde_api.forward.identity_resolution as mod

    source = pathlib.Path(mod.__file__).read_text(encoding="utf-8")
    # SQL deletes and ORM deletes, not the word in a comment. Matching the
    # bare word finds the docstring that promises there are none.
    statements = [
        line for line in source.splitlines()
        if re.search(r"DELETE\s+FROM", line, re.IGNORECASE)
        or re.search(r"\.delete\(", line)
        or re.search(r"TRUNCATE", line, re.IGNORECASE)
    ]
    assert not statements, statements


def test_the_migration_consults_the_manifest() -> None:
    """The migration must not carry its own private duplicate policy."""
    import pathlib

    path = (pathlib.Path(__file__).resolve().parents[1] / "migrations" / "versions"
            / "b7e2f9c41a68_domain_identity_hashes.py")
    source = path.read_text(encoding="utf-8")
    assert "plan_group" in source and "ResolutionManifest.load" in source
    assert "apply_plan" in source
    # And it must still refuse by default: the environment variable is the
    # only way to supply one, and its absence is an empty manifest.
    assert "FDE_IDENTITY_RESOLUTION_MANIFEST" in source


def test_sqlalchemy_is_importable_for_the_applier() -> None:
    assert sa.__version__


class TestOnlyIdentityBearingTablesMayBeResolved:
    """`table` is interpolated into SQL, because a table name cannot be a
    bound parameter.

    Nothing untrusted reaches it: the manifest path comes from an
    environment variable, and anyone able to write that file already
    controls the deployment. The allowlist exists so correctness does not
    depend on that argument staying true - the set of tables carrying a
    logical identity is fixed and known, and accepting anything else is one
    typo away from an UPDATE against a table nobody meant to touch.
    """

    def test_the_four_identity_tables_are_accepted(self) -> None:
        from fde_api.forward.identity_resolution import RESOLVABLE_TABLES

        assert set(RESOLVABLE_TABLES) == {
            "availability_assessments",
            "consensus_snapshots",
            "forward_ledger",
            "manual_book_price_entries",
        }

    @pytest.mark.parametrize(
        "table", ["users", "consensus_snapshots; DROP TABLE users", ""],
    )
    def test_a_manifest_naming_another_table_is_refused(self, table) -> None:
        # Planned against the SAME table the manifest names, so the entry is
        # found and the allowlist is what rejects it - not a missing entry.
        plan = plan_group(table=table, logical_identity_hash=LID,
                          rows=[(1, "c" * 64), (2, "c" * 64)],
                          manifest=_manifest(table=table))
        assert not plan.resolved
        assert any("carries no logical identity" in p for p in plan.problems), (
            plan.problems
        )

    def test_the_applier_checks_independently_of_the_plan(self) -> None:
        """`apply_plan` is what turns a name into SQL text, so it has to be
        safe on its own. A caller constructing a GroupPlan by some other
        route must not be able to route around the allowlist."""
        from fde_api.forward.identity_resolution import (
            GroupPlan,
            UnknownTable,
            apply_plan,
        )

        entry = _manifest().for_group("consensus_snapshots", LID)
        assert entry is not None
        forged = GroupPlan(
            table="some_other_table", logical_identity_hash=LID,
            row_ids=[1, 2], content_hashes=["c" * 64, "c" * 64],
            classification="EXACT_DUPLICATE", entry=entry, problems=[],
        )
        with pytest.raises(UnknownTable):
            apply_plan(None, forged)

    @pytest.mark.parametrize(
        ("relative_path", "declaration"),
        [
            ("migrations/versions/b7e2f9c41a68_domain_identity_hashes.py",
             "_TABLES: tuple[tuple[str, str], ...] = ("),
            ("scripts/identity_duplicate_inventory.py",
             "TABLES: tuple[tuple[str, str], ...] = ("),
        ],
    )
    def test_every_copy_of_the_table_list_agrees(
        self, relative_path, declaration
    ) -> None:
        """Three hand-kept lists of the same four tables.

        The migration decides which tables get the constraint, the
        inventory decides which are scanned for duplicates, and the
        allowlist decides which may be written. They have to describe the
        same set, and this repository has already been bitten once by two
        copies of the same thing drifting apart.
        """
        import pathlib
        import re

        from fde_api.forward.identity_resolution import RESOLVABLE_TABLES

        source = (pathlib.Path(__file__).resolve().parents[1] / relative_path
                  ).read_text(encoding="utf-8")
        # Split on the line-initial ")" closing the OUTER tuple; the first
        # bare ")" closes the first inner tuple and truncates to one table.
        block = source.split(declaration, 1)[1].split("\n)", 1)[0]
        declared = set(re.findall(r'\("([a-z_]+)",', block))
        assert declared == set(RESOLVABLE_TABLES), relative_path
