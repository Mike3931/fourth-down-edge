"""Security regressions for the Starlette upgrade (0.48.0 -> 1.3.1).

Scoped to what this service actually exposes. The audit found:

  * no `StaticFiles` mount
  * no `FileResponse` anywhere
  * no `UploadFile`, `File(...)`, `Form(...)`, or any multipart route -
    `python-multipart` is not even an installed dependency
  * nothing reads `request.url`, `request.url.path`, or Host-derived
    values for authorization, routing, tenant selection, or redirects

So the Range-header and multipart advisories are structurally unreachable
here: there is no code path that serves a file or parses a form. Writing
tests that mount a `StaticFiles` app purely to exercise them would be
testing Starlette, not this service, and would create the very surface the
audit confirmed absent.

What IS testable is that a malformed Host header cannot influence the
routed path or anything the auth layer sees, and that upload-shaped
requests are rejected rather than absorbed.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from fde_api.api import main as api_main
from fde_api.db import engine as engine_mod
from fde_api.db.models import Base

TOKEN = "host-header-test-token"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from fde_api.config import settings

    monkeypatch.setattr(settings, "database_url", f"sqlite:///{(tmp_path / 'h.db').as_posix()}")
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "raw_dir", tmp_path / "raw")
    monkeypatch.setattr(settings, "artifacts_dir", tmp_path / "artifacts")
    monkeypatch.setattr(settings, "reports_dir", tmp_path / "reports")
    monkeypatch.setenv("FDE_API_TOKEN", TOKEN)
    engine_mod.get_engine.cache_clear()
    Base.metadata.create_all(engine_mod.get_engine())
    with TestClient(api_main.app) as c:
        yield c
    engine_mod.get_engine.cache_clear()


AUTH = {"Authorization": f"Bearer {TOKEN}"}


class TestMalformedHostCannotChangeRouting:
    @pytest.mark.parametrize("host", [
        "evil.example.com",
        "example.com/",
        "example.com/../v1/models",
        "example.com?x=1",
        "example.com#frag",
        "example.com/?#",
        "localhost:5173/evil",
        "",
        "a" * 300,
    ])
    def test_host_does_not_reroute(self, client, host) -> None:
        """Whatever the Host header says, /health must still be /health."""
        r = client.get("/health", headers={"Host": host})
        assert r.status_code == 200, (host, r.status_code)
        body = r.json()
        assert "research_banner" in body
        assert body["auth"] == "enabled"

    @pytest.mark.parametrize("host", [
        "evil.example.com",
        "example.com/../v1/models",
        "example.com?a=b",
        "example.com#x",
    ])
    def test_host_cannot_bypass_authentication(self, client, host) -> None:
        """The decisive one: a Host header must not reach a protected
        endpoint's authorization decision."""
        r = client.get("/v1/models", headers={"Host": host})
        assert r.status_code == 401, (host, r.status_code)

    def test_host_does_not_grant_access_with_a_bad_token(self, client) -> None:
        r = client.get(
            "/v1/models",
            headers={"Host": "example.com/../health", "Authorization": "Bearer wrong"},
        )
        assert r.status_code == 401

    def test_a_valid_token_still_works_under_a_hostile_host(self, client) -> None:
        r = client.get("/v1/models", headers={"Host": "evil.example.com/?#", **AUTH})
        assert r.status_code == 200

    def test_host_is_not_echoed_into_the_response(self, client) -> None:
        """A reflected Host is how a poisoned absolute URL escapes."""
        marker = "attacker-controlled-marker.example"
        r = client.get("/health", headers={"Host": marker})
        assert marker not in r.text


class TestNoMultipartSurface:
    def test_python_multipart_is_not_installed(self) -> None:
        """The multipart advisories require a form parser. Asserting its
        absence is stronger than testing limits on a parser we do not ship."""
        import importlib.util

        assert importlib.util.find_spec("multipart") is None
        assert importlib.util.find_spec("python_multipart") is None

    def test_no_route_declares_a_multipart_body(self) -> None:
        spec = api_main.app.openapi()
        for path, ops in spec.get("paths", {}).items():
            for method, op in ops.items():
                content = (op.get("requestBody") or {}).get("content", {})
                assert "multipart/form-data" not in content, (path, method)
                assert "application/x-www-form-urlencoded" not in content, (path, method)

    def test_an_upload_shaped_post_is_refused(self, client) -> None:
        """No endpoint accepts one, so this must not be quietly absorbed."""
        r = client.post(
            "/v1/features/build",
            files={"f": ("big.bin", b"x" * 1024, "application/octet-stream")},
            headers=AUTH,
        )
        assert r.status_code in (400, 415, 422), r.status_code


class TestNoFileServingSurface:
    def test_no_staticfiles_mount(self) -> None:
        from starlette.staticfiles import StaticFiles

        for route in api_main.app.routes:
            assert not isinstance(getattr(route, "app", None), StaticFiles)

    def test_no_route_returns_a_file_response(self) -> None:
        """FileResponse Range handling is the other advisory class. Nothing
        here serves files, so there is no Range surface to harden."""
        import inspect

        src = inspect.getsource(api_main)
        assert "FileResponse" not in src
        assert "StaticFiles" not in src


class TestCorsStillConstrained:
    def test_a_foreign_origin_is_not_granted(self, client) -> None:
        r = client.get("/health", headers={"Origin": "https://evil.example.com"})
        assert r.headers.get("access-control-allow-origin") != "https://evil.example.com"

    def test_credentials_are_never_allowed(self, client) -> None:
        r = client.get("/health", headers={"Origin": "http://localhost:5173"})
        assert r.headers.get("access-control-allow-credentials") != "true"

    def test_the_configured_origin_is_granted(self, client) -> None:
        r = client.get("/health", headers={"Origin": "http://localhost:5173"})
        assert r.headers.get("access-control-allow-origin") == "http://localhost:5173"


class TestOpenApiStillGenerates:
    def test_openapi_is_valid_after_the_upgrade(self, client) -> None:
        spec = client.get("/openapi.json").json()
        assert spec["openapi"].startswith("3.")
        assert "/health" in spec["paths"]
        assert "/v1/models" in spec["paths"]
