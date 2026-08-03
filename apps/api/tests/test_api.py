"""API tests: OpenAPI contract, auth, validation, research-only status,
job lifecycle, failure states."""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

import fde_api.db.engine as engine_mod
from conftest import seed_synthetic_league
from fde_api import RESEARCH_BANNER
from fde_api.config import settings
from fde_api.db.models import Base


@pytest.fixture()
def client(tmp_path, monkeypatch):
    # Serving unauthenticated is now an explicit choice rather than what
    # happens when nobody sets a token, so the test harness has to make
    # that choice out loud like any other caller would.
    monkeypatch.delenv("FDE_API_TOKEN", raising=False)
    monkeypatch.setenv("FDE_ALLOW_UNAUTHENTICATED", "1")
    db_path = tmp_path / "api.db"
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "raw_dir", tmp_path / "raw")
    monkeypatch.setattr(settings, "artifacts_dir", tmp_path / "artifacts")
    monkeypatch.setattr(settings, "reports_dir", tmp_path / "reports")
    engine_mod.get_engine.cache_clear()
    engine = engine_mod.get_engine()
    Base.metadata.create_all(engine)
    s = engine_mod.get_session()
    seed_synthetic_league(s, seasons=range(2023, 2026))
    s.close()
    from fde_api.api.main import app

    with TestClient(app) as c:
        yield c
    engine_mod.get_engine.cache_clear()


def test_openapi_lists_required_endpoints(client: TestClient) -> None:
    spec = client.get("/openapi.json").json()
    paths = spec["paths"]
    for p in [
        "/health",
        "/v1/models",
        "/v1/models/{model_version}",
        "/v1/data/ingest/nflverse",
        "/v1/features/build",
        "/v1/backtests/run",
        "/v1/backtests/{run_id}",
        "/v1/backtests/{run_id}/metrics",
        "/v1/predictions/generate",
        "/v1/predictions/{prediction_id}",
        "/v1/games/{game_id}/predictions",
        "/v1/performance/calibration",
        "/v1/performance/model-comparison",
    ]:
        assert p in paths, p


def test_health_carries_research_banner(client: TestClient) -> None:
    body = client.get("/health").json()
    assert body["research_banner"] == RESEARCH_BANNER
    assert body["database"] == "ok"
    assert body["games"] > 0


def test_auth_enforced_when_token_configured(client: TestClient, monkeypatch) -> None:
    monkeypatch.setenv("FDE_API_TOKEN", "secret-token")
    assert client.get("/v1/models").status_code == 401
    assert client.get("/v1/models", headers={"Authorization": "Bearer wrong"}).status_code == 401
    ok = client.get("/v1/models", headers={"Authorization": "Bearer secret-token"})
    assert ok.status_code == 200


def test_validation_errors_are_422(client: TestClient) -> None:
    assert client.post("/v1/data/ingest/nflverse", json={"seasons": []}).status_code == 422
    assert client.post("/v1/backtests/run", json={"test_season": 1800}).status_code == 422
    assert client.post("/v1/features/build", json={"half_life_days": -5}).status_code == 422


def test_unknown_resources_are_404(client: TestClient) -> None:
    assert client.get("/v1/models/nope").status_code == 404
    assert client.get("/v1/backtests/nope").status_code == 404
    assert client.get("/v1/predictions/nope").status_code == 404
    assert client.get("/v1/games/nope/predictions").status_code == 404
    assert client.get("/v1/jobs/nope").status_code == 404


def _wait_job(client: TestClient, job_id: str, timeout: float = 180.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get(f"/v1/jobs/{job_id}").json()
        if body["status"] in ("finished", "failed"):
            return body
        time.sleep(0.5)
    raise TimeoutError(job_id)


def test_job_lifecycle_features_build(client: TestClient) -> None:
    r = client.post("/v1/features/build", json={"half_life_days": 200.0, "horizon": "PREGAME"})
    assert r.status_code == 202
    job = r.json()
    assert job["status"] in ("queued", "running")
    done = _wait_job(client, job["id"])
    assert done["status"] == "finished", done.get("error")
    assert done["result"]["games_with_features"] > 0
    assert done["result"]["feature_set"] == "nfl-core-v1"


def test_job_failure_state_is_recorded(client: TestClient) -> None:
    r = client.post("/v1/predictions/generate",
                    json={"game_id": "nope", "model_version_id": "nope", "horizon": "PREGAME"})
    assert r.status_code == 202
    done = _wait_job(client, r.json()["id"])
    assert done["status"] == "failed"
    assert "not registered" in done["error"]


def test_model_comparison_names_market_benchmark(client: TestClient) -> None:
    body = client.get("/v1/performance/model-comparison").json()
    assert body["market_benchmark_id"] == "market-benchmark-v1"
    assert body["research_banner"] == RESEARCH_BANNER
