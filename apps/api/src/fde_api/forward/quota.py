"""Provider quota governance.

The Odds API charges 1 credit per (region x market) per request, so the
configured us/h2h+spreads+totals call costs 3 credits. The ideal research
cadence costs roughly 6,700 credits per month including reserves, which
exceeds several entry plans — so the scheduler must degrade deliberately
rather than run the budget to zero mid-week and lose the closing capture,
which is the single most valuable observation of the cycle.

Priority rule: when credits are constrained, games nearest kickoff keep
their cadence and distant games lose theirs. A stale price on a game six
days out costs nothing; a missing close destroys CLV for that game
permanently.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from fde_api.db.forward_models import ProviderQuotaUsage
from fde_api.util import utc_now

CREDITS_PER_REQUEST = 3  # 1 region x 3 markets


class QuotaState:
    OK = "OK"
    CONSTRAINED = "CONSTRAINED"  # below warning threshold: reduce cadence
    CRITICAL = "CRITICAL"  # reserve only: closing captures alone
    EXHAUSTED = "EXHAUSTED"  # stop polling


@dataclass(frozen=True)
class QuotaConfig:
    """All ceilings are configurable; none are inferred from the provider."""

    monthly_plan_credits: int = 20_000
    daily_ceiling_credits: int = 1_000
    emergency_reserve_credits: int = 1_500
    warn_at_remaining: int = 5_000
    critical_at_remaining: int = 2_000
    credits_per_request: int = CREDITS_PER_REQUEST


@dataclass
class QuotaDecision:
    allowed: bool
    state: str
    reason: str
    cadence_multiplier: float  # 1.0 = full cadence, 2.0 = half as often
    remaining: int | None
    used_today: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "state": self.state,
            "reason": self.reason,
            "cadence_multiplier": self.cadence_multiplier,
            "remaining": self.remaining,
            "used_today": self.used_today,
        }


def credits_used_since(session: Session, provider: str, since: datetime) -> int:
    rows = session.scalars(
        select(ProviderQuotaUsage).where(
            ProviderQuotaUsage.provider == provider,
            ProviderQuotaUsage.window_start >= since,
        )
    ).all()
    return sum(r.calls_used or 0 for r in rows)


def latest_remaining(session: Session, provider: str) -> int | None:
    row = session.scalars(
        select(ProviderQuotaUsage)
        .where(ProviderQuotaUsage.provider == provider)
        .order_by(ProviderQuotaUsage.window_start.desc())
        .limit(1)
    ).first()
    return row.calls_remaining if row else None


def classify(remaining: int | None, cfg: QuotaConfig) -> str:
    if remaining is None:
        return QuotaState.OK  # unknown until the provider tells us
    if remaining <= 0:
        return QuotaState.EXHAUSTED
    if remaining <= cfg.emergency_reserve_credits:
        return QuotaState.CRITICAL
    if remaining <= cfg.warn_at_remaining:
        return QuotaState.CONSTRAINED
    return QuotaState.OK


def authorize_poll(
    session: Session,
    *,
    provider: str = "the-odds-api",
    hours_to_kickoff: float | None,
    is_closing_capture: bool = False,
    cfg: QuotaConfig | None = None,
    now: datetime | None = None,
) -> QuotaDecision:
    """Decide whether one poll may proceed, and at what cadence.

    Closing captures are protected: they are the only observation that can
    never be recovered later, so they are permitted until the credits are
    genuinely gone.
    """
    cfg = cfg or QuotaConfig()
    now = now or utc_now()
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    used_today = credits_used_since(session, provider, day_start) * cfg.credits_per_request
    remaining = latest_remaining(session, provider)
    state = classify(remaining, cfg)

    if state == QuotaState.EXHAUSTED:
        return QuotaDecision(False, state, "provider credits exhausted", 0.0, remaining, used_today)

    if is_closing_capture:
        return QuotaDecision(
            True, state, "closing capture is protected and cannot be recovered later",
            1.0, remaining, used_today,
        )

    if used_today + cfg.credits_per_request > cfg.daily_ceiling_credits:
        return QuotaDecision(
            False, state,
            f"daily ceiling {cfg.daily_ceiling_credits} would be exceeded ({used_today} used)",
            0.0, remaining, used_today,
        )

    if state == QuotaState.CRITICAL:
        # Reserve is for closing captures only.
        if hours_to_kickoff is not None and hours_to_kickoff <= 2:
            return QuotaDecision(True, state, "critical quota: near-kickoff games only",
                                 2.0, remaining, used_today)
        return QuotaDecision(False, state, "critical quota: reserved for imminent kickoffs",
                             0.0, remaining, used_today)

    if state == QuotaState.CONSTRAINED:
        # Degrade distant games first; keep near-kickoff cadence intact.
        if hours_to_kickoff is None or hours_to_kickoff > 24:
            return QuotaDecision(True, state, "constrained quota: distant games polled less often",
                                 4.0, remaining, used_today)
        if hours_to_kickoff > 6:
            return QuotaDecision(True, state, "constrained quota: mild reduction",
                                 2.0, remaining, used_today)
        return QuotaDecision(True, state, "constrained quota: near-kickoff cadence preserved",
                             1.0, remaining, used_today)

    return QuotaDecision(True, state, "within quota", 1.0, remaining, used_today)


def should_poll_game(
    *,
    kickoff_utc: datetime | None,
    game_status: str,
    has_final_score: bool,
    now: datetime | None = None,
) -> tuple[bool, str]:
    """Never spend credits on a game that cannot produce a usable quote."""
    now = now or utc_now()
    if game_status in ("CANCELLED", "POSTPONED"):
        return False, f"game is {game_status}"
    if has_final_score:
        return False, "game already has a final score"
    if kickoff_utc is None:
        return False, "kickoff unresolved"
    if now > kickoff_utc + timedelta(minutes=5):
        return False, "kickoff has passed; in-play quotes are out of scope"
    return True, "eligible"


def record_usage(
    session: Session,
    *,
    provider: str,
    headers: dict[str, str] | None,
    calls: int = 1,
    now: datetime | None = None,
) -> ProviderQuotaUsage:
    """Persist provider quota headers verbatim.

    The Odds API reports x-requests-remaining / x-requests-used / and the
    per-request cost. Header names are read defensively because a provider
    schema change must surface as missing data, not a crash.
    """
    now = now or utc_now()
    headers = {k.lower(): v for k, v in (headers or {}).items()}

    def as_int(key: str) -> int | None:
        try:
            return int(float(headers[key]))
        except (KeyError, TypeError, ValueError):
            return None

    row = ProviderQuotaUsage(
        provider=provider,
        window_start=now,
        calls_used=as_int("x-requests-used") or calls,
        calls_remaining=as_int("x-requests-remaining"),
        quota_limit=None,
        last_response_at=now,
    )
    session.add(row)
    session.flush()
    return row


def forecast_from_cadence(
    tiers: list[tuple[str, float, int]], cfg: QuotaConfig | None = None
) -> dict[str, Any]:
    """Generate the credit forecast from a cadence table.

    Derived from the scheduler's own configuration rather than a
    hand-maintained constant, so the two cannot drift apart.
    `tiers` is (label, window_hours, interval_minutes).
    """
    cfg = cfg or QuotaConfig()
    c = cfg.credits_per_request
    rows = []
    total_requests = 0.0
    for label, hours, interval in tiers:
        reqs = hours * 60.0 / interval
        total_requests += reqs
        rows.append({
            "tier": label, "window_hours": hours, "interval_minutes": interval,
            "requests": round(reqs, 1), "credits": round(reqs * c, 1),
        })
    weekly = total_requests * c
    monthly = weekly * 4.5
    return {
        "credits_per_request": c,
        "cost_model": "1 credit per region x market; us + h2h/spreads/totals",
        "tiers": rows,
        "requests_per_slate_lifecycle": round(total_requests, 1),
        "week_1_credits": round(weekly),
        "regular_season_credits": round(weekly * 18),
        "monthly_base_credits": round(monthly),
        "retry_reserve_credits": round(monthly * 0.10),
        "outage_recovery_reserve_credits": round(monthly * 0.15),
        "monthly_requirement_credits": round(monthly * 1.25),
        "minimum_viable_plan_credits": 20_000,
        "configured": {
            "monthly_plan_credits": cfg.monthly_plan_credits,
            "daily_ceiling_credits": cfg.daily_ceiling_credits,
            "emergency_reserve_credits": cfg.emergency_reserve_credits,
            "warn_at_remaining": cfg.warn_at_remaining,
            "critical_at_remaining": cfg.critical_at_remaining,
        },
        "compromise": (
            "The ideal cadence costs roughly 6,700 credits/month including reserves. "
            "On a 20,000-credit plan it fits with headroom. On a smaller plan the "
            "scheduler sheds distant-game cadence first (4x, then 2x) and preserves "
            "near-kickoff polling, because a stale six-day-out price costs nothing "
            "while a missing close permanently destroys CLV for that game."
        ),
        "closing_capture_note": (
            "Closing captures receive the highest scheduling and quota priority, with "
            "reserved credits, but remain subject to provider availability, "
            "connectivity, rate limits, and remaining subscription credits."
        ),
    }


def scheduler_cadence_tiers() -> list[tuple[str, float, int]]:
    """The polling tiers the odds-capture job actually applies.

    Single source of truth shared by the scheduler and the forecast.
    """
    return [
        ("> 7 days", 21 * 24.0, 360),
        ("7d-24h", 144.0, 60),
        ("24h-6h", 18.0, 15),
        ("6h-90m", 4.5, 5),
        ("final 90m", 1.5, 2),
    ]


def budget_report(cfg: QuotaConfig | None = None) -> dict[str, Any]:
    """The documented budget, generated from the scheduler cadence."""
    return forecast_from_cadence(scheduler_cadence_tiers(), cfg)
