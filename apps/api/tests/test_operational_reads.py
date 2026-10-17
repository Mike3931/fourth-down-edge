"""Read optimization must preserve revision, cutoff and refresh isolation."""
from datetime import timedelta

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from fde_api.db.research_models import ResearchFact, ResearchSource
from fde_api.forward.fact_reads import read_facts
from test_war_room import NOW, schedule


@pytest.mark.parametrize("wal", [False, True])
def test_fact_payload_reuse_invalidates_after_commits_and_is_database_specific(tmp_path, wal):
    engines = [create_engine(f"sqlite:///{tmp_path / name}") for name in ("one.db", "two.db")]
    for i, engine in enumerate(engines):
        with engine.begin() as c:
            if wal:
                c.execute(text("PRAGMA journal_mode=WAL"))
            ResearchSource.__table__.create(c)
            ResearchFact.__table__.create(c)
            c.execute(ResearchSource.__table__.insert().values(id="source", dataset="rosters", season=2026,
                provider="nflverse", payload_hash="a"*64, source_url="https://example.org/roster",
                captured_at=NOW, coverage={}))
            c.execute(ResearchFact.__table__.insert().values(id="one", source_id="source", values={"n":i}))
    with Session(engines[0]) as s:
        first = read_facts(s, {"source"})
        assert read_facts(s, {"source"}) is first
        with pytest.raises(TypeError):
            first[0].values["n"] = 100
        s.execute(ResearchFact.__table__.insert().values(id="uncommitted", source_id="source", values={"n":99}))
        assert len(read_facts(s, {"source"})) == 2
        s.rollback()
        assert len(read_facts(s, {"source"})) == 1
    with engines[0].begin() as c:
        c.execute(ResearchFact.__table__.insert().values(id="two", source_id="source", values={"n":2}))
    with Session(engines[0]) as s:
        assert len(read_facts(s, {"source"})) == 2
    with Session(engines[1]) as s:
        assert read_facts(s, {"source"})[0].values["n"] == 1
    assert len(first) == 1  # previously returned evidence never mutated
    for engine in engines:
        engine.dispose()


def test_window_is_applied_after_latest_revision_and_cutoff(session):
    from fde_api.forward.briefing import prepare_briefing

    schedule(session, "MOVED")
    schedule(session, "MOVED", kickoff=NOW + timedelta(days=30), observed=NOW)
    schedule(session, "FUTURE")
    schedule(session, "FUTURE", kickoff=NOW + timedelta(days=30), observed=NOW + timedelta(seconds=1))
    session.flush()
    before = prepare_briefing(session, now=NOW)
    assert {g["canonical_game_id"] for g in before["games"]} == {"FUTURE"}
    scoped = prepare_briefing(session, now=NOW, canonical_game_id="FUTURE")
    assert scoped["games"] == before["games"]
    assert scoped["decision"] == "NO_BET"


def test_selected_game_does_not_wait_for_slow_global_briefing(monkeypatch):
    import asyncio
    from threading import Event
    from types import SimpleNamespace

    import anyio

    from fde_api.api.main import get_research_briefing
    from fde_api.forward import briefing

    started, release = Event(), Event()

    def slow(s, *, now):
        started.set()
        assert release.wait(5)
        return {"kind": "global"}

    monkeypatch.setattr(briefing, "_read_briefing", slow)
    monkeypatch.setattr(briefing, "prepare_briefing", lambda s, **kw: {"kind": kw["canonical_game_id"]})
    session = SimpleNamespace(get_bind=lambda: SimpleNamespace(dialect=SimpleNamespace(name="sqlite")))

    async def exercise():
        global_read = asyncio.create_task(get_research_briefing(session))
        try:
            for _ in range(100):
                if started.is_set():
                    break
                await asyncio.sleep(.01)
            assert started.is_set()
            result = await asyncio.wait_for(get_research_briefing(session, "SELECTED"), 1)
            assert result["kind"] == "SELECTED"
            assert not global_read.done()
        finally:
            release.set()
            await global_read

    anyio.run(exercise)
