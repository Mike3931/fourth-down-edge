"""The provider credential must not escape the adapter.

The Odds API takes its key as a QUERY PARAMETER, so the key is part of the
request URL. httpx puts that URL in the message of every HTTPStatusError
and RequestError it raises — and the scheduler persists handler exceptions
to `ScheduledJobRun.error_summary`.

So before this was fixed, one 401 from the provider would write the live
API key into the database, into the health report that reads
`error_summary`, and into every log line that echoed it. Nothing was
logging the key deliberately; the transport was doing it.

These tests assert the negative: whatever goes wrong, the key is not in
what comes out.
"""

from __future__ import annotations

import httpx
import pytest

from fde_api.forward.odds import (
    OddsProviderError,
    OddsProviderNotConfigured,
    TheOddsApiProvider,
)

KEY = "live-key-do-not-leak-8f3a2b"


def _provider(handler) -> TheOddsApiProvider:
    return TheOddsApiProvider(
        api_key=KEY, client=httpx.Client(transport=httpx.MockTransport(handler))
    )


class TestErrorsAreScrubbed:
    @pytest.mark.parametrize("status", [400, 401, 403, 404, 422, 429, 500, 502, 503])
    def test_no_http_error_carries_the_key(self, status: int) -> None:
        p = _provider(lambda request: httpx.Response(status, json={"message": "no"}))
        with pytest.raises(OddsProviderError) as excinfo:
            p.fetch_odds()
        assert KEY not in str(excinfo.value)

    def test_the_status_survives_redaction(self) -> None:
        """Scrubbing must not make the error useless — the operator still
        needs to know a 401 is a bad key and a 429 is quota."""
        p = _provider(lambda request: httpx.Response(401, json={"message": "no"}))
        with pytest.raises(OddsProviderError, match="401"):
            p.fetch_odds()

    def test_redaction_marker_is_visible(self) -> None:
        p = _provider(lambda request: httpx.Response(401, json={"message": "no"}))
        with pytest.raises(OddsProviderError) as excinfo:
            p.fetch_odds()
        assert "REDACTED" in str(excinfo.value)

    def test_transport_failures_are_scrubbed_too(self) -> None:
        """A connection error carries the URL just as a status error does."""
        def boom(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError(f"failed connecting to {request.url}", request=request)

        p = _provider(boom)
        with pytest.raises(OddsProviderError) as excinfo:
            p.fetch_odds()
        assert KEY not in str(excinfo.value)

    def test_timeouts_are_scrubbed_too(self) -> None:
        def slow(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout(f"timed out reading {request.url}", request=request)

        p = _provider(slow)
        with pytest.raises(OddsProviderError) as excinfo:
            p.fetch_odds()
        assert KEY not in str(excinfo.value)


class TestNothingRawEscapes:
    def test_httpx_exceptions_do_not_cross_the_boundary(self) -> None:
        """Only OddsProviderError leaves fetch_odds. A raw httpx error
        reaching a caller would be an unscrubbed message."""
        p = _provider(lambda request: httpx.Response(500))
        with pytest.raises(OddsProviderError):
            p.fetch_odds()

    def test_the_chained_cause_is_dropped(self) -> None:
        """`raise ... from None` matters here: a chained __cause__ would put
        the original unscrubbed httpx message into any traceback that gets
        formatted into a log or an error_summary."""
        p = _provider(lambda request: httpx.Response(401))
        try:
            p.fetch_odds()
        except OddsProviderError as e:
            assert e.__cause__ is None
            assert e.__suppress_context__ is True
        else:  # pragma: no cover
            pytest.fail("expected OddsProviderError")

    def test_a_full_traceback_contains_no_key(self) -> None:
        """The realistic leak path: something formats the traceback."""
        import traceback

        p = _provider(lambda request: httpx.Response(401))
        try:
            p.fetch_odds()
        except OddsProviderError as e:
            text = "".join(traceback.format_exception(type(e), e, e.__traceback__))
        assert KEY not in text, text


class TestMissingKeyIsStillDistinct:
    def test_absent_key_raises_the_configuration_error(self) -> None:
        """"No key" must never be mistaken for "no prices", and that error
        predates the request, so there is nothing to redact."""
        p = TheOddsApiProvider(api_key="", client=httpx.Client())
        with pytest.raises(OddsProviderNotConfigured):
            p.fetch_odds()

    def test_redaction_is_a_no_op_without_a_key(self) -> None:
        p = TheOddsApiProvider(api_key="", client=httpx.Client())
        assert p._redact("nothing to hide") == "nothing to hide"


class TestUrlEncodedFormsAreCaught:
    def test_a_key_needing_encoding_is_still_redacted(self) -> None:
        """A key containing URL-unsafe characters appears percent-encoded in
        the URL, so a naive substring replace would miss it."""
        awkward = "key/with+special=chars&here"
        p = TheOddsApiProvider(
            api_key=awkward,
            client=httpx.Client(transport=httpx.MockTransport(
                lambda request: httpx.Response(401)
            )),
        )
        with pytest.raises(OddsProviderError) as excinfo:
            p.fetch_odds()
        message = str(excinfo.value)
        assert awkward not in message
        # and the encoded form the URL actually carried
        from urllib.parse import quote

        assert quote(awkward, safe="") not in message


class TestPersistenceLayerScrubsToo:
    """Defense in depth. The adapter scrubs its own errors, but
    `error_summary` is built from arbitrary exception messages and written
    to the database, so redaction is also applied where it is persisted —
    covering handlers that do not exist yet."""

    def test_redact_secrets_removes_the_odds_key(self, monkeypatch) -> None:
        from fde_api.util import redact_secrets

        monkeypatch.setenv("FDE_ODDS_API_KEY", KEY)
        out = redact_secrets(f"boom at https://x/odds?apiKey={KEY}&regions=us")
        assert out is not None
        assert KEY not in out
        assert "FDE_ODDS_API_KEY_REDACTED" in out

    def test_redact_secrets_removes_the_api_token(self, monkeypatch) -> None:
        from fde_api.util import redact_secrets

        monkeypatch.setenv("FDE_API_TOKEN", "tok-abc-123")
        out = redact_secrets("Bearer tok-abc-123 rejected")
        assert out is not None
        assert "tok-abc-123" not in out

    def test_redact_secrets_handles_none_and_empty(self) -> None:
        from fde_api.util import redact_secrets

        assert redact_secrets(None) is None
        assert redact_secrets("") == ""

    def test_redact_secrets_is_a_no_op_when_unset(self, monkeypatch) -> None:
        from fde_api.util import redact_secrets

        monkeypatch.delenv("FDE_ODDS_API_KEY", raising=False)
        monkeypatch.delenv("FDE_API_TOKEN", raising=False)
        assert redact_secrets("nothing secret here") == "nothing secret here"

    def test_a_leaking_handler_cannot_persist_the_key(self, tmp_path, monkeypatch) -> None:
        """The realistic regression: some future handler raises an exception
        carrying the key. It must not reach the database."""
        from datetime import UTC, datetime

        from sqlalchemy import create_engine, select
        from sqlalchemy.orm import sessionmaker

        from fde_api.config import settings
        from fde_api.db.forward_models import ScheduledJobRun
        from fde_api.db.models import Base
        from fde_api.forward.cohort import Cohort, ProviderMode
        from fde_api.forward.scheduler import (
            FrozenClock,
            JobDefinition,
            RetryPolicy,
            Scheduler,
        )

        monkeypatch.setattr(settings, "data_dir", tmp_path)
        monkeypatch.setenv("FDE_ODDS_API_KEY", KEY)

        engine = create_engine("sqlite://", future=True)
        Base.metadata.create_all(engine)
        factory = sessionmaker(bind=engine, future=True)
        now = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)

        def leaky(ctx):
            raise RuntimeError(f"GET https://api.the-odds-api.com/v4/odds?apiKey={KEY} failed")

        s = Scheduler(factory, clock=FrozenClock(now), cohort=Cohort.BURN_IN,
                      provider_mode=ProviderMode.FIXTURE)
        s.register(JobDefinition(name="leaky", handler=leaky,
                                 retry=RetryPolicy(max_attempts=1)))
        s.run_job("leaky", slot=now)

        with factory() as sess:
            summaries = [x for x in sess.scalars(select(ScheduledJobRun.error_summary)) if x]
        assert summaries, "expected a recorded failure"
        for summary in summaries:
            assert KEY not in summary, summary
            assert "REDACTED" in summary
