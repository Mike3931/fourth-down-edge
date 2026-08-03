"""How the API decides whether it is allowed to answer.

Two defects lived here:

  * The token was compared with `!=`. String equality short-circuits on
    the first differing byte, so response timing leaked the token one
    character at a time.

  * An unset `FDE_API_TOKEN` meant "local dev, serve everything". That
    fails OPEN — a deployment whose environment failed to load would serve
    every endpoint unauthenticated and report itself healthy. The odds
    credential already had the opposite discipline (fail readiness checks
    when absent); the API token did not.

Serving without authentication is now something a deployment has to say
out loud.
"""

from __future__ import annotations

import inspect

import pytest
from fastapi.testclient import TestClient

from fde_api.api import main as api_main
from fde_api.db import engine as engine_mod
from fde_api.db.models import Base


@pytest.fixture()
def build_client(tmp_path, monkeypatch):
    """A client factory that leaves the auth environment to the caller."""
    from fde_api.config import settings

    monkeypatch.setattr(settings, "database_url", f"sqlite:///{(tmp_path / 'a.db').as_posix()}")
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "raw_dir", tmp_path / "raw")
    monkeypatch.setattr(settings, "artifacts_dir", tmp_path / "artifacts")
    monkeypatch.setattr(settings, "reports_dir", tmp_path / "reports")
    engine_mod.get_engine.cache_clear()
    Base.metadata.create_all(engine_mod.get_engine())

    def make() -> TestClient:
        return TestClient(api_main.app)

    yield make
    engine_mod.get_engine.cache_clear()


class TestUnsetTokenFailsClosed:
    def test_no_token_and_no_opt_out_is_refused(self, build_client, monkeypatch) -> None:
        monkeypatch.delenv("FDE_API_TOKEN", raising=False)
        monkeypatch.delenv("FDE_ALLOW_UNAUTHENTICATED", raising=False)
        with build_client() as c:
            r = c.get("/v1/models")
        assert r.status_code == 503, r.text
        assert "FDE_API_TOKEN" in r.json()["detail"]

    def test_the_refusal_explains_both_ways_out(self, build_client, monkeypatch) -> None:
        """An error that does not say how to fix it just gets worked around."""
        monkeypatch.delenv("FDE_API_TOKEN", raising=False)
        monkeypatch.delenv("FDE_ALLOW_UNAUTHENTICATED", raising=False)
        with build_client() as c:
            detail = c.get("/v1/models").json()["detail"]
        assert "FDE_API_TOKEN" in detail
        assert "FDE_ALLOW_UNAUTHENTICATED" in detail

    def test_explicit_opt_out_serves(self, build_client, monkeypatch) -> None:
        monkeypatch.delenv("FDE_API_TOKEN", raising=False)
        monkeypatch.setenv("FDE_ALLOW_UNAUTHENTICATED", "1")
        with build_client() as c:
            assert c.get("/v1/models").status_code == 200

    def test_a_truthy_looking_value_is_not_enough(self, build_client, monkeypatch) -> None:
        """Only the exact opt-out counts, so a stray value cannot open it."""
        monkeypatch.delenv("FDE_API_TOKEN", raising=False)
        monkeypatch.setenv("FDE_ALLOW_UNAUTHENTICATED", "true")
        with build_client() as c:
            assert c.get("/v1/models").status_code == 503

    def test_empty_token_does_not_count_as_configured(self, build_client, monkeypatch) -> None:
        monkeypatch.setenv("FDE_API_TOKEN", "")
        monkeypatch.delenv("FDE_ALLOW_UNAUTHENTICATED", raising=False)
        with build_client() as c:
            assert c.get("/v1/models").status_code == 503


class TestTokenEnforcement:
    def test_correct_token_is_accepted(self, build_client, monkeypatch) -> None:
        monkeypatch.setenv("FDE_API_TOKEN", "secret-token")
        with build_client() as c:
            r = c.get("/v1/models", headers={"Authorization": "Bearer secret-token"})
        assert r.status_code == 200

    @pytest.mark.parametrize("header", [
        None,
        "",
        "secret-token",              # missing the scheme
        "Bearer wrong-token",
        "Bearer secret-token ",      # trailing space
        "bearer secret-token",       # wrong case
        "Bearer secret-token-extra",
        "Basic c2VjcmV0",
    ])
    def test_anything_else_is_rejected(self, build_client, monkeypatch, header) -> None:
        monkeypatch.setenv("FDE_API_TOKEN", "secret-token")
        headers = {} if header is None else {"Authorization": header}
        with build_client() as c:
            r = c.get("/v1/models", headers=headers)
        assert r.status_code == 401, (header, r.status_code)

    def test_a_configured_token_beats_the_opt_out(self, build_client, monkeypatch) -> None:
        """The opt-out must not be a way to disable a token that IS set."""
        monkeypatch.setenv("FDE_API_TOKEN", "secret-token")
        monkeypatch.setenv("FDE_ALLOW_UNAUTHENTICATED", "1")
        with build_client() as c:
            assert c.get("/v1/models").status_code == 401


class TestComparisonIsConstantTime:
    def test_the_check_uses_compare_digest(self) -> None:
        """A timing-safe comparison is not observable from the outside, so
        this asserts the implementation rather than the behaviour. `!=`
        short-circuits on the first differing byte and leaks the token."""
        src = inspect.getsource(api_main._require_auth)
        assert "secrets.compare_digest" in src
        assert "authorization != " not in src


class TestHealthTellsTheTruth:
    def test_health_is_reachable_without_a_token(self, build_client, monkeypatch) -> None:
        """Health must answer even when misconfigured, or nothing can
        report that it IS misconfigured."""
        monkeypatch.delenv("FDE_API_TOKEN", raising=False)
        monkeypatch.delenv("FDE_ALLOW_UNAUTHENTICATED", raising=False)
        with build_client() as c:
            assert c.get("/health").status_code == 200

    def test_misconfiguration_is_named_not_disguised(self, build_client, monkeypatch) -> None:
        monkeypatch.delenv("FDE_API_TOKEN", raising=False)
        monkeypatch.delenv("FDE_ALLOW_UNAUTHENTICATED", raising=False)
        with build_client() as c:
            auth = c.get("/health").json()["auth"]
        assert auth.startswith("MISCONFIGURED")

    def test_deliberate_openness_reads_differently_from_misconfiguration(
        self, build_client, monkeypatch
    ) -> None:
        monkeypatch.delenv("FDE_API_TOKEN", raising=False)
        monkeypatch.setenv("FDE_ALLOW_UNAUTHENTICATED", "1")
        with build_client() as c:
            auth = c.get("/health").json()["auth"]
        assert auth == "disabled (explicitly allowed)"

    def test_enabled_when_a_token_is_set(self, build_client, monkeypatch) -> None:
        monkeypatch.setenv("FDE_API_TOKEN", "secret-token")
        with build_client() as c:
            assert c.get("/health").json()["auth"] == "enabled"

    def test_health_never_echoes_the_token(self, build_client, monkeypatch) -> None:
        monkeypatch.setenv("FDE_API_TOKEN", "super-secret-value")
        with build_client() as c:
            body = c.get("/health").text
        assert "super-secret-value" not in body
