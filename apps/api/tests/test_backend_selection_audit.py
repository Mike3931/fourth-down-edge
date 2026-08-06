"""Backend selection must be explicit, and PostgreSQL gates must not degrade.

Two failure modes this guards against, both of which produce a GREEN result
that means nothing:

  1. A module named or reported as PostgreSQL concurrency quietly builds a
     `sqlite://` engine. SQLite serialises writers at the file level, so the
     race never happens and the suite passes.
  2. A PostgreSQL fixture falls back to SQLite when `FDE_DATABASE_URL` is
     unset, so a misconfigured CI job reports success without a server.

Also pins §4: the legacy free-form `#recovery` suffix must never be
generated again.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path
from typing import ClassVar

import pytest

TESTS_DIR = Path(__file__).resolve().parent
SRC_DIR = TESTS_DIR.parent / "src"

def _concurrency_modules() -> list[Path]:
    """Modules that CLAIM to be PostgreSQL concurrency gates.

    Selected by the marker they carry, not by filename. The filename glob
    `test_pg_*.py` was a proxy for the real property, and a proxy is wrong
    in both directions: it caught an evidence-artifact test that merely
    started with the same prefix, and it would have missed a genuine gate
    named anything else. The marker is what actually puts a module in the
    CI gate step, so the marker is what the audit follows.

    Detected by PARSING for a module-level `pytestmark` assignment that
    names the marker, not by searching the text for it. A substring search
    selected this very file, which merely mentions the marker in order to
    audit it - the same false positive, one level up.
    """
    out: list[Path] = []
    for path in sorted(TESTS_DIR.glob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:  # module level only
            if not isinstance(node, ast.Assign):
                continue
            names = {t.id for t in node.targets if isinstance(t, ast.Name)}
            if "pytestmark" not in names:
                continue
            # Any pg_* marker, not just pg_concurrency: the chain gate is
            # also a PostgreSQL-only module, and a fallback there would
            # report PostgreSQL verification it never performed.
            rendered = ast.unparse(node.value)
            if "pg_concurrency" in rendered or "pg_chain" in rendered:
                out.append(path)
                break
    return out


PG_MODULES = _concurrency_modules()


def _detector_lines(tree: ast.AST) -> set[int]:
    """Lines where a literal is COMPARED against, not built from.

    `"#recovery" in key` detects the legacy form; `f"{k}#recovery{n}"`
    creates it. The rule is that nothing may CREATE it - a detector is how
    historical rows stay readable. Membership and equality tests are
    therefore exempt, and everything else is not.
    """
    out: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            for sub in ast.walk(node):
                if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                    out.add(sub.lineno)
    return out


def _docstring_nodes(tree: ast.AST) -> set[int]:
    """Line numbers occupied by docstrings.

    Both audits below match on source text, and a docstring that NAMES the
    forbidden pattern in order to explain why it is forbidden is
    documentation, not a defect. An audit that cannot tell those apart
    produces false positives until someone deletes the explanation - or the
    audit.
    """
    out: set[int] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            end = node.value.end_lineno or node.value.lineno
            out.update(range(node.value.lineno, end + 1))
    return out


class TestPostgresModulesNeverBuildSqlite:
    def test_there_is_at_least_one_postgres_module(self) -> None:
        """Otherwise every assertion below is vacuously true."""
        assert PG_MODULES, "no test_pg_*.py modules found"

    @pytest.mark.parametrize("path", PG_MODULES, ids=lambda p: p.name)
    def test_no_sqlite_url_is_constructed(self, path: Path) -> None:
        """Parsed, not grepped: a `sqlite://` string literal anywhere in a
        PostgreSQL module is a fallback waiting to happen."""
        offenders: list[str] = []
        tree = ast.parse(path.read_text(encoding="utf-8"))
        exempt = _docstring_nodes(tree) | _detector_lines(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if node.lineno in exempt:
                    continue
                v = node.value
                if "sqlite" in v.lower() and "://" in v:
                    offenders.append(f"{path.name}:{node.lineno}: {v[:60]}")
        assert not offenders, "sqlite URL in a PostgreSQL module:\n" + chr(10).join(offenders)

    @pytest.mark.parametrize("path", PG_MODULES, ids=lambda p: p.name)
    def test_the_module_asserts_its_dialect(self, path: Path) -> None:
        src = path.read_text(encoding="utf-8")
        assert 'dialect.name == "postgresql"' in src, (
            f"{path.name} must assert the backend before racing on it"
        )

    @pytest.mark.parametrize("path", PG_MODULES, ids=lambda p: p.name)
    def test_the_module_is_marked(self, path: Path) -> None:
        src = path.read_text(encoding="utf-8")
        assert "pg_concurrency" in src or "pg_chain" in src, (
            f"{path.name} must carry a PostgreSQL gate marker"
        )


class TestPostgresFixturesRefuseToFallBack:
    def test_missing_url_raises_rather_than_defaulting(self, monkeypatch) -> None:
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "pgconftest_probe", TESTS_DIR / "pgconftest.py"
        )
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        monkeypatch.delenv("FDE_DATABASE_URL", raising=False)
        with pytest.raises(mod.PostgresRequired, match="not set"):
            mod.require_pg_url()

    def test_a_non_postgres_url_is_refused(self, monkeypatch) -> None:
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "pgconftest_probe2", TESTS_DIR / "pgconftest.py"
        )
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        monkeypatch.setenv("FDE_DATABASE_URL", "sqlite:///" + os.devnull)
        with pytest.raises(mod.PostgresRequired, match="not a PostgreSQL URL"):
            mod.require_pg_url()

    def test_credentials_are_redacted_in_messages(self, monkeypatch) -> None:
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "pgconftest_probe3", TESTS_DIR / "pgconftest.py"
        )
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        monkeypatch.setenv("FDE_DATABASE_URL", "mysql://user:hunter2@host/db")
        with pytest.raises(mod.PostgresRequired) as e:
            mod.require_pg_url()
        assert "hunter2" not in str(e.value)
        assert "***" in str(e.value)


class TestLegacyRecoveryMechanismIsGone:
    """§4: no production path may generate the free-form suffix."""

    def test_no_source_file_generates_the_legacy_suffix(self) -> None:
        offenders: list[str] = []
        for p in SRC_DIR.rglob("*.py"):
            if "__pycache__" in p.parts:
                continue
            tree = ast.parse(p.read_text(encoding="utf-8"))
            exempt = _docstring_nodes(tree) | _detector_lines(tree)
            for node in ast.walk(tree):
                # A literal or f-string that BUILDS the suffix. A docstring
                # that merely names it is the explanation of this rule.
                if (
                    isinstance(node, ast.Constant)
                    and isinstance(node.value, str)
                    and node.lineno not in exempt
                    and "#recovery" in node.value
                ):
                    offenders.append(f"{p.name}:{node.lineno}")
        assert not offenders, "legacy recovery suffix still generated:\n" + chr(10).join(offenders)

    def test_the_typed_generator_is_used_by_the_scheduler(self) -> None:
        src = (SRC_DIR / "fde_api" / "forward" / "scheduler.py").read_text(encoding="utf-8")
        assert "build_recovery_key(" in src

    def test_the_legacy_form_is_still_recognisable(self) -> None:
        """Historical rows remain readable; they are just never created."""
        from fde_api.forward.recovery import is_legacy_recovery_key

        assert is_legacy_recovery_key("job:cohort:slot#recovery1")
        assert not is_legacy_recovery_key("job:cohort:slot")


class TestTypedRecoveryKey:
    def test_sequence_zero_is_the_original_key_unchanged(self) -> None:
        from fde_api.forward.recovery import build_recovery_key

        key = build_recovery_key(
            job_name="odds_capture", cohort="burn_in", logical_slot="2026-09-10T17:00:00+00:00",
            root_run_id="run_a", sequence=0, original_key="odds_capture:burn_in:X",
        )
        assert key == "odds_capture:burn_in:X"

    def test_a_recovery_key_round_trips(self) -> None:
        from fde_api.forward.recovery import RecoveryKey, build_recovery_key

        original = "odds_capture:burn_in:X"
        key = build_recovery_key(
            job_name="odds_capture", cohort="burn_in", logical_slot="s",
            root_run_id="run_a", sequence=3, original_key=original,
        )
        parsed_original, root, seq = RecoveryKey.parse(key)
        assert parsed_original == original
        assert root == "run_a"
        assert seq == 3

    def test_sequences_produce_distinct_keys(self) -> None:
        from fde_api.forward.recovery import build_recovery_key

        keys = {
            build_recovery_key(
                job_name="j", cohort="c", logical_slot="s",
                root_run_id="r", sequence=n, original_key="orig",
            )
            for n in range(4)
        }
        assert len(keys) == 4

    def test_a_negative_sequence_is_refused(self) -> None:
        from fde_api.forward.recovery import RecoveryError, build_recovery_key

        with pytest.raises(RecoveryError, match="may not be negative"):
            build_recovery_key(
                job_name="j", cohort="c", logical_slot="s",
                root_run_id="r", sequence=-1, original_key="orig",
            )

    def test_a_plain_key_parses_as_sequence_zero(self) -> None:
        from fde_api.forward.recovery import RecoveryKey

        original, root, seq = RecoveryKey.parse("job:cohort:20260910T170000Z")
        assert original == "job:cohort:20260910T170000Z"
        assert root is None
        assert seq == 0


class TestTheLegacyClosingFlagIsNeverWritten:
    """`consensus_snapshots.is_closing_capture` is read-only history.

    The close is its own record now. The column stays because rows written
    before the capture table carry it, and dropping it would destroy the
    only evidence of what was treated as the close then. But nothing may
    SET it again: a snapshot is an observation, and mutating one to mark it
    is the edit this whole design forbids.

    A source-level audit rather than a behavioural test, deliberately. The
    stale reference that motivated it lived in a PostgreSQL-only module, so
    the local suite never executed it and a green local run said nothing.
    Parsing the source needs no database.
    """

    def _assignments(self, path: Path) -> list[int]:
        """Lines that ASSIGN to is_closing_capture. Reading it is fine."""
        tree = ast.parse(path.read_text(encoding="utf-8"))
        hits: list[int] = []
        for node in ast.walk(tree):
            targets: list[ast.expr] = []
            if isinstance(node, ast.Assign):
                targets = list(node.targets)
            elif isinstance(node, ast.AugAssign | ast.AnnAssign):
                targets = [node.target]
            for tgt in targets:
                if isinstance(tgt, ast.Attribute) and tgt.attr == "is_closing_capture":
                    hits.append(node.lineno)
            # A keyword argument is an assignment too, but ONLY on the model
            # constructor. `authorize_poll(is_closing_capture=...)` is a
            # quota-priority argument that happens to share the name and has
            # nothing to do with the column - flagging it was a false
            # positive this audit produced against itself on its first run.
            if isinstance(node, ast.Call) and _constructs_snapshot(node):
                for kw in node.keywords:
                    if kw.arg == "is_closing_capture" and not _is_false(kw.value):
                        hits.append(node.lineno)
        return hits

    def test_no_source_file_sets_the_legacy_flag(self) -> None:
        offenders: list[str] = []
        for p in SRC_DIR.rglob("*.py"):
            if "__pycache__" in p.parts:
                continue
            # The model DECLARES the column; declaring is not writing.
            if p.name == "forward_models.py":
                continue
            offenders += [f"{p.name}:{ln}" for ln in self._assignments(p)]
        assert not offenders, (
            "the legacy closing flag is still being written:\n" + chr(10).join(offenders)
        )

    def test_the_column_still_exists_for_historical_rows(self) -> None:
        """Removing it would be worse than leaving it: the flag is the only
        record of which snapshot was the close before captures existed."""
        from fde_api.db.forward_models import ConsensusSnapshot

        assert "is_closing_capture" in ConsensusSnapshot.__table__.c

    def test_the_capture_table_carries_its_rule_version(self) -> None:
        """Without the version, a close captured under one rule and one
        captured under a revised rule are indistinguishable."""
        from fde_api.db.forward_models import ClosingCapture

        for column in ("selection_rule", "selection_rule_version",
                       "consensus_snapshot_id", "status", "missing_close_reason",
                       "conflict_reason", "cohort", "provider_mode"):
            assert column in ClosingCapture.__table__.c, f"missing {column}"


def _is_false(node: ast.expr) -> bool:
    """True for a literal `False`, which merely restates the default."""
    return isinstance(node, ast.Constant) and node.value is False


def _constructs_snapshot(node: ast.Call) -> bool:
    """True when this call builds a ConsensusSnapshot."""
    func = node.func
    name = getattr(func, "id", None) or getattr(func, "attr", None)
    return name == "ConsensusSnapshot"


class TestTheParityTestCannotBeSilenced:
    """No skip, xfail or conditional deselect in the parity module.

    A pytest run whose only selected test was skipped exits 0. The parity
    test spent two increments skipped with a truthful reason while every CI
    run reported success, so "the suite is green" was true and told you
    nothing about parity.

    Parsed rather than grepped: a decorator is what actually silences a
    test, and a docstring that discusses skipping is documentation.
    """

    PARITY_MODULE = TESTS_DIR / "test_chain_direct.py"
    SILENCERS: ClassVar[set[str]] = {"skip", "skipif", "xfail"}

    def _markers(self) -> list[str]:
        tree = ast.parse(self.PARITY_MODULE.read_text(encoding="utf-8"))
        found: list[str] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                continue
            for dec in node.decorator_list:
                rendered = ast.unparse(dec)
                if not rendered.startswith(("pytest.mark.", "mark.")):
                    continue
                marker = rendered.split("(")[0].split(".")[-1]
                if marker in self.SILENCERS:
                    found.append(f"{node.name}: @{rendered[:70]}")
        return found

    def test_no_skip_or_xfail_marker_exists(self) -> None:
        offenders = self._markers()
        assert not offenders, (
            "the parity module contains a silencing marker:\n" + chr(10).join(offenders)
        )

    def test_no_runtime_skip_call(self) -> None:
        """`pytest.skip(...)` inside a body silences just as effectively."""
        tree = ast.parse(self.PARITY_MODULE.read_text(encoding="utf-8"))
        offenders: list[str] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            rendered = ast.unparse(node.func)
            if rendered in ("pytest.skip", "pytest.xfail", "skip", "xfail"):
                offenders.append(f"line {node.lineno}: {rendered}(...)")
        assert not offenders, (
            "the parity module skips at runtime:\n" + chr(10).join(offenders)
        )

    def test_the_named_parity_test_still_exists(self) -> None:
        """A rename would make the JUnit gate collect zero and pass
        vacuously, so the name is pinned in both places."""
        src = self.PARITY_MODULE.read_text(encoding="utf-8")
        assert "def test_the_semantic_hashes_match(" in src
        assert "class TestDirectAndSchedulerChainsAgree:" in src

        gate = (TESTS_DIR.parent / "scripts" / "verify_parity_junit.py").read_text(
            encoding="utf-8")
        assert "test_the_semantic_hashes_match" in gate
        assert "TestDirectAndSchedulerChainsAgree" in gate


class TestTheLegacyClosingFlagIsNeverRead:
    """`closing_captures` is the sole current authority.

    The no-write audit already exists. Reading is the other half: a service
    that SELECTs on `is_closing_capture` is deciding from archival state,
    and every row written since the capture table landed carries False. It
    would silently find nothing, or worse, find only pre-migration rows.

    Reads are permitted in code explicitly classified as historical audit,
    migration, or compatibility reporting - so the flag stays inspectable
    without being authoritative.
    """

    # Modules allowed to read the flag, each for a stated reason.
    CLASSIFIED_READERS: ClassVar[dict[str, str]] = {
        # Reports what the chain holds, including legacy rows; the reader is
        # a census, not a decision.
        "chain.py": "compatibility reporting - counts legacy rows for audit",
        # `latest_consensus_at` excludes rows carrying the legacy flag. Every
        # row written since the capture table landed has it False, so the
        # filter only ever affects PRE-MIGRATION rows - which is exactly what
        # it is for. It selects a consensus, never a close.
        "consensus.py": (
            "compatibility filtering - excludes pre-migration rows that were "
            "marked as closes before closing_captures existed"
        ),
    }

    AUTHORITY_MODULES: ClassVar[tuple[str, ...]] = (
        "closing.py",      # closing selection
        "ledger.py",       # CLV, settlement, forward performance
        "semantic_hash.py",  # parity and semantic hashing
        "handlers.py",     # candidate evaluation and capture
        "prices.py",
        "results.py",
        "vintages.py",
    )

    def _reads(self, path: Path) -> list[int]:
        """Lines that READ the flag. Assignment is the no-write audit's job."""
        tree = ast.parse(path.read_text(encoding="utf-8"))
        assigned: set[int] = set()
        for node in ast.walk(tree):
            targets: list[ast.expr] = []
            if isinstance(node, ast.Assign):
                targets = list(node.targets)
            elif isinstance(node, ast.AugAssign | ast.AnnAssign):
                targets = [node.target]
            for tgt in targets:
                if isinstance(tgt, ast.Attribute) and tgt.attr == "is_closing_capture":
                    assigned.add(node.lineno)
        hits: list[int] = []
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and node.attr == "is_closing_capture"
                and node.lineno not in assigned
            ):
                hits.append(node.lineno)
        return hits

    @pytest.mark.parametrize("module", AUTHORITY_MODULES)
    def test_no_authority_module_reads_the_legacy_flag(self, module: str) -> None:
        path = SRC_DIR / "fde_api" / "forward" / module
        if not path.exists():
            pytest.skip(f"{module} does not exist in this checkout")
        hits = self._reads(path)
        assert not hits, (
            f"{module} reads the legacy closing flag at line(s) {hits}. "
            "closing_captures is the sole current authority; classify the "
            "module as historical-audit code if the read is deliberate."
        )

    def test_every_reader_outside_the_authority_set_is_classified(self) -> None:
        unclassified: list[str] = []
        for path in sorted((SRC_DIR / "fde_api").rglob("*.py")):
            if "__pycache__" in path.parts or path.name == "forward_models.py":
                continue
            if not self._reads(path):
                continue
            if path.name in self.AUTHORITY_MODULES:
                continue  # covered by the parametrised test above
            if path.name not in self.CLASSIFIED_READERS:
                unclassified.append(f"{path.name}:{self._reads(path)}")
        assert not unclassified, (
            "unclassified reads of the legacy closing flag:\n"
            + chr(10).join(unclassified)
            + "\nAdd the module to CLASSIFIED_READERS with a reason, or stop reading it."
        )

    def test_migrations_may_reference_the_flag(self) -> None:
        """Migration code is explicitly permitted: the column exists because
        historical rows carry it, and a migration is where that is handled."""
        migrations = SRC_DIR.parent / "migrations" / "versions"
        assert migrations.exists()
        # No assertion about content - this documents the exemption and
        # fails only if the directory disappears, which would mean the
        # exemption no longer has a subject.

    def test_closing_selection_uses_the_capture_table(self) -> None:
        """Behavioural counterpart: the selector reads ClosingCapture."""
        src = (SRC_DIR / "fde_api" / "forward" / "closing.py").read_text(
            encoding="utf-8")
        assert "ClosingCapture" in src
        assert "is_closing_capture" not in src
