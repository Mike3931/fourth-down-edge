"""No test may hardcode a platform-specific interpreter or path layout.

`tests/test_migration_upgrade.py` invoked `.venv/Scripts/python` — the
Windows venv layout. On Linux CI the layout is `.venv/bin/python`, so the
step died with FileNotFoundError before alembic ran, and because the later
steps depended on it, the downgrade/re-upgrade check and the ENTIRE Python
suite were skipped. A path bug cost the whole PostgreSQL verification while
every local run stayed green.

This is a repository audit rather than a behavioural test: it reads the
source of the test suite and fails on the patterns that cause that class of
CI-only breakage.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
SRC_DIR = TESTS_DIR.parent / "src"
SCRIPTS_DIR = TESTS_DIR.parent / "scripts"

# Windows-only or POSIX-only interpreter/venv layouts. Either is wrong to
# hardcode: the correct answer is always the running interpreter.
PLATFORM_PATHS = re.compile(
    r"""
      \.venv[/\\]Scripts       # Windows venv layout
    | \.venv[/\\]bin           # POSIX venv layout
    | Scripts[/\\]python       # Windows interpreter
    | /usr/bin/python          # absolute POSIX interpreter
    | python\.exe              # Windows executable name
    | (?<![A-Za-z])[A-Za-z]:[/\\]   # absolute Windows drive path.
                                    # The lookbehind matters: without it
                                    # this matches the scheme in every URL
                                    # ("https://", "sqlite://"), which is a
                                    # false positive that would get the
                                    # whole audit disabled.
    """,
    re.VERBOSE,
)


def _code_only(path: Path) -> list[tuple[int, str]]:
    """Source lines with docstrings and comments removed.

    The audit must look at CODE. An explanatory docstring that NAMES the
    bad pattern in order to warn about it is documentation, not a defect,
    and an audit that cannot tell the difference produces false positives
    until someone switches it off.
    """
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    doc_lines: set[int] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
            and node.value.lineno
        ):
            end = node.value.end_lineno or node.value.lineno
            doc_lines.update(range(node.value.lineno, end + 1))
    out: list[tuple[int, str]] = []
    for i, line in enumerate(text.splitlines(), 1):
        if i in doc_lines:
            continue
        code = line.split("#", 1)[0]
        if code.strip():
            out.append((i, code))
    return out


def _python_files() -> list[Path]:
    files: list[Path] = []
    for root in (TESTS_DIR, SRC_DIR, SCRIPTS_DIR):
        if root.exists():
            files.extend(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)
    return files


class TestNoHardcodedInterpreterPaths:
    def test_no_module_hardcodes_a_platform_specific_path(self) -> None:
        offenders: list[str] = []
        for p in _python_files():
            if p.name == Path(__file__).name:
                continue  # this file names the patterns on purpose
            for i, code in _code_only(p):
                if PLATFORM_PATHS.search(code):
                    offenders.append(f"{p.relative_to(TESTS_DIR.parent)}:{i}: {code.strip()[:90]}")
        assert not offenders, "platform-specific paths found:\n" + "\n".join(offenders)

    def test_subprocess_python_invocations_use_sys_executable(self) -> None:
        """Any subprocess that runs Python must run THIS Python.

        Parsed rather than grepped: a call whose first argument is a string
        literal ending in `python` is hardcoding an interpreter, whatever
        path it happens to name.
        """
        offenders: list[str] = []
        for p in _python_files():
            if p.name == Path(__file__).name:
                continue
            tree = ast.parse(p.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                name = getattr(func, "attr", None) or getattr(func, "id", None)
                if name not in ("run", "Popen", "check_output", "call", "check_call"):
                    continue
                if not node.args:
                    continue
                first = node.args[0]
                if isinstance(first, ast.List) and first.elts:
                    head = first.elts[0]
                    if (
                        isinstance(head, ast.Constant)
                        and isinstance(head.value, str)
                        and "python" in head.value.lower()
                    ):
                        offenders.append(
                            f"{p.relative_to(TESTS_DIR.parent)}:{head.lineno}: {head.value}"
                        )
        assert not offenders, (
            "subprocess calls hardcoding an interpreter:\n" + "\n".join(offenders)
        )


class TestTheMigrationHelperIsPortable:
    def test_alembic_helper_uses_the_running_interpreter(self) -> None:
        lines = [c for _, c in _code_only(TESTS_DIR / "test_migration_upgrade.py")]
        code = chr(10).join(lines)
        assert "sys.executable" in code
        assert ".venv" not in code  # docstrings may discuss it; code may not

    def test_sys_executable_exists_on_this_platform(self) -> None:
        """Trivially true, and that is the point: it is true everywhere,
        which is exactly what the hardcoded path was not."""
        assert Path(sys.executable).exists()

    @pytest.mark.parametrize("layout", [".venv/Scripts/python", ".venv/bin/python"])
    def test_neither_venv_layout_is_assumed(self, layout: str) -> None:
        lines = [c for _, c in _code_only(TESTS_DIR / "test_migration_upgrade.py")]
        code = chr(10).join(lines)
        assert layout not in code


class TestPathsAreConstructedPortably:
    def test_no_backslash_path_separators_in_string_literals(self) -> None:
        """A Windows separator baked into a literal breaks on POSIX."""
        offenders: list[str] = []
        for p in _python_files():
            if p.name == Path(__file__).name:
                continue
            tree = ast.parse(p.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    v = node.value
                    if chr(10) in v or len(v) > 200:
                        continue  # a docstring or prose block, not a path
                    if "\\" in v and any(
                        seg in v for seg in ("/", "tests", "src", "data", ".py")
                    ) and "\\n" not in v and "\\r" not in v and "\\t" not in v:
                        offenders.append(f"{p.relative_to(TESTS_DIR.parent)}:{node.lineno}: {v[:70]}")
        assert not offenders, "backslash paths in literals:\n" + "\n".join(offenders)
