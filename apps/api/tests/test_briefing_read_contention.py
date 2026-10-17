"""The SQLite read guard releases on failure and never substitutes a cached cutoff."""
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Lock
from time import sleep

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from fde_api.forward import briefing


def test_sqlite_briefings_serialize_without_caching_or_holding_connections(monkeypatch):
    engine = create_engine("sqlite://")
    active = 0
    peak = 0
    counter = Lock()

    def work(s, *, now):
        nonlocal active, peak
        assert not s.in_transaction()
        with counter:
            active += 1
            peak = max(peak, active)
        sleep(.02)
        with counter:
            active -= 1
        return {"as_of_at": now.isoformat()}

    monkeypatch.setattr(briefing, "_read_briefing", work)
    times = [datetime.now(UTC) + timedelta(seconds=i) for i in range(12)]

    def call(now):
        with Session(engine) as s:
            return briefing.read_briefing(s, now=now)

    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(call, times))
    assert peak == 1
    assert [x["as_of_at"] for x in results] == [t.isoformat() for t in times]

    def fail(s, *, now):
        raise RuntimeError("interrupted read")

    monkeypatch.setattr(briefing, "_read_briefing", fail)
    with pytest.raises(RuntimeError):
        call(times[0])
    monkeypatch.setattr(briefing, "_read_briefing", work)
    assert call(times[0])["as_of_at"] == times[0].isoformat()


def test_async_gate_does_not_exhaust_shared_workers(monkeypatch):
    import asyncio
    from types import SimpleNamespace

    import anyio
    from starlette.concurrency import run_in_threadpool

    from fde_api.api.main import get_research_briefing

    def work(s, *, now):
        sleep(.01)
        return {"as_of_at": now.isoformat()}

    monkeypatch.setattr(briefing, "read_briefing", work)
    session = SimpleNamespace(get_bind=lambda: SimpleNamespace(dialect=SimpleNamespace(name="sqlite")))

    async def exercise():
        reads = [asyncio.create_task(get_research_briefing(session)) for _ in range(48)]
        await asyncio.sleep(.02)
        # More pending briefings than the default shared worker limit must not
        # block unrelated sync endpoints from obtaining a worker.
        assert await asyncio.wait_for(run_in_threadpool(lambda: "healthy"), timeout=1) == "healthy"
        results = await asyncio.gather(*reads)
        assert len(results) == 48
        assert len({r["as_of_at"] for r in results}) == 48

    anyio.run(exercise)
