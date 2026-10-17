"""Reuse immutable SQLite fact payloads, never a cutoff decision or response."""
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from types import MappingProxyType
from typing import Any

from sqlalchemy import select

from fde_api.db.research_models import ResearchFact


@dataclass(frozen=True, slots=True)
class Fact:
    id: str
    source_id: str
    canonical_game_id: str | None
    team_id: str | None
    player_id: str | None
    week: int | None
    values: Any


_lock = Lock()
_cache: OrderedDict[tuple, tuple[Fact, ...]] = OrderedDict()


def _revision(s):
    url = s.get_bind().url
    if url.get_backend_name() != "sqlite" or not url.database or url.database == ":memory:":
        return None
    name = url.database.removeprefix("file:").split("?", 1)[0]
    path = Path(name).resolve()
    if not path.is_file():
        return None
    # A commit changes the main DB or WAL; rollback journal changes also refuse
    # reuse. Include file identity so replacing a DB at the same path is distinct.
    def stamp(p):
        try:
            st = p.stat()
            return st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns
        except FileNotFoundError:
            return None
    return str(path), tuple(stamp(Path(str(path) + suffix)) for suffix in ("", "-wal", "-journal"))


def read_facts(s, source_ids):
    revision = _revision(s)
    # Pending writes must remain visible to their own session. PostgreSQL and
    # in-memory tests read directly; no connection/session objects are cached.
    # An explicit SQLite transaction can have an older snapshot or uncommitted
    # Core/ORM writes even after flush cleared Session.new/dirty. Never reuse or
    # publish its rows under the on-disk committed revision.
    transaction = revision and s.connection().connection.driver_connection.in_transaction
    key = (revision, tuple(sorted(source_ids))) if revision and not (transaction or s.new or s.dirty or s.deleted) else None
    with _lock:
        if key is not None and key in _cache:
            _cache.move_to_end(key)
            return _cache[key]
    rows = s.execute(select(
        ResearchFact.id, ResearchFact.source_id, ResearchFact.canonical_game_id,
        ResearchFact.team_id, ResearchFact.player_id, ResearchFact.week, ResearchFact.values,
    ).where(ResearchFact.source_id.in_(source_ids)))
    facts = tuple(Fact(r[0], r[1], r[2], r[3], r[4], r[5], MappingProxyType(r[6])) for r in rows)
    if key and _revision(s) == revision:
        with _lock:
            _cache[key] = facts
            while len(_cache) > 4:
                _cache.popitem(last=False)
    return facts
