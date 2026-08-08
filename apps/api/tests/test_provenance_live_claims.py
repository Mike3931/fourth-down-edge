"""A live claim must be backed by a provider that could have supplied it.

The check asked "is THE SERVICE live?" of every record. The service's
provider mode describes one provider - the credential-gated one - so a
second, genuinely live source was reported as a governance breach for
telling the truth: real prices from a public endpoint, correctly labelled
LIVE, flagged CRITICAL.

The fix must not become a way to launder fixture data. The question is now
per-provider, and a provider absent from the registry cannot substantiate
LIVE at all, so admitting a new source is a deliberate act rather than a
consequence of the check not recognising it.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from fde_api.db.forward_models import OddsQuote
from fde_api.db.models import Base
from fde_api.forward.cohort import ProviderMode
from fde_api.forward.health import _can_be_live, run_health_checks
from fde_api.forward.modes import DataMode

NOW = datetime(2026, 9, 13, 12, 0, tzinfo=UTC)


@pytest.fixture()
def session() -> Session:
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, future=True)()


def _quote(session: Session, *, provider: str, mode: str, n: int = 1) -> None:
    for i in range(n):
        session.add(OddsQuote(
            data_mode="LIVE_RESEARCH", canonical_game_id="G", provider=provider,
            provider_mode=mode, sportsbook="draftkings", market="SPREAD",
            selection="HOME", line=-3.0, american=-110, decimal_odds=1.91,
            is_live=False, observed_at=NOW - timedelta(minutes=5),
            raw_hash=hashlib.sha256(f"{provider}{mode}{i}".encode()).hexdigest()))
    session.commit()


def _check(session: Session, mode: ProviderMode) -> dict:
    report = run_health_checks(
        session, now=NOW, data_mode=DataMode.LIVE_RESEARCH, provider_mode=mode)
    return next(c for c in report["checks"]
                if c["id"] == "provenance_live_claim_without_live_provider")


class TestTheRuleIsPerProvider:
    def test_a_public_source_can_claim_live_without_a_key(self, session) -> None:
        """espn needs no credential: it answered or it did not."""
        _quote(session, provider="espn", mode="LIVE", n=6)
        assert _check(session, ProviderMode.KEY_MISSING)["status"] == "OK"

    def test_the_keyed_provider_cannot_claim_live_without_the_key(
        self, session
    ) -> None:
        """The strictness that matters, unchanged."""
        _quote(session, provider="the-odds-api", mode="LIVE", n=3)
        check = _check(session, ProviderMode.KEY_MISSING)
        assert check["status"] != "OK"
        assert "the-odds-api" in check["explanation"]

    def test_the_keyed_provider_may_claim_live_when_the_service_is_live(
        self, session
    ) -> None:
        _quote(session, provider="the-odds-api", mode="LIVE", n=3)
        assert _check(session, ProviderMode.LIVE)["status"] == "OK"

    def test_an_unknown_provider_cannot_claim_live(self, session) -> None:
        """Fail-closed. The check exists to catch fixture data wearing a
        live label, so a source nobody registered gets no benefit of the
        doubt."""
        _quote(session, provider="some-new-source", mode="LIVE", n=2)
        check = _check(session, ProviderMode.LIVE)
        assert check["status"] != "OK"
        assert "some-new-source" in check["explanation"]

    def test_a_bad_claim_is_caught_even_beside_a_good_one(self, session) -> None:
        """One substantiated source must not vouch for another."""
        _quote(session, provider="espn", mode="LIVE", n=6)
        _quote(session, provider="the-odds-api", mode="LIVE", n=1)
        check = _check(session, ProviderMode.KEY_MISSING)
        assert check["status"] != "OK"
        assert check["detail"]["unsubstantiated"] == {"the-odds-api": 1}

    def test_fixture_rows_are_not_live_claims_at_all(self, session) -> None:
        _quote(session, provider="the-odds-api", mode="FIXTURE", n=4)
        assert _check(session, ProviderMode.KEY_MISSING)["status"] == "OK"


class TestTheRegistryIsExplicit:
    @pytest.mark.parametrize(
        ("provider", "mode", "expected"),
        [
            ("espn", ProviderMode.KEY_MISSING, True),
            ("espn", ProviderMode.LIVE, True),
            ("the-odds-api", ProviderMode.LIVE, True),
            ("the-odds-api", ProviderMode.FIXTURE, False),
            ("the-odds-api", ProviderMode.KEY_MISSING, False),
            ("unregistered", ProviderMode.LIVE, False),
            ("", ProviderMode.LIVE, False),
            (None, ProviderMode.LIVE, False),
        ],
    )
    def test_can_be_live(self, provider, mode, expected) -> None:
        assert _can_be_live(provider, mode) is expected
