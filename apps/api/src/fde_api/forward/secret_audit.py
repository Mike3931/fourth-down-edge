"""Check whether a configured secret has ever been written to the database.

The odds provider takes its key as a URL query parameter, so httpx put it
in the message of every transport error, and those messages were persisted
to `ScheduledJobRun.error_summary`. That is fixed (see
`odds.TheOddsApiProvider._redact` and `util.redact_secrets`), but the fix
is forward-looking: rows written before it stay as they were.

This module answers the operational question that follows — *did it
actually happen here?* — and can clean up if it did.

Run it against any deployment:

    python -m fde_api.forward.secret_audit           # report only
    python -m fde_api.forward.secret_audit --redact  # rewrite affected rows

It reports presence, never values. A tool for finding leaked credentials
that prints them to a terminal has not helped anyone.
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

from sqlalchemy import select
from sqlalchemy.orm import Session

from fde_api.db.forward_models import ScheduledJobRun
from fde_api.util import SECRET_ENV_VARS


@dataclass
class SecretAuditResult:
    configured: list[str] = field(default_factory=list)
    unconfigured: list[str] = field(default_factory=list)
    rows_scanned: int = 0
    rows_with_summary: int = 0
    affected_run_ids: list[str] = field(default_factory=list)
    affected_by_secret: dict[str, int] = field(default_factory=dict)
    redacted: int = 0

    @property
    def clean(self) -> bool:
        return not self.affected_run_ids

    @property
    def conclusive(self) -> bool:
        """Whether the answer can be trusted.

        Two ways to be sure. Either no row carries an error summary at all,
        in which case nothing could contain a secret regardless of what is
        configured — or at least one secret IS configured, so there was
        something to search for.

        The gap is a secret that leaked and was later rotated or unset:
        those rows exist but cannot be matched. Saying "clean" there would
        be the one genuinely dangerous answer this tool could give.
        """
        return self.rows_with_summary == 0 or bool(self.configured)

    def as_dict(self) -> dict[str, Any]:
        return {
            "configured_secrets": self.configured,
            "unconfigured_secrets": self.unconfigured,
            "rows_scanned": self.rows_scanned,
            "rows_with_summary": self.rows_with_summary,
            "affected_run_ids": self.affected_run_ids[:100],
            "affected_count": len(self.affected_run_ids),
            "affected_by_secret": self.affected_by_secret,
            "redacted": self.redacted,
            "clean": self.clean,
            "conclusive": self.conclusive,
        }


def _forms(value: str) -> tuple[str, ...]:
    """Raw and percent-encoded. A credential carried in a query string
    appears encoded, so a plain substring check would miss it."""
    return (value, quote(value, safe=""))


def audit_error_summaries(session: Session, *, redact: bool = False) -> SecretAuditResult:
    """Scan persisted error summaries for any configured secret.

    Only secrets that are actually configured can be searched for — there
    is nothing to match against otherwise. A secret that was configured
    when the row was written but is not configured now will NOT be found,
    which is why `unconfigured` is reported rather than silently skipped.
    """
    result = SecretAuditResult()
    secrets: dict[str, tuple[str, ...]] = {}
    for name in SECRET_ENV_VARS:
        value = os.environ.get(name)
        if value:
            result.configured.append(name)
            secrets[name] = _forms(value)
        else:
            result.unconfigured.append(name)

    rows = list(session.scalars(select(ScheduledJobRun)))
    result.rows_scanned = len(rows)

    for row in rows:
        summary = row.error_summary
        if not summary:
            continue
        result.rows_with_summary += 1
        if not secrets:
            continue
        hit = False
        cleaned = summary
        for name, forms in secrets.items():
            for form in forms:
                if form in cleaned:
                    hit = True
                    result.affected_by_secret[name] = (
                        result.affected_by_secret.get(name, 0) + 1
                    )
                    cleaned = cleaned.replace(form, f"***{name}_REDACTED***")
        if hit:
            result.affected_run_ids.append(row.id)
            if redact:
                row.error_summary = cleaned
                result.redacted += 1

    if redact and result.redacted:
        session.commit()
    return result


def format_report(result: SecretAuditResult) -> str:
    # ASCII only: this prints to operator consoles, and a Windows terminal
    # on the default code page mangles anything else.
    lines = ["Secret exposure audit - scheduled_job_runs.error_summary", ""]
    lines.append(f"  rows scanned      : {result.rows_scanned}")
    lines.append(f"  with an error      : {result.rows_with_summary}")
    lines.append(f"  secrets configured : {', '.join(result.configured) or 'none'}")
    if result.unconfigured:
        lines.append(f"  NOT configured     : {', '.join(result.unconfigured)}")
        lines.append(
            "                       (cannot be searched for; a secret that has "
            "since been rotated or unset would not be detected)"
        )
    lines.append("")
    if result.rows_with_summary == 0:
        lines.append("  CLEAN: no row carries an error summary, so no row can contain")
        lines.append("  a secret. This holds regardless of what is configured here.")
    elif not result.configured:
        lines.append("  INCONCLUSIVE: rows carry error summaries but no secret is")
        lines.append("  configured here, so there was nothing to match against.")
        lines.append("  Re-run where the secret is set. A secret that leaked and was")
        lines.append("  since rotated would not be detected either way.")
    elif result.clean:
        lines.append("  CLEAN: no configured secret appears in any error summary.")
    else:
        lines.append(f"  EXPOSED: {len(result.affected_run_ids)} row(s) contain a secret.")
        for name, count in sorted(result.affected_by_secret.items()):
            lines.append(f"    - {name}: {count} row(s)")
        lines.append("")
        lines.append("  Rotate the credential. Redacting the rows removes it from this")
        lines.append("  database but not from any log, backup, or replica that already")
        lines.append("  copied it. Re-run with --redact to clean the rows.")
        if result.redacted:
            lines.append(f"  Redacted {result.redacted} row(s).")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check whether a configured secret was persisted to error summaries.",
    )
    parser.add_argument(
        "--redact", action="store_true",
        help="rewrite affected rows in place (rotate the credential regardless)",
    )
    args = parser.parse_args(argv)

    from fde_api.db.engine import get_session

    session = get_session()
    result = audit_error_summaries(session, redact=args.redact)
    print(format_report(result))
    # Distinct codes so this can gate a deploy without treating "we could
    # not tell" as a pass.
    if not result.conclusive:
        return 1
    return 0 if result.clean else 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
