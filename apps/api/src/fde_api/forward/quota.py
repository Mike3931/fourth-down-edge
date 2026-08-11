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
    # A balance is known but the plan it belongs to is not, so no threshold
    # can be applied to it. Distinct from CRITICAL because the remedy is
    # opposite: CRITICAL means stop spending, this means spend once, on
    # purpose, because the response is the only thing that ends the state.
    UNKNOWN_PLAN = "UNKNOWN_PLAN"


@dataclass(frozen=True)
class QuotaConfig:
    """Ceilings for the assumed plan, plus the fractions used to rescale
    them to whatever plan the account is ACTUALLY on.

    The absolute values below describe a 20,000-credit plan. Left as
    absolutes they are wrong in a way that fails silently on a smaller
    plan: an account with 500 credits classifies as CRITICAL at every
    remaining value from 1 to 1,500, so the scheduler drops to
    closing-captures-only on its first call and never comes back. The
    numbers all look deliberate, and nothing reports a misconfiguration -
    only a permanently unhappy quota state that reads like a real warning.

    So thresholds are fractions of the observed plan, capped by these
    absolutes. On a 20,000 plan nothing changes; on a 500 plan the reserve
    becomes proportionate to what 500 credits can actually buy.
    """

    monthly_plan_credits: int = 20_000
    daily_ceiling_credits: int = 1_000
    emergency_reserve_credits: int = 1_500
    warn_at_remaining: int = 5_000
    critical_at_remaining: int = 2_000
    credits_per_request: int = CREDITS_PER_REQUEST

    # Chosen to reproduce the absolutes above at a 20,000-credit plan:
    # 1500/20000 and 5000/20000. The defaults are therefore unchanged for
    # the plan they were written for.
    reserve_fraction: float = 0.075
    warn_fraction: float = 0.25
    # A month of daily ceilings should not exceed the month's credits.
    days_per_window: int = 30

    # What may be spent per day while the plan size is still unknown.
    #
    # The plan is learned from `x-requests-remaining` + `x-requests-used` on
    # a provider response, and a response only exists if a poll was
    # authorised — so refusing to poll on assumed thresholds is a deadlock.
    # Twenty requests is enough to learn the plan many times over and is
    # below any plausible month's budget, so a probe that never resolves
    # cannot quietly drain an account.
    unknown_plan_probe_credits: int = 60


def thresholds_for(plan_credits: int | None, cfg: QuotaConfig) -> tuple[int, int]:
    """(reserve, warn) scaled to the plan in force.

    An unknown plan keeps the configured absolutes: guessing small would
    throttle a large plan for no reason, and guessing large is what this
    function exists to stop.
    """
    if not plan_credits or plan_credits <= 0:
        return cfg.emergency_reserve_credits, cfg.warn_at_remaining
    # Never smaller than a handful of requests: a reserve that cannot pay
    # for one closing capture is not a reserve.
    floor = cfg.credits_per_request * 5
    reserve = min(cfg.emergency_reserve_credits,
                  max(floor, round(plan_credits * cfg.reserve_fraction)))
    warn = min(cfg.warn_at_remaining,
               max(reserve * 2, round(plan_credits * cfg.warn_fraction)))
    return reserve, warn


def daily_ceiling_for(plan_credits: int | None, cfg: QuotaConfig) -> int:
    """The daily ceiling, never exceeding an even share of the window.

    A 1,000/day ceiling against a 500-credit month is not a ceiling; it is
    the absence of one, expressed as a number.
    """
    if not plan_credits or plan_credits <= 0:
        return cfg.daily_ceiling_credits
    share = max(cfg.credits_per_request, plan_credits // max(1, cfg.days_per_window))
    return min(cfg.daily_ceiling_credits, share)


def observed_plan_credits(session: Session, provider: str = "the-odds-api") -> int | None:
    """The plan size the provider's own headers imply.

    `x-requests-used` + `x-requests-remaining` is the plan, and the
    provider reports both on every response. Taking the MAX across
    observations rather than the latest keeps a single truncated or
    mid-reset response from shrinking the apparent plan.

    Only the RECORDED limit counts. Reconstructing it as
    `calls_used + calls_remaining` was tempting and is wrong: `calls_used`
    is this call's cost, so the sum would read a 20,000-credit account with
    496 left as a 497-credit plan — a fabricated plan that looks precise.
    Rows written before the limit was captured report the plan as unknown,
    which is true, and which the first real response corrects.
    """
    rows = session.scalars(
        select(ProviderQuotaUsage).where(ProviderQuotaUsage.provider == provider)
    ).all()
    known = [r.quota_limit for r in rows if r.quota_limit]
    return max(known) if known else None


def plan_capacity(plan_credits: int | None, cfg: QuotaConfig | None = None) -> dict[str, Any]:
    """What a plan can actually buy, in requests rather than credits.

    Credits hide the cost: one poll of us/h2h+spreads+totals is three of
    them. Stating the budget in polls is the difference between "500
    credits" and "about five polls a day".
    """
    cfg = cfg or QuotaConfig()
    if not plan_credits or plan_credits <= 0:
        return {"plan_credits": None, "note": "plan unknown until the provider responds"}
    reserve, warn = thresholds_for(plan_credits, cfg)
    polls = plan_credits // cfg.credits_per_request
    return {
        "plan_credits": plan_credits,
        "credits_per_poll": cfg.credits_per_request,
        "polls_per_window": polls,
        "polls_per_day": round(polls / max(1, cfg.days_per_window), 1),
        "reserve_credits": reserve,
        "warn_at_remaining": warn,
        "daily_ceiling_credits": daily_ceiling_for(plan_credits, cfg),
    }


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


def month_to_date_used(session: Session, provider: str = "the-odds-api") -> int | None:
    """Credits the provider says have been spent this window.

    Derived rather than stored: `quota_limit - calls_remaining` is exactly
    the provider's own `x-requests-used`, and deriving it means there is no
    second copy to drift.
    """
    row = session.scalars(
        select(ProviderQuotaUsage)
        .where(ProviderQuotaUsage.provider == provider)
        .order_by(ProviderQuotaUsage.window_start.desc())
        .limit(1)
    ).first()
    if row is None or row.calls_remaining is None or not row.quota_limit:
        return None
    return row.quota_limit - row.calls_remaining


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


def classify(
    remaining: int | None, cfg: QuotaConfig, plan_credits: int | None = None
) -> str:
    """Classify the credit balance against the plan actually in force.

    An unknown plan reports UNKNOWN_PLAN rather than falling back to the
    absolutes. The fallback was not neutral: it measured every balance
    against a 20,000-credit plan, so a real 496 on a 500-credit account
    read CRITICAL - the exact case `QuotaConfig` above says the scaling
    exists to prevent, arrived at by a different route. And because the
    plan is only ever learned from a response, and `authorize_poll`
    refused the request that would carry it, the state could not correct
    itself except in the narrow windows where a poll is protected.

    EXHAUSTED still needs no plan: `remaining <= 0` says nothing is left
    whatever the plan was.
    """
    if remaining is None:
        return QuotaState.OK  # unknown until the provider tells us
    if remaining <= 0:
        return QuotaState.EXHAUSTED
    if plan_credits is None or plan_credits <= 0:
        return QuotaState.UNKNOWN_PLAN
    reserve, warn = thresholds_for(plan_credits, cfg)
    if remaining <= reserve:
        return QuotaState.CRITICAL
    if remaining <= warn:
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
    plan = observed_plan_credits(session, provider)
    state = classify(remaining, cfg, plan)
    daily_ceiling = daily_ceiling_for(plan, cfg)

    if state == QuotaState.EXHAUSTED:
        return QuotaDecision(False, state, "provider credits exhausted", 0.0, remaining, used_today)

    if is_closing_capture:
        return QuotaDecision(
            True, state, "closing capture is protected and cannot be recovered later",
            1.0, remaining, used_today,
        )

    if state == QuotaState.UNKNOWN_PLAN:
        # Spend, deliberately and in small amounts. The plan is carried on
        # the response headers, so the only way out of this state is
        # through a request - and refusing here on thresholds derived from
        # a plan nobody has measured is a deadlock, not a reserve.
        #
        # Its own budget rather than `daily_ceiling`, which is 1,000
        # credits when the plan is unknown and would not be a ceiling at
        # all on a small account.
        if used_today + cfg.credits_per_request > cfg.unknown_plan_probe_credits:
            return QuotaDecision(
                False, state,
                f"plan size unknown and the daily probe budget of "
                f"{cfg.unknown_plan_probe_credits} credits is spent ({used_today} used)",
                0.0, remaining, used_today,
            )
        return QuotaDecision(
            True, state,
            "plan size unknown; polling at reduced cadence so one response records it",
            4.0, remaining, used_today,
        )

    if used_today + cfg.credits_per_request > daily_ceiling:
        return QuotaDecision(
            False, state,
            f"daily ceiling {daily_ceiling} would be exceeded ({used_today} used)",
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

    cumulative = as_int("x-requests-used")
    remaining = as_int("x-requests-remaining")
    # The provider never states the plan, but it states both halves of it
    # on every response. Recording the sum is what lets the thresholds
    # scale to the real plan instead of an assumed one.
    limit = (cumulative + remaining) if (cumulative is not None and remaining is not None) else None
    row = ProviderQuotaUsage(
        provider=provider,
        window_start=now,
        # THIS call's requests, not the provider's month-to-date counter.
        # `credits_used_since` SUMS this column, and summing a cumulative
        # counter grows quadratically: after n polls it reports
        # 1+2+...+n requests instead of n, so the daily ceiling slams shut
        # after a handful of calls and blames a budget that was never
        # spent. The month-to-date figure is not lost - it is
        # `quota_limit - calls_remaining`, exactly.
        calls_used=calls,
        calls_remaining=remaining,
        quota_limit=limit,
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
