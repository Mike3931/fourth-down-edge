"""Decision semantics, separated from the prose that explains them.

An evaluation carries three different things that were previously one:

  decision_reason_codes   WHY, as typed identifiers
  decision_context_hash   the stable inputs the decision was made from
  rendered_reason_text    the human explanation

Only the first two participate in semantic parity. The third is presentation
and may differ in wording without meaning the two paths disagreed.

That distinction is not cosmetic. It is the difference between "the two
execution paths reached different conclusions" and "the two databases
rendered the same conclusion with different words" - and the parity gate
was failing on the second while reporting the first.

Concretely: an evaluation's reason list embeds Data Health explanations, and
the health report legitimately differs between a database that holds
scheduler-run history and one that does not. Scheduler-process health is
OPERATIONAL health. It belongs in a health report, not in the semantics of
an analytical decision about a game - unless the failure actually
compromised that game's source data, which is DECISION-INPUT health and is
carried in the context hash.
"""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Any

# Bump when the code vocabulary or the context field set changes. A stored
# context hash is not interpretable without it.
DECISION_CODEC_VERSION = "decision-v2"


class ReasonCode(StrEnum):
    """Typed reasons an evaluation reached its state.

    Deliberately structural: each is derivable from the evaluation's own
    fields rather than parsed out of a sentence. Prose changes with wording;
    these change only when the decision changes.
    """

    EDGE_ABOVE_THRESHOLD = "EDGE_ABOVE_THRESHOLD"
    EDGE_IN_WATCH_BAND = "EDGE_IN_WATCH_BAND"
    EDGE_BELOW_THRESHOLD = "EDGE_BELOW_THRESHOLD"
    DATA_INCOMPLETE = "DATA_INCOMPLETE"
    HEALTH_GATE_CLEAR = "HEALTH_GATE_CLEAR"
    HEALTH_GATE_SUPPRESSED = "HEALTH_GATE_SUPPRESSED"
    PRICE_STALE = "PRICE_STALE"
    NO_MODEL_PROBABILITY = "NO_MODEL_PROBABILITY"
    COMPLETENESS_BELOW_MINIMUM = "COMPLETENESS_BELOW_MINIMUM"
    FILLED = "FILLED"
    NOT_FILLED = "NOT_FILLED"
    NOT_EXECUTED = "NOT_EXECUTED"
    PRICE_INVALID = "PRICE_INVALID"
    MISSING_MARKET = "MISSING_MARKET"
    MISSING_QUARTERBACK = "MISSING_QUARTERBACK"
    POLICY_REJECTED = "POLICY_REJECTED"


# The edge code each status implies. Documentation of the correspondence,
# not the derivation: `evaluate_candidate` emits the code where it decides,
# and this table is what an auditor checks that emission against.
STATUS_TO_EDGE_CODE: dict[str, ReasonCode] = {
    "RESEARCH_CANDIDATE": ReasonCode.EDGE_ABOVE_THRESHOLD,
    "WATCH": ReasonCode.EDGE_IN_WATCH_BAND,
    "PASS": ReasonCode.EDGE_BELOW_THRESHOLD,
    "DATA_INCOMPLETE": ReasonCode.DATA_INCOMPLETE,
}

def normalise_codes(codes: list[str] | tuple[str, ...] | None) -> list[str]:
    """Validate, deduplicate and order a set of emitted codes.

    Codes now arrive from the service that evaluated the condition. This
    used to reconstruct them by searching rendered sentences for phrases -
    a bridge that worked only while nobody reworded an explanation, and
    that made the decision depend on its own prose. Wording is
    presentation; a decision is not.

    Unknown codes raise rather than being dropped. A code the vocabulary
    does not contain is either a typo or a condition nobody registered, and
    silently ignoring it would let a real reason vanish from the semantics.
    """
    seen = list(codes or [])
    valid = {c.value for c in ReasonCode}
    unknown = sorted({c for c in seen if c not in valid})
    if unknown:
        raise UnknownReasonCode(
            f"unknown decision reason code(s): {unknown}; "
            f"add them to ReasonCode or fix the emitter"
        )
    # Sorted and deduplicated: a code is a fact about the decision, not a
    # count of how many times something mentioned it, and two paths that
    # emitted the same set in a different order must agree.
    return sorted(set(seen))


class UnknownReasonCode(ValueError):
    """A code outside the controlled vocabulary reached the semantics."""


def decision_context_hash(
    *,
    prediction_identity: str | None,
    price_identity: str | None,
    policy_version: str | None,
    model_version: str | None,
    cohort: str,
    cutoff: str,
    health_suppressed: bool,
    health_check_ids: list[str] | None = None,
    missing_data_ids: list[str] | None = None,
) -> str:
    """Hash the stable inputs a decision was made from.

    Everything here is a semantic identity or a version. Deliberately
    absent: remediation prose, scheduler run ids, database keys,
    last-checked timestamps, and any count of records belonging to other
    games. Those differ between environments without the decision differing,
    which is exactly the confusion this hash exists to end.

    `health_check_ids` carries the IDENTIFIERS of failing decision-input
    checks - not their explanations - so a genuine health difference that
    bears on this game still changes the hash.
    """
    payload: dict[str, Any] = {
        "codec": DECISION_CODEC_VERSION,
        "prediction_identity": prediction_identity,
        "price_identity": price_identity,
        "policy_version": policy_version,
        "model_version": model_version,
        "cohort": cohort,
        "cutoff": cutoff,
        "health_suppressed": health_suppressed,
        "health_check_ids": sorted(health_check_ids or []),
        "missing_data_ids": sorted(missing_data_ids or []),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def rendered_reason_text(reasons: list[str] | None) -> str:
    """The human explanation, retained and never hashed for parity."""
    return " | ".join(reasons or [])
