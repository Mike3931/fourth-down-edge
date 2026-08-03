"""The audit that answers "did the credential actually leak here?"

The redaction fix is forward-looking: rows written before it keep whatever
was written. This checks the tool that finds them, including the case that
matters most — an environment where the secret is no longer configured,
where the honest answer is "inconclusive" rather than "clean".
"""

from __future__ import annotations

from datetime import UTC, datetime
from urllib.parse import quote

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from fde_api.db.forward_models import ScheduledJobRun
from fde_api.db.models import Base
from fde_api.forward.secret_audit import audit_error_summaries, format_report

KEY = "live-odds-key-9c1f"
NOW = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)


@pytest.fixture()
def session():
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine, future=True)()
    yield s
    s.close()


def _run(s: Session, run_id: str, summary: str | None) -> None:
    s.add(ScheduledJobRun(
        id=run_id, job_kind="odds_capture", idempotency_key=run_id,
        data_mode="LIVE_RESEARCH", scheduled_for=NOW, started_at=NOW,
        status="failed", job_outcome="RETRYABLE_FAILURE", domain_state="DATA_INCOMPLETE",
        state_origin="LIVE", root_run_id=run_id, recovery_sequence=0,
        administrative_override=False, retry_count=0, provider_calls=0,
        records_received=0, records_written=0, code_commit="abc",
        created_at=NOW, error_summary=summary,
    ))
    s.commit()


class TestDetection:
    def test_a_clean_database_reports_clean(self, session, monkeypatch) -> None:
        monkeypatch.setenv("FDE_ODDS_API_KEY", KEY)
        _run(session, "run_ok", "provider returned HTTP 500")
        r = audit_error_summaries(session)
        assert r.clean
        assert r.rows_scanned == 1
        assert r.rows_with_summary == 1

    def test_a_leaked_key_is_found(self, session, monkeypatch) -> None:
        monkeypatch.setenv("FDE_ODDS_API_KEY", KEY)
        _run(session, "run_bad", f"HTTPStatusError: 401 for url .../odds?apiKey={KEY}&regions=us")
        r = audit_error_summaries(session)
        assert not r.clean
        assert r.affected_run_ids == ["run_bad"]
        assert r.affected_by_secret["FDE_ODDS_API_KEY"] == 1

    def test_the_percent_encoded_form_is_found(self, session, monkeypatch) -> None:
        awkward = "key/with+chars"
        monkeypatch.setenv("FDE_ODDS_API_KEY", awkward)
        _run(session, "run_enc", f"failed: .../odds?apiKey={quote(awkward, safe='')}")
        r = audit_error_summaries(session)
        assert not r.clean

    def test_rows_without_an_error_are_not_counted(self, session, monkeypatch) -> None:
        monkeypatch.setenv("FDE_ODDS_API_KEY", KEY)
        _run(session, "run_a", None)
        _run(session, "run_b", "")
        r = audit_error_summaries(session)
        assert r.rows_scanned == 2
        assert r.rows_with_summary == 0
        assert r.clean

    def test_the_api_token_is_checked_too(self, session, monkeypatch) -> None:
        monkeypatch.delenv("FDE_ODDS_API_KEY", raising=False)
        monkeypatch.setenv("FDE_API_TOKEN", "tok-xyz")
        _run(session, "run_tok", "rejected Bearer tok-xyz")
        r = audit_error_summaries(session)
        assert r.affected_by_secret["FDE_API_TOKEN"] == 1


class TestHonestyAboutWhatItCannotKnow:
    def test_no_configured_secret_is_inconclusive_not_clean(
        self, session, monkeypatch
    ) -> None:
        """The dangerous false negative: a key that leaked and was then
        rotated leaves rows this tool cannot match. Reporting "clean" there
        would be worse than reporting nothing."""
        monkeypatch.delenv("FDE_ODDS_API_KEY", raising=False)
        monkeypatch.delenv("FDE_API_TOKEN", raising=False)
        _run(session, "run_x", "HTTPStatusError: 401 for url .../odds?apiKey=whatever")
        r = audit_error_summaries(session)
        assert r.configured == []
        assert set(r.unconfigured) == {"FDE_ODDS_API_KEY", "FDE_API_TOKEN"}
        assert r.conclusive is False
        assert "INCONCLUSIVE" in format_report(r)
        assert "CLEAN" not in format_report(r)

    def test_no_error_summaries_is_conclusively_clean(
        self, session, monkeypatch
    ) -> None:
        """If no row carries an error summary, nothing can contain a secret
        — that holds whether or not a secret is configured, so it is a real
        answer rather than an inconclusive one. This is the actual state of
        the repository database."""
        monkeypatch.delenv("FDE_ODDS_API_KEY", raising=False)
        monkeypatch.delenv("FDE_API_TOKEN", raising=False)
        _run(session, "run_quiet", None)
        r = audit_error_summaries(session)
        assert r.conclusive is True
        assert r.clean is True
        report = format_report(r)
        assert "CLEAN" in report
        assert "INCONCLUSIVE" not in report

    def test_an_empty_database_is_conclusively_clean(self, session, monkeypatch) -> None:
        monkeypatch.delenv("FDE_ODDS_API_KEY", raising=False)
        monkeypatch.delenv("FDE_API_TOKEN", raising=False)
        r = audit_error_summaries(session)
        assert r.conclusive is True and r.clean is True

    def test_the_report_is_ascii_only(self, session, monkeypatch) -> None:
        """Operator consoles on the default Windows code page mangle
        anything else, and a garbled security report gets ignored."""
        monkeypatch.setenv("FDE_ODDS_API_KEY", KEY)
        _run(session, "run_bad", f"401 for url .../odds?apiKey={KEY}")
        format_report(audit_error_summaries(session)).encode("ascii")

    def test_unconfigured_secrets_are_named_in_the_report(
        self, session, monkeypatch
    ) -> None:
        monkeypatch.setenv("FDE_ODDS_API_KEY", KEY)
        monkeypatch.delenv("FDE_API_TOKEN", raising=False)
        r = audit_error_summaries(session)
        report = format_report(r)
        assert "NOT configured" in report
        assert "FDE_API_TOKEN" in report

    def test_the_report_never_prints_the_secret(self, session, monkeypatch) -> None:
        """A tool for finding leaked credentials must not print them."""
        monkeypatch.setenv("FDE_ODDS_API_KEY", KEY)
        _run(session, "run_bad", f"401 for url .../odds?apiKey={KEY}")
        report = format_report(audit_error_summaries(session))
        assert KEY not in report

    def test_the_result_dict_never_carries_the_secret(self, session, monkeypatch) -> None:
        monkeypatch.setenv("FDE_ODDS_API_KEY", KEY)
        _run(session, "run_bad", f"401 for url .../odds?apiKey={KEY}")
        assert KEY not in str(audit_error_summaries(session).as_dict())


class TestRedaction:
    def test_redact_rewrites_the_row(self, session, monkeypatch) -> None:
        monkeypatch.setenv("FDE_ODDS_API_KEY", KEY)
        _run(session, "run_bad", f"401 for url .../odds?apiKey={KEY}")
        r = audit_error_summaries(session, redact=True)
        assert r.redacted == 1
        row = session.get(ScheduledJobRun, "run_bad")
        assert row is not None
        assert KEY not in (row.error_summary or "")
        assert "REDACTED" in (row.error_summary or "")

    def test_reporting_alone_changes_nothing(self, session, monkeypatch) -> None:
        monkeypatch.setenv("FDE_ODDS_API_KEY", KEY)
        original = f"401 for url .../odds?apiKey={KEY}"
        _run(session, "run_bad", original)
        audit_error_summaries(session, redact=False)
        session.expire_all()
        row = session.get(ScheduledJobRun, "run_bad")
        assert row is not None
        assert row.error_summary == original

    def test_redaction_is_idempotent(self, session, monkeypatch) -> None:
        monkeypatch.setenv("FDE_ODDS_API_KEY", KEY)
        _run(session, "run_bad", f"401 for url .../odds?apiKey={KEY}")
        audit_error_summaries(session, redact=True)
        second = audit_error_summaries(session, redact=True)
        assert second.clean
        assert second.redacted == 0

    def test_the_report_still_says_to_rotate(self, session, monkeypatch) -> None:
        """Cleaning the rows does not undo the exposure — logs, backups and
        replicas may already have copied it."""
        monkeypatch.setenv("FDE_ODDS_API_KEY", KEY)
        _run(session, "run_bad", f"401 for url .../odds?apiKey={KEY}")
        report = format_report(audit_error_summaries(session, redact=True))
        assert "Rotate" in report
        assert "backup" in report


class TestExitCode:
    def test_clean_exits_zero_and_exposure_exits_nonzero(self) -> None:
        """The exit code is what makes this usable as a deploy gate."""
        from fde_api.forward.secret_audit import SecretAuditResult

        assert SecretAuditResult().clean is True
        assert SecretAuditResult(affected_run_ids=["x"]).clean is False
