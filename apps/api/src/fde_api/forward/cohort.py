"""Cohort and provider-mode separation.

Two independent axes, both immutable once written:

  * **Cohort** — which experiment a record belongs to. Burn-in exists to
    shake out payload, mapping, quota, scheduler, and UI defects; its
    prediction outcomes may never influence the official evaluation, and
    no threshold or model parameter may be changed because of what
    burn-in results looked like. Operational corrections are allowed.

  * **Provider mode** — where the data actually came from. Fixture output
    must never be stored or described as live-provider output, so the
    mode travels with every captured record and is displayed in the UI.

Aggregating across cohorts requires an explicit administrative call.
There is no implicit union anywhere.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from fde_api.forward.modes import DataMode


class Cohort(StrEnum):
    FIXTURE = "fixture"
    DEMO = "demo"
    BURN_IN = "burn_in"
    OFFICIAL_FORWARD_TEST = "official_forward_test"


class ProviderMode(StrEnum):
    """How a provider-sourced record was obtained."""

    FIXTURE = "FIXTURE"
    SANDBOX = "SANDBOX"
    LIVE = "LIVE"
    UNAVAILABLE = "UNAVAILABLE"  # configured, but the request failed
    KEY_MISSING = "KEY_MISSING"  # no credential configured
    QUOTA_EXHAUSTED = "QUOTA_EXHAUSTED"


# Cohorts whose records may enter live research evaluation at all.
EVALUABLE_COHORTS = frozenset({Cohort.BURN_IN, Cohort.OFFICIAL_FORWARD_TEST})

# Only this cohort backs official forward-test claims.
OFFICIAL_COHORT = Cohort.OFFICIAL_FORWARD_TEST

PROVIDER_MODE_LABELS: dict[ProviderMode, str] = {
    ProviderMode.FIXTURE: "Fixture data — deterministic test payloads, not live market data",
    ProviderMode.SANDBOX: "Sandbox provider — not live market data",
    ProviderMode.LIVE: "Live provider",
    ProviderMode.UNAVAILABLE: "Provider unavailable — no current quotes",
    ProviderMode.KEY_MISSING: "Provider key not configured — no market data",
    ProviderMode.QUOTA_EXHAUSTED: "Provider quota exhausted — no new quotes",
}


class CohortViolationError(RuntimeError):
    """Raised on an attempt to mix cohorts or reassign one."""


@dataclass(frozen=True)
class CohortPolicy:
    """Which cohort new captures are written into, and under what policy."""

    cohort: Cohort
    policy_version: str | None
    data_mode: DataMode

    def __post_init__(self) -> None:
        if self.cohort is Cohort.OFFICIAL_FORWARD_TEST and not self.policy_version:
            raise CohortViolationError(
                "official_forward_test records must reference a frozen policy version"
            )
        if self.cohort in (Cohort.FIXTURE, Cohort.DEMO) and self.data_mode is DataMode.LIVE_RESEARCH:
            raise CohortViolationError(
                f"cohort {self.cohort.value} may not be written as LIVE_RESEARCH data"
            )

    def as_dict(self) -> dict[str, Any]:
        return {
            "cohort": self.cohort.value,
            "policy_version": self.policy_version,
            "data_mode": self.data_mode.value,
        }


def assert_single_cohort(cohorts: Iterable[str | Cohort], context: str = "result set") -> Cohort:
    """Every dashboard and metric must resolve to exactly one cohort."""
    distinct = {Cohort(c) for c in cohorts}
    if not distinct:
        raise CohortViolationError(f"{context} has no records; cohort is undetermined")
    if len(distinct) > 1:
        raise CohortViolationError(
            f"{context} mixes cohorts {sorted(c.value for c in distinct)}; "
            "cross-cohort aggregation requires an explicit administrative call"
        )
    return distinct.pop()


def aggregate_across_cohorts_explicitly(
    cohorts: list[Cohort], *, authorized_by: str, reason: str
) -> dict[str, Any]:
    """The only sanctioned way to combine cohorts.

    Deliberately noisy: it demands an operator and a reason, and returns a
    record of the decision so a mixed figure can never be mistaken for a
    single-cohort result.
    """
    if not authorized_by or not reason:
        raise CohortViolationError(
            "cross-cohort aggregation requires both an authorizing operator and a reason"
        )
    return {
        "cohorts": sorted(c.value for c in cohorts),
        "authorized_by": authorized_by,
        "reason": reason,
        "warning": (
            "This figure combines separate experiments and must not be reported "
            "as a forward-test result."
        ),
    }


def may_influence_official_evaluation(cohort: Cohort) -> bool:
    return cohort is OFFICIAL_COHORT


def is_live_provider_data(mode: ProviderMode) -> bool:
    """Fixture and sandbox output is never live data."""
    return mode is ProviderMode.LIVE


def provider_mode_from_env(api_key: str | None, *, use_fixtures: bool) -> ProviderMode:
    if use_fixtures:
        return ProviderMode.FIXTURE
    if not api_key:
        return ProviderMode.KEY_MISSING
    return ProviderMode.LIVE
