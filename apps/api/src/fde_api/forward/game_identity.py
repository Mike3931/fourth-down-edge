"""Conservative, append-only provider crosswalk. Never renumber existing games."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from fde_api.db.forward_models import GameIdentityBinding, GameIdentityReview, ScheduleObservation

FINAL_CARRIER_PREFIX = "identity-schedule-revision-v1; "


def resolve_game(s: Session, *, provider: str, provider_game_id: str, data_mode: str,
                 season: int, season_type: str, home: str, away: str, week: int,
                 kickoff: datetime | None, proposed_id: str, observed_at: datetime) -> str | None:
    """Stable source key first; exact corroboration for an unbound second provider.

    A unique same-week opponent pair with a disagreeing kickoff is NOT enough.
    Once bound, kickoff/week revisions retain the identity. Legacy duplicates
    and contradictory participant/season histories are never arbitrarily merged.
    """
    key = (data_mode, provider, provider_game_id)
    binding = s.get(GameIdentityBinding, key)
    rows = list(s.scalars(select(ScheduleObservation).where(
        ScheduleObservation.data_mode == data_mode,
        or_(and_(ScheduleObservation.season == season,
                 ScheduleObservation.season_type == season_type,
                 ScheduleObservation.home_team_id.in_([home, away]),
                 ScheduleObservation.away_team_id.in_([home, away])),
            and_(ScheduleObservation.provider == provider,
                 ScheduleObservation.provider_game_id == provider_game_id),
            ScheduleObservation.canonical_game_id.in_([
                proposed_id, binding.canonical_game_id if binding else proposed_id])),
    ).order_by(ScheduleObservation.observed_at, ScheduleObservation.id)))
    anchors = {r.canonical_game_id for r in rows
               if r.provider == provider and r.provider_game_id == provider_game_id}
    if binding:
        anchors.add(binding.canonical_game_id)
    evidence = {"season": season, "season_type": season_type, "home": home, "away": away,
                "week": week, "kickoff": kickoff.isoformat() if kickoff else None}

    def review(reason: str, candidates: set[str]) -> None:
        details = {**evidence, "candidates": sorted(candidates)}
        digest = hashlib.sha256(json.dumps([key, reason, details], sort_keys=True).encode()).hexdigest()
        if s.get(GameIdentityReview, digest) is None:
            s.add(GameIdentityReview(id=digest, data_mode=data_mode, provider=provider,
                  provider_game_id=provider_game_id, reason=reason, evidence=details,
                  created_at=observed_at))
            s.flush()

    if not provider_game_id or provider_game_id in {"None", "null"}:
        review("MISSING_PROVIDER_ID", set())
        return None
    if len(anchors) > 1:
        review("CONFLICTING_PROVIDER_BINDINGS", anchors)
        return None
    participants = (season, season_type, home, away)
    latest: dict[str, ScheduleObservation] = {}
    for row in rows:
        latest[row.canonical_game_id] = row
    matching = {r.canonical_game_id for r in latest.values()
                if (r.season, r.season_type, r.home_team_id, r.away_team_id) == participants
                and (r.week == week if season_type == "REG" else r.kickoff_utc == kickoff)}
    if anchors:
        canonical = next(iter(anchors))
        history = [r for r in rows if r.canonical_game_id == canonical]
        if any((r.season, r.season_type, r.home_team_id, r.away_team_id) != participants for r in history):
            review("PARTICIPANT_OR_SEASON_DISAGREEMENT", anchors)
            return None
        if matching - anchors:
            review("EXISTING_DUPLICATE_IDENTITIES", matching | anchors)
            return None
        if latest.get(canonical) and latest[canonical].game_status == "FINAL" and latest[canonical].kickoff_utc != kickoff:
            review("FINAL_KICKOFF_DISAGREEMENT", anchors)
            return None
    else:
        reversed_roles = {r.canonical_game_id for r in latest.values()
                          if (r.season, r.season_type, r.home_team_id, r.away_team_id)
                          == (season, season_type, away, home)
                          and (r.week == week or r.kickoff_utc == kickoff)}
        if reversed_roles:
            review("HOME_AWAY_DISAGREEMENT", reversed_roles | matching)
            return None
        if len(matching) > 1:
            review("AMBIGUOUS_MATCH", matching)
            return None
        if matching:
            canonical = next(iter(matching))
            if kickoff is None or latest[canonical].kickoff_utc != kickoff:
                review("UNBOUND_PROVIDER_KICKOFF_DISAGREEMENT", matching)
                return None
        else:
            # Same participants, different week/date may be a moved game. Never
            # create a second identity merely because the source changed both.
            possible = {r.canonical_game_id for r in latest.values()
                        if (r.season, r.season_type, r.home_team_id, r.away_team_id) == participants}
            if possible:
                review("UNBOUND_PROVIDER_WEEK_DISAGREEMENT", possible)
                return None
            canonical = proposed_id
            if canonical in latest:
                review("CANONICAL_ID_COLLISION", {canonical})
                return None
    if binding is None:
        history = list(s.scalars(select(ScheduleObservation).where(
            ScheduleObservation.data_mode == data_mode,
            ScheduleObservation.canonical_game_id == canonical)))
        if any((r.season, r.season_type, r.home_team_id, r.away_team_id) != participants for r in history):
            review("PARTICIPANT_OR_SEASON_DISAGREEMENT", {canonical})
            return None
        claim_id = hashlib.sha256(json.dumps(participants).encode()).hexdigest()
        claim = s.get(GameIdentityBinding, (data_mode, "_fixture_claim", claim_id))
        if claim and claim.canonical_game_id != canonical:
            review("CONFLICTING_FIXTURE_CLAIM", {claim.canonical_game_id, canonical})
            return None
        if claim is None:
            s.add(GameIdentityBinding(data_mode=data_mode, provider="_fixture_claim",
                  provider_game_id=claim_id, canonical_game_id=canonical,
                  evidence=evidence, created_at=observed_at))
        s.add(GameIdentityBinding(data_mode=data_mode, provider=provider,
              provider_game_id=provider_game_id, canonical_game_id=canonical,
              evidence=evidence, created_at=observed_at))
        s.flush()  # PK prevents concurrent inconsistent binding publication.
    return canonical
