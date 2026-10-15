"""Public official injury-page observations, never inferred healthy/inactive status.

The page has NFL player slugs, not GSIS IDs or per-report issue timestamps.
Unmapped rows remain evidence; they cannot establish an identity or verified status.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from fde_api.canonical.team_map import CANONICAL_TEAMS
from fde_api.config import settings
from fde_api.db.models import Player
from fde_api.db.research_models import (
    PlayerBinding,
    PlayerIdentityReview,
    ResearchFact,
    ResearchPoll,
    ResearchSource,
)
from fde_api.forward.intelligence import digest
from fde_api.raw import RawArtifactStore

URL = "https://www.nfl.com/injuries/"
DATASET = "official_injuries"
VERSION = "nfl-official-page-v1"
TEAM_LABELS = {name.split()[-1]: code for code, name in CANONICAL_TEAMS.items()}
TEAM_LABELS.update({"49ers": "SF", "Buccaneers": "TB", "Commanders": "WAS"})


class ReportParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.team: str | None = None
        self.rows: list[dict[str, Any]] = []
        self.cells: list[str] | None = None
        self.cell: str | None = None
        self.slug: str | None = None
        self.text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self.cells, self.slug = [], None
        if tag == "td" and self.cells is not None:
            self.cell = ""
        if tag == "a" and self.cell is not None:
            href = dict(attrs).get("href") or ""
            if re.fullmatch(r"/players/[a-z0-9-]+/", href):
                self.slug = href.strip("/").split("/")[-1]

    def handle_data(self, data: str) -> None:
        self.text.append(data)
        if self.cell is not None:
            self.cell += data
        elif data.strip() in TEAM_LABELS:
            self.team = TEAM_LABELS[data.strip()]

    def handle_endtag(self, tag: str) -> None:
        if tag == "td" and self.cells is not None and self.cell is not None:
            self.cells.append(" ".join(self.cell.split()))
            self.cell = None
        if tag == "tr" and self.cells is not None:
            if len(self.cells) == 5 and self.slug and self.team:
                self.rows.append(
                    dict(
                        zip(
                            ["name", "position", "injury", "practice", "designation"], self.cells, strict=True
                        )
                    )
                    | {"team": self.team, "nfl_player_slug": self.slug}
                )
            self.cells = None


def ingest_official(s: Session, payload: bytes, *, captured_at: datetime, season: int) -> ResearchSource:
    parser = ReportParser()
    parser.feed(payload.decode("utf-8"))
    text = " ".join(parser.text)
    year = re.search(r"(20\d\d) NFL Injury Report", text)
    week = re.search(r"Injuries\s*-\s*WEEK\s+(\d+)", text, re.I)
    if not year or int(year[1]) != season or not week or not parser.rows:
        raise ValueError("Official page season/week/layout unavailable; no completeness claim")
    blocked = set(
        s.scalars(
            select(PlayerIdentityReview.player_id).where(PlayerIdentityReview.observed_at <= captured_at)
        )
    )
    canonical_players = set(s.scalars(select(Player.id)))
    gsis = set(s.scalars(select(PlayerBinding.player_id).where(
        PlayerBinding.provider=="gsis", PlayerBinding.observed_at<=captured_at)))
    bindings = {
        r.provider_id: r.player_id
        for r in s.scalars(
            select(PlayerBinding).where(
                PlayerBinding.provider == "nfl", PlayerBinding.observed_at <= captured_at
            )
        )
        if r.player_id not in blocked and r.player_id in canonical_players and r.player_id in gsis
    }
    from fde_api.forward.prospective import schedule_at

    states, invalid = schedule_at(s, captured_at)
    context = sorted(
        (g.canonical_game_id, g.home_team_id, g.away_team_id, g.week)
        for g in states.values()
        if g.season == season and g.week == int(week[1]) and g.canonical_game_id not in invalid
    )
    key = digest([VERSION, payload.hex(), sorted(bindings.items()), context])
    existing = s.get(ResearchSource, key)
    if existing:
        return existing
    raw = RawArtifactStore(settings.raw_dir).write(
        provider="nfl",
        dataset=DATASET,
        params={"season": season},
        payload=payload,
        filename="injuries.html",
        schema_version=VERSION,
        observed_at=captured_at.isoformat(),
        row_count=len(parser.rows),
    )
    source = ResearchSource(
        id=key,
        dataset=DATASET,
        season=season,
        provider="nfl",
        payload_hash=raw.payload_sha256,
        source_url=URL,
        published_at=None,
        captured_at=captured_at,
        coverage={
            "version": VERSION,
            "week": int(week[1]),
            "rows": len(parser.rows),
            "source_rows": len(parser.rows),
            "accepted_rows": len(parser.rows),
            "refused_rows": 0,
            "max_week": int(week[1]),
            "teams": sorted({r["team"] for r in parser.rows}),
            "mapped_players": sum(r["nfl_player_slug"] in bindings for r in parser.rows),
            "complete_availability": False,
            "official_inactives": False,
            "publication_semantics": "Per-report issuance unknown; capture time is not publication time",
            "raw_manifest_version": raw.version_id,
        },
    )
    s.add(source)
    s.flush()
    for row in parser.rows:
        # Team/week linkage is explicit. Ambiguous fixtures are never chosen by proximity.
        matches = [
            g
            for g in states.values()
            if g.season == season
            and g.week == int(week[1])
            and row["team"] in {g.home_team_id, g.away_team_id}
            and g.canonical_game_id not in invalid
        ]
        game_id = matches[0].canonical_game_id if len(matches) == 1 else None
        s.add(
            ResearchFact(
                id=digest([key, row]),
                source_id=key,
                team_id=row["team"],
                player_id=bindings.get(row["nfl_player_slug"]),
                week=int(week[1]),
                canonical_game_id=game_id,
                values=row
                | {
                    "source_category": "OFFICIAL_PAGE_UNTIMED",
                    "issue_time_known": False,
                    "confirmed_inactive": False,
                    "identity_status": "MAPPED" if row["nfl_player_slug"] in bindings else "UNRESOLVED",
                },
            )
        )
    s.flush()
    return source


def collect_official(
    s: Session, *, season: int, now: datetime, client: httpx.Client | None = None
) -> dict[str, int]:
    previous = s.scalars(
        select(ResearchPoll)
        .where(ResearchPoll.dataset == DATASET, ResearchPoll.season == season)
        .order_by(ResearchPoll.captured_at.desc())
    ).first()
    if previous and now - previous.captured_at < timedelta(minutes=15):
        return {"official_not_due": 1}
    poll = ResearchPoll(
        id=str(uuid.uuid4()), dataset=DATASET, season=season, captured_at=now, status="FAILED"
    )
    owns = client is None
    http = client or httpx.Client(follow_redirects=True, timeout=25)
    try:
        response = http.get(URL)
        response.raise_for_status()
        poll.captured_at = max(now, datetime.now(UTC))
        with s.begin_nested():
            source = ingest_official(s, response.content, captured_at=poll.captured_at, season=season)
            poll.source_id, poll.status = source.id, "SUCCESS"
    except Exception as exc:
        poll.error = f"{type(exc).__name__}: official report unavailable; completeness unknown"
    finally:
        if owns:
            http.close()
    s.add(poll)
    s.flush()
    return {
        "official_downloads": int(poll.status == "SUCCESS"),
        "official_failures": int(poll.status != "SUCCESS"),
    }
