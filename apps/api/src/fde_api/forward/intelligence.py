"""Prospective, immutable research vintages. No evaluation/model writes.

Mutable public exports become knowable at first capture, never at their season
or game date. Artifact publication time is NOT an injury report issue time.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import uuid
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from fde_api.canonical.team_map import canonical_team_code
from fde_api.config import settings
from fde_api.db.forward_models import GameIdentityBinding, ScheduleObservation
from fde_api.db.models import Player, ProviderEntityMap
from fde_api.db.research_models import (
    PlayerBinding,
    PlayerIdentityReview,
    ResearchFact,
    ResearchPoll,
    ResearchSource,
)
from fde_api.ingest import _mirror
from fde_api.pit.guards import assert_no_lookahead
from fde_api.raw import RawArtifactStore

VERSION = "nfl-intelligence-v7"
BASE = "https://github.com/nflverse/nflverse-data/releases/download/"
INTERVALS = {"schedule": 900, "players": 86400, "rosters": 21600,
             "team_stats": 21600, "player_stats": 21600, "snap_counts": 21600, "injuries": 21600}
METRICS = ("attempts", "completions", "passing_yards", "passing_tds", "passing_epa",
           "carries", "rushing_yards", "rushing_tds", "rushing_epa", "receptions",
           "targets", "receiving_yards", "receiving_tds", "target_share", "air_yards_share")


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     default=str).encode()).hexdigest()


def source_url(dataset: str, season: int) -> str:
    paths = {"players": "players/players.csv", "rosters": f"rosters/roster_{season}.csv",
             "team_stats": f"stats_team/stats_team_week_{season}.csv",
             "player_stats": f"stats_player/stats_player_week_{season}.csv",
             "injuries": f"injuries/injuries_{season}.csv",
             "snap_counts": f"snap_counts/snap_counts_{season}.csv"}
    if dataset == "schedule":
        return "https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv"
    return BASE + paths[dataset]


def review(s: Session, player: str | None, reason: str, evidence: dict[str, Any], now: datetime) -> None:
    key = digest([player, reason, evidence])
    if s.get(PlayerIdentityReview, key) is None:
        s.add(PlayerIdentityReview(id=key, player_id=player, reason=reason,
                                  evidence=evidence, observed_at=now))


def bind_players(s: Session, rows: list[dict[str, str]], source: str, now: datetime) -> dict[str, int]:
    """Full payload collision audit BEFORE accepting either side of a duplicate ID."""
    claims: dict[tuple[str, str], set[str]] = defaultdict(set)
    for row in rows:
        gsis = row.get("gsis_id", "").strip()
        if not gsis:
            continue
        for provider, field in (("gsis", "gsis_id"), ("espn", "espn_id"), ("pfr", "pfr_id")):
            if row.get(field):
                claims[(provider, row[field])].add(gsis)
    existing = {(b.provider, b.provider_id): b.player_id for b in s.scalars(select(PlayerBinding))}
    for legacy in s.scalars(select(ProviderEntityMap).where(ProviderEntityMap.entity_type == "player")):
        key = (legacy.provider, legacy.provider_id)
        claims[key].add(legacy.canonical_id)
    bad: set[str] = set()
    for key, ids in claims.items():
        if key in existing:
            ids.add(existing[key])
        if len(ids) > 1:
            bad.update(ids)
            for player in ids:
                review(s, player, "PROVIDER_ID_COLLISION", {"provider": key[0],
                    "provider_id": key[1], "claims": sorted(ids), "source_id": source}, now)
    players = {p.id: p for p in s.scalars(select(Player))}
    # Audit every row before publishing any identity or binding. A later row
    # must not invalidate an identity already accepted from this same payload.
    birth_dates: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        gsis = row.get("gsis_id", "").strip()
        if gsis and row.get("birth_date"):
            birth_dates[gsis].add(row["birth_date"])
    for gsis, dates in birth_dates.items():
        old = players.get(gsis)
        if old and old.birth_date:
            dates.add(old.birth_date)
        if len(dates) > 1:
            bad.add(gsis)
            review(s, gsis, "BIRTH_DATE_DISAGREEMENT", {"source_id": source,
                   "existing": old.birth_date if old else None,
                   "claims": sorted(dates)}, now)
    accepted = 0
    for row in rows:
        gsis = row.get("gsis_id", "").strip()
        if not gsis or len(gsis) > 16:
            continue
        old = players.get(gsis)
        if gsis in bad:
            continue
        if old is None:
            new = Player(id=gsis, name=row.get("display_name") or gsis,
                         position=row.get("position") or None, birth_date=row.get("birth_date") or None)
            s.add(new)
            players[gsis] = new
        for provider, field in (("gsis", "gsis_id"), ("espn", "espn_id"), ("pfr", "pfr_id")):
            pid = row.get(field)
            if pid and (provider, pid) not in existing:
                s.add(PlayerBinding(provider=provider, provider_id=pid, player_id=gsis,
                    observed_at=now, evidence={"source_id": source, "source_field": field,
                        "gsis_id": gsis, "birth_date": row.get("birth_date")}))
                existing[(provider, pid)] = gsis
        accepted += 1
    s.flush()
    return {"mapped_players": accepted, "conflicting_players": len(bad)}


def ingest_source(s: Session, *, dataset: str, season: int, payload: bytes,
                  captured_at: datetime, published_at: datetime | None,
                  store: RawArtifactStore | None = None) -> ResearchSource:
    if captured_at.tzinfo is None:
        raise ValueError("Aware capture time required")
    if published_at is not None:
        assert_no_lookahead(published_at, captured_at, "source publication")
    payload_hash = hashlib.sha256(payload).hexdigest()
    dependencies: str | None = None
    if dataset not in {"players", "schedule"}:
        # Retrying a previously refused payload after a mapping is established
        # creates a new normalization vintage, never backdates recovered facts.
        bindings = [(b.provider, b.provider_id, b.player_id) for b in s.scalars(select(PlayerBinding))]
        flags = [(r.id, r.player_id) for r in s.scalars(select(PlayerIdentityReview).where(
            PlayerIdentityReview.reason.in_(["PROVIDER_ID_COLLISION", "BIRTH_DATE_DISAGREEMENT"])))]
        alias_context = [(b.provider_game_id, b.canonical_game_id) for b in s.scalars(select(GameIdentityBinding).where(
            GameIdentityBinding.provider == "nflverse", GameIdentityBinding.data_mode == "LIVE_RESEARCH"))]
        dependencies = digest([sorted(bindings), sorted(flags), sorted(alias_context)])
    # Byte-identical recaptures retain original first-seen time and identity.
    key = digest([VERSION, dataset, season, payload_hash, dependencies])
    prior = s.get(ResearchSource, key)
    if prior:
        return prior
    rows = list(csv.DictReader(io.StringIO(payload.decode("utf-8-sig"))))
    required = {"schedule": {"game_id", "season", "home_team", "away_team"},
        "players": {"gsis_id", "espn_id", "birth_date"},
        "rosters": {"season", "team", "gsis_id", "espn_id", "position", "week"},
        "team_stats": {"season", "week", "team", "game_id", "passing_epa", "attempts"},
        "player_stats": {"season", "week", "team", "player_id", "game_id", "targets"},
        "injuries": {"season", "week", "team", "gsis_id", "report_status", "practice_status"},
        "snap_counts": {"season", "week", "team", "pfr_player_id", "game_id", "offense_pct", "offense_snaps"}}
    if not rows or not required[dataset].issubset(rows[0]):
        raise ValueError(f"{dataset} empty or changed schema")
    rows = [r for r in rows if dataset == "players" or int(r["season"]) == season]
    if not rows:
        raise ValueError("Requested season unavailable")
    weeks = sorted({int(r["week"]) for r in rows if r.get("week")})
    coverage: dict[str, Any] = {"source_rows": len(rows), "weeks": weeks,
        "max_week": max(weeks, default=None), "accepted_rows": 0, "refused_rows": 0,
        "publication_semantics": "Artifact release time, not per-player report issuance",
        "quality": "SECONDARY_PUBLIC", "complete_availability": False}
    coverage["identity_context_hash"] = dependencies
    coverage["normalization_version"] = VERSION
    batch = ResearchSource(id=key, dataset=dataset, season=season, provider="nflverse",
        payload_hash=payload_hash, source_url=source_url(dataset, season),
        published_at=published_at, captured_at=captured_at, coverage=coverage)
    pending_facts = []
    if store is not None:
        raw = store.write(provider="nflverse", dataset=f"research_{dataset}", params={"season": season},
            payload=payload, filename=f"{dataset}.csv", schema_version=VERSION,
            observed_at=captured_at.isoformat(),
            source_updated_at=published_at.isoformat() if published_at else None, row_count=len(rows))
        _mirror(s, raw)
    if dataset == "schedule":
        from fde_api.forward.schedule import ingest_schedule
        outcome = ingest_schedule(s, payload, season=season, observed_at=captured_at)
        coverage.update(outcome.as_dict())
        coverage.update(capture_schedule_results(s, rows, key, captured_at))
    elif dataset == "players":
        coverage.update(bind_players(s, rows, key, captured_at))
    else:
        valid = {b.player_id for b in s.scalars(select(PlayerBinding).where(PlayerBinding.provider == "gsis"))}
        blocked = {r.player_id for r in s.scalars(select(PlayerIdentityReview).where(
            PlayerIdentityReview.reason.in_(["PROVIDER_ID_COLLISION", "BIRTH_DATE_DISAGREEMENT"])))}
        aliases = {b.provider_game_id: b.canonical_game_id for b in s.scalars(select(GameIdentityBinding).where(
            GameIdentityBinding.provider == "nflverse", GameIdentityBinding.data_mode == "LIVE_RESEARCH"))}
        espn = {b.provider_id: b.player_id for b in s.scalars(select(PlayerBinding).where(PlayerBinding.provider == "espn"))}
        pfr = {b.provider_id: b.player_id for b in s.scalars(select(PlayerBinding).where(PlayerBinding.provider == "pfr"))}
        game_records: dict[str, ScheduleObservation] = {}
        for game in s.scalars(select(ScheduleObservation).where(
                ScheduleObservation.data_mode == "LIVE_RESEARCH", ScheduleObservation.season == season,
                ScheduleObservation.observed_at <= captured_at).order_by(
                    ScheduleObservation.observed_at, ScheduleObservation.id)):
            game_records[game.canonical_game_id] = game
        # Each natural row key must have exactly one meaning in this vintage.
        groups: dict[tuple[Any, ...], list[dict[str, str]]] = defaultdict(list)
        roster_teams: dict[tuple[str, str], set[str]] = defaultdict(set)
        for row in rows:
            groups[(row.get("team"), row.get("gsis_id") or row.get("player_id") or row.get("pfr_player_id"), row.get("week"),
                    row.get("season_type") or row.get("game_type"))].append(row)
            if dataset == "rosters" and row.get("gsis_id"):
                roster_teams[(row["gsis_id"], row["week"])].add(row["team"])
        seen: set[str] = set()
        for natural, variants in groups.items():
            distinct = {digest(r) for r in variants}
            if len(distinct) > 1:
                coverage["refused_rows"] += len(variants)
                review(s, natural[1], "CONTRADICTORY_SOURCE_ROWS", {"source_id": key,
                       "natural_key": list(natural), "row_hashes": sorted(distinct)}, captured_at)
                continue
            row = variants[0]
            player = row.get("gsis_id") or row.get("player_id") or pfr.get(row.get("pfr_player_id", ""))
            try:
                team = canonical_team_code(row["team"])
                if dataset != "team_stats" and not player:
                    raise ValueError("Missing canonical player identity")
                if player and (player not in valid or player in blocked):
                    raise ValueError("Unresolved player identity")
                if dataset == "rosters" and len(roster_teams[(str(player), row["week"])]) > 1:
                    raise ValueError("Ambiguous same-week roster team assignment")
                if dataset == "rosters" and row.get("espn_id") and espn.get(row["espn_id"]) != player:
                    raise ValueError("Roster provider identity disagreement")
                week = int(row["week"])
                if week < 1 or week > 22:
                    raise ValueError("Invalid week")
                gid = aliases.get(row.get("game_id", ""))
                if dataset in {"team_stats", "player_stats", "snap_counts"} and gid is None:
                    raise ValueError("Game identity unresolved")
                if dataset in {"team_stats", "player_stats", "snap_counts"}:
                    linked_game = game_records.get(str(gid))
                    if (linked_game is None or team not in {linked_game.home_team_id, linked_game.away_team_id}
                            or linked_game.week != week):
                        raise ValueError("Statistical game/team/week linkage disagreement")
                if dataset == "injuries":
                    candidates = {g.canonical_game_id for g in s.scalars(select(ScheduleObservation).where(
                        ScheduleObservation.data_mode == "LIVE_RESEARCH", ScheduleObservation.season == season,
                        ScheduleObservation.week == week, ScheduleObservation.season_type == "REG",
                        (ScheduleObservation.home_team_id == team) | (ScheduleObservation.away_team_id == team)))}
                    if len(candidates) != 1:
                        raise ValueError("Availability game identity ambiguous")
                    gid = next(iter(candidates))
                for metric in METRICS:
                    if row.get(metric) and number(row, metric) is None:
                        raise ValueError("Non-finite or malformed metric")
                if dataset == "snap_counts" and (number(row, "offense_pct") is None
                        or not 0 <= float(row["offense_pct"]) <= 1):
                    raise ValueError("Invalid snap share")
                fact_key = digest([key, natural])
                if fact_key not in seen:
                    pending_facts.append(ResearchFact(id=fact_key, source_id=key, team_id=team,
                        player_id=player, week=week, canonical_game_id=gid, values=row))
                    seen.add(fact_key)
                    coverage["accepted_rows"] += 1
            except (ValueError, KeyError) as exc:
                coverage["refused_rows"] += len(variants)
                review(s, player, "INVALID_RESEARCH_ROW", {"source_id": key,
                       "natural_key": list(natural), "row_hash": digest(row),
                       "refusal_reason": str(exc)}, captured_at)
    # Final coverage is inserted once, before immutable evidence triggers apply.
    s.add(batch)
    s.flush()
    s.add_all(pending_facts)
    s.flush()
    return batch


def capture_schedule_results(s: Session, rows: list[dict[str, str]], source: str,
                             now: datetime) -> dict[str, int]:
    """Published final scores enter through the existing governed observation path."""
    from fde_api.db.forward_models import GameIdentityReview
    from fde_api.forward.modes import DataMode
    from fde_api.forward.results import ResultObservation, ingest_result

    counts = {"results_recorded": 0, "results_unchanged": 0, "results_refused": 0}
    for row in rows:
        if not row.get("home_score") or not row.get("away_score"):
            continue
        binding = s.get(GameIdentityBinding, ("LIVE_RESEARCH", "nflverse", row["game_id"]))
        if binding is None:
            continue
        try:
            home, away = float(row["home_score"]), float(row["away_score"])
            if not home.is_integer() or not away.is_integer() or min(home, away) < 0:
                raise ValueError("Invalid final score")
            _, disposition = ingest_result(s, ResultObservation(binding.canonical_game_id,
                int(home), int(away), now, "nflverse"), data_mode=DataMode.LIVE_RESEARCH)
            if disposition == "refused":
                raise ValueError("Result refused")
            counts["results_unchanged" if disposition == "unchanged" else "results_recorded"] += 1
        except ValueError:
            evidence = {"canonical_game_id": binding.canonical_game_id, "source_id": source,
                        "source_game_id": row["game_id"], "home_score": row["home_score"],
                        "away_score": row["away_score"]}
            key = digest(evidence)
            if s.get(GameIdentityReview, key) is None:
                s.add(GameIdentityReview(id=key, data_mode="LIVE_RESEARCH", provider="nflverse",
                    provider_game_id=row["game_id"], reason="RESULT_CONFLICT_OR_INVALID",
                    evidence=evidence, created_at=now))
            counts["results_refused"] += 1
    s.flush()
    return counts


def collect_intelligence(s: Session, *, now: datetime, season: int,
                         client: httpx.Client | None = None,
                         clock: Callable[[], datetime] | None = None) -> dict[str, int]:
    """Source-specific persisted polling/backoff; independent failures never erase evidence."""
    counts = {"research_downloads": 0, "research_failures": 0, "research_not_due": 0}
    owns = client is None
    http = client or httpx.Client(follow_redirects=True, timeout=25)
    clock = clock or (lambda: datetime.now(UTC))
    store = RawArtifactStore(settings.raw_dir)
    try:
        for dataset, interval in INTERVALS.items():
            previous = s.scalars(select(ResearchPoll).where(ResearchPoll.dataset == dataset,
                ResearchPoll.season == season).order_by(ResearchPoll.captured_at.desc(), ResearchPoll.id.desc())).first()
            delay = min(interval, 1800) if previous and previous.status == "FAILED" else interval
            old_source = s.get(ResearchSource, previous.source_id) if previous and previous.source_id else None
            same_version = old_source is None or old_source.coverage.get("normalization_version") == VERSION
            if previous and same_version and now < previous.captured_at + timedelta(seconds=delay):
                counts["research_not_due"] += 1
                continue
            poll = ResearchPoll(id=str(uuid.uuid4()), dataset=dataset, season=season,
                                captured_at=now, status="FAILED")
            try:
                with s.begin_nested():
                    response = http.get(source_url(dataset, season))
                    response.raise_for_status()
                    received_at = max(now, clock())
                    header = response.headers.get("last-modified")
                    published = parsedate_to_datetime(header).astimezone(UTC) if header else None
                    batch = ingest_source(s, dataset=dataset, season=season, payload=response.content,
                        captured_at=received_at, published_at=published, store=store)
                    poll.source_id, poll.status = batch.id, "SUCCESS"
                    counts["research_downloads"] += 1
            except Exception as exc:
                # No payload/key/credential is rendered; failed attempt stays discoverable.
                poll.error = f"{type(exc).__name__}: source unavailable or rejected"
                counts["research_failures"] += 1
            poll.captured_at = max(now, clock())
            s.add(poll)
            s.flush()
    finally:
        if owns:
            http.close()
    return counts


def number(row: dict[str, Any], key: str) -> float | None:
    try:
        value = float(row.get(key, ""))
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


class IntelligenceAt:
    """One bounded-query view per briefing; cutoff gates source AND identity vintages."""
    def __init__(self, s: Session, now: datetime):
        if now.tzinfo is None:
            raise ValueError("Aware research cutoff required")
        self.now = now
        self.sources: dict[tuple[int, str], ResearchSource] = {}
        self.polls: dict[tuple[int, str], ResearchPoll] = {}
        for poll in s.scalars(select(ResearchPoll).where(ResearchPoll.captured_at <= now).order_by(
                ResearchPoll.captured_at, ResearchPoll.id)):
            self.polls[(poll.season, poll.dataset)] = poll
        for source in s.scalars(select(ResearchSource).where(ResearchSource.captured_at <= now).order_by(
                ResearchSource.captured_at, ResearchSource.id)):
            if source.published_at is None or source.published_at <= now:
                self.sources[(source.season, source.dataset)] = source
        # A source reverting to an earlier payload is a known operational state;
        # use the referenced evidence vintage rather than latest hash insertion.
        for key, poll in self.polls.items():
            if poll.status == "SUCCESS" and poll.source_id:
                src = s.get(ResearchSource, poll.source_id)
                if src and src.captured_at <= now and (src.published_at is None or src.published_at <= now):
                    self.sources[key] = src
        self.facts: dict[str, list[ResearchFact]] = defaultdict(list)
        ids = {src.id for src in self.sources.values()}
        for fact in s.scalars(select(ResearchFact).where(ResearchFact.source_id.in_(ids))):
            self.facts[fact.source_id].append(fact)
        self.blocked = {r.player_id for r in s.scalars(select(PlayerIdentityReview).where(
            PlayerIdentityReview.observed_at <= now,
            PlayerIdentityReview.reason.in_(["PROVIDER_ID_COLLISION", "BIRTH_DATE_DISAGREEMENT"])))}
        self.bound = set(s.scalars(select(PlayerBinding.player_id).where(
            PlayerBinding.provider == "gsis", PlayerBinding.observed_at <= now)))
        self.schedule: dict[str, ScheduleObservation] = {}
        from fde_api.db.forward_models import GameIdentityReview
        self.invalid_games = {r.evidence.get("canonical_game_id") for r in s.scalars(select(GameIdentityReview).where(
            GameIdentityReview.data_mode == "LIVE_RESEARCH", GameIdentityReview.created_at <= now,
            GameIdentityReview.reason == "RESULT_CONFLICT_OR_INVALID"))}
        for game in s.scalars(select(ScheduleObservation).where(
            ScheduleObservation.data_mode == "LIVE_RESEARCH", ScheduleObservation.observed_at <= now).order_by(
                ScheduleObservation.observed_at, ScheduleObservation.id)):
            self.schedule[game.canonical_game_id] = game
        from fde_api.db.forward_models import InjuryObservation
        self.official: dict[str, dict[str, InjuryObservation]] = defaultdict(dict)
        self.availability_conflicts: set[tuple[str, str]] = set()
        for injury in s.scalars(select(InjuryObservation).where(
            InjuryObservation.data_mode == "LIVE_RESEARCH", InjuryObservation.observed_at <= now,
            InjuryObservation.entered_at <= now).order_by(InjuryObservation.observed_at, InjuryObservation.id)):
            availability_key = (injury.canonical_game_id, injury.player_id)
            previous = self.official[injury.canonical_game_id].get(injury.player_id)
            if previous is not None and previous.observed_at == injury.observed_at:
                if (previous.practice_status, previous.game_designation, previous.verification_status) != (
                        injury.practice_status, injury.game_designation, injury.verification_status):
                    self.availability_conflicts.add(availability_key)
            else:
                self.availability_conflicts.discard(availability_key)
            self.official[injury.canonical_game_id][injury.player_id] = injury

    def game(self, game: ScheduleObservation) -> dict[str, Any]:
        missing = []
        if game.canonical_game_id in self.invalid_games:
            missing.append("Conflicting final-result inputs withheld pending review")
        sources = []
        season = game.season
        usable: dict[str, list[ResearchFact]] = {}
        for dataset in ("players", "rosters", "team_stats", "player_stats", "snap_counts", "injuries"):
            src = self.sources.get((season, dataset))
            poll = self.polls.get((season, dataset))
            state = "MISSING"
            if src:
                state = "OBSERVED"
                if src.published_at is None:
                    state = "PUBLICATION_UNKNOWN"
                elif self.now - src.published_at > timedelta(hours=48):
                    state = "STALE"
                if poll and poll.status != "SUCCESS":
                    state = "REFRESH_FAILED"
                sources.append({"dataset": dataset, "source_id": src.id,
                    "published_at": src.published_at.isoformat() if src.published_at else None,
                    "captured_at": src.captured_at.isoformat(), "status": state,
                    "source_url": src.source_url, "coverage": src.coverage})
            if state != "OBSERVED":
                missing.append(f"{dataset.replace('_', ' ')}: {state.lower().replace('_', ' ')}")
            # Old facts may be displayed as historical observations, explicitly labelled.
            usable[dataset] = self.facts[src.id] if src else []
        teams = []
        evidence_ids: list[str] = []
        for team in (game.away_team_id, game.home_team_id):
            roster = [r for r in usable["rosters"] if r.team_id == team and r.week is not None
                      and game.week is not None and r.week <= game.week]
            roster_week = max((r.week or 0 for r in roster), default=0)
            roster = [r for r in roster if r.week == roster_week and r.player_id in self.bound
                      and r.player_id not in self.blocked]
            roster_ids = {r.player_id for r in roster}
            weeks = self._past(usable["team_stats"], team, game)
            weeks.sort(key=lambda r: (r.week or 0, r.id), reverse=True)
            weeks = weeks[:3]
            stats = self._past(usable["player_stats"], team, game)
            latest: dict[str, ResearchFact] = {}
            for row in sorted(stats, key=lambda r: (r.week or 0, r.id)):
                if row.player_id in roster_ids and row.player_id not in self.blocked:
                    latest[str(row.player_id)] = row
            players: list[dict[str, Any]] = []
            snaps = {(r.player_id, r.canonical_game_id): r for r in self._past(usable["snap_counts"], team, game)}
            roster_by_id = {r.player_id: r for r in roster}
            for pid, row in latest.items():
                r = roster_by_id[pid]
                # A position contradiction is visible and withheld, never silently remapped.
                pos = r.values.get("position")
                if pos != row.values.get("position"):
                    missing.append(f"{team} player position disagreement: {pid}")
                    continue
                production = {metric: number(row.values, metric) for metric in METRICS}
                volume = sum(production[k] or 0 for k in ("attempts", "carries", "targets"))
                if volume > 0:
                    players.append({"player_id": pid, "name": r.values.get("full_name") or pid,
                        "position": pos, "week": row.week, "metrics": production,
                        "source_row_id": row.id, "roster_row_id": r.id, "volume": volume,
                        "snap_share": number(snaps[(pid, row.canonical_game_id)].values, "offense_pct")
                            if (pid, row.canonical_game_id) in snaps else None,
                        "snap_row_id": snaps[(pid, row.canonical_game_id)].id
                            if (pid, row.canonical_game_id) in snaps else None})
            players.sort(key=lambda p: (-p["volume"], p["player_id"]))
            injuries = [r for r in usable["injuries"] if r.team_id == team and r.week == game.week
                        and r.player_id in roster_ids and r.player_id not in self.blocked]
            official = [r for r in self.official[game.canonical_game_id].values()
                        if r.team_id == team and r.player_id in roster_ids and r.player_id not in self.blocked
                        and r.verification_status == "VERIFIED"
                        and r.source_category in {"OFFICIAL_VERIFIED", "LICENSED_PROVIDER"}
                        and r.source_updated_at is not None
                        and timedelta(0) <= self.now - r.source_updated_at <= timedelta(hours=48)]
            official = [r for r in official if (game.canonical_game_id, r.player_id) not in self.availability_conflicts]
            if any(gid == game.canonical_game_id for gid, _ in self.availability_conflicts):
                missing.append("Contradictory equal-time availability reports require review")
            if not roster:
                missing.append(f"{team}: no usable roster at cutoff")
            if roster_week != game.week:
                missing.append(f"{team}: current game-week roster unavailable")
            if not weeks:
                missing.append(f"{team}: no linked completed-game team stats at cutoff")
            if not players:
                missing.append(f"{team}: no linked player production at cutoff")
            missing.append(f"{team}: complete official availability and inactive lists unverified")
            evidence_ids.extend(r.id for r in [*roster, *weeks, *stats, *injuries, *snaps.values()])
            teams.append({"team_id": team, "roster_count": len(roster), "roster_week": roster_week or None,
                "recent_games": [{"week": r.week, "canonical_game_id": r.canonical_game_id,
                    "metrics": {metric: number(r.values, metric) for metric in METRICS},
                    "source_row_id": r.id} for r in weeks],
                "players": players[:8], "player_count": len(players),
                "availability": [{"player_id": r.player_id,
                    "name": r.values.get("full_name") or r.player_id,
                    "practice": r.values.get("practice_status") or None,
                    "designation": r.values.get("report_status") or None,
                    "status": "SECONDARY_UNVERIFIED", "source_row_id": r.id} for r in injuries],
                "verified_availability": [{"player_id": r.player_id, "practice": r.practice_status,
                    "designation": r.game_designation, "source": r.source_reference,
                    "published_at": r.source_updated_at.isoformat() if r.source_updated_at else None,
                    "captured_at": r.observed_at.isoformat(), "source_row_id": r.id}
                    for r in official],
                "availability_complete": False})
        missing.append("Route participation and complete game-day participation coverage unavailable")
        content = {"version": VERSION, "canonical_game_id": game.canonical_game_id,
            "cutoff": self.now.isoformat(), "sources": sources, "teams": teams,
            "missing": sorted(set(missing)), "identity_review_count": len(self.blocked),
            "evidence_ids": sorted(set(evidence_ids)), "model_features": "NOT_PROMOTED",
            "readiness": "RESEARCH_ONLY_INCOMPLETE"}
        content["manifest_hash"] = digest(content)
        content["evidence_hash"] = digest([content["evidence_ids"], [src["source_id"] for src in sources]])
        return content

    def _past(self, rows: list[ResearchFact], team: str, game: ScheduleObservation) -> list[ResearchFact]:
        result = []
        for r in rows:
            previous = self.schedule.get(r.canonical_game_id or "")
            if (r.team_id == team and r.week is not None and game.week is not None
                    and r.week < game.week and r.values.get("season_type", r.values.get("game_type", "REG")) == "REG"
                    and previous is not None and previous.game_status == "FINAL"
                    and team in {previous.home_team_id, previous.away_team_id} and previous.week == r.week
                    and r.canonical_game_id not in self.invalid_games
                    and previous.kickoff_utc is not None and previous.kickoff_utc < self.now
                    and (r.player_id is None or r.player_id in self.bound)):
                result.append(r)
        return result
