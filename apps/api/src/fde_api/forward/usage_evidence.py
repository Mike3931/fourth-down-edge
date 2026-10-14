"""Descriptive usage from already selected point-in-time research facts.

Callers must select eligible source vintages and completed games first. This
module performs no reads or writes and cannot turn usage into a probability.
"""
from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

from fde_api.db.research_models import ResearchFact

VERSION = "observed-usage-v1"


def _count(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) and result >= 0 and result.is_integer() else None


def usage_evidence(player: ResearchFact, team_rows: Sequence[ResearchFact]) -> dict[str, Any]:
    """One observed game's shares, never projected opportunity or route share.

    Join on canonical game, team AND week. Do not reconstruct a denominator
    from an incomplete player list, or choose arbitrarily among conflicting
    team rows. Missing and zero denominators remain unknown.
    """
    matches = [row for row in team_rows if player.canonical_game_id and player.team_id
               and player.week is not None and row.player_id is None
               and (row.canonical_game_id, row.team_id, row.week)
               == (player.canonical_game_id, player.team_id, player.week)]
    denominator = matches[0] if len(matches) == 1 else None
    missing = []
    if not player.player_id:
        denominator = None
        missing.append("PLAYER_ID_MISSING")
    if len(matches) != 1:
        missing.append("TEAM_GAME_DENOMINATOR_MISSING" if not matches else "TEAM_GAME_DENOMINATOR_AMBIGUOUS")
    shares = {}
    for field in ("attempts", "carries", "targets"):
        numerator = _count(player.values.get(field))
        total = _count(denominator.values.get(field)) if denominator is not None else None
        share = None
        if numerator is None:
            missing.append(f"{field.upper()}_COUNT_MISSING_OR_INVALID")
        elif total is None or total == 0:
            missing.append(f"{field.upper()}_DENOMINATOR_MISSING_OR_ZERO")
        elif numerator > total:
            missing.append(f"{field.upper()}_EXCEEDS_TEAM_TOTAL")
        else:
            share = numerator / total
        shares[field] = {"player_count": numerator, "team_count": total, "share": share}
    return {"version": VERSION, "status": "DESCRIPTIVE_ONLY",
            "canonical_game_id": player.canonical_game_id, "player_id": player.player_id,
            "team_id": player.team_id, "week": player.week, "sample_games": 1,
            "shares": shares, "source_row_ids": sorted({player.id, *[r.id for r in matches]}),
            "source_ids": sorted({player.source_id, *[r.source_id for r in matches]}),
            "route_participation": None, "expected_starter": None,
            "missing": sorted(set(missing)), "projected_opportunity": None}
