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
DECISION_CODEC_VERSION = "decision-v1"


class ReasonCode(StrEnum):
    """Typed reasons an evaluation reached its state.

    Deliberately structural: each is derivable from the evaluation's own
    fields rather than parsed out of a sentence. Prose changes with wording;
    these change only when the decision changes.
    """

    EDGE_ABOVE_THRESHOLD = "EDGE_ABOVE_THRESHOLD"
    EDGE_WITHIN_WATCH_BAND = "EDGE_WITHIN_WATCH_BAND"
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


# Status -> the edge code it implies. One table rather than a chain of
# branches, so the mapping is the thing an auditor reads.
_STATUS_CODES: dict[str, ReasonCode] = {
    "RESEARCH_CANDIDATE": ReasonCode.EDGE_ABOVE_THRESHOLD,
    "WATCH": ReasonCode.EDGE_WITHIN_WATCH_BAND,
    "PASS": ReasonCode.EDGE_BELOW_THRESHOLD,
    "DATA_INCOMPLETE": ReasonCode.DATA_INCOMPLETE,
}

# Phrases the evaluation service emits that carry decision meaning. Matching
# on them is a bridge, not the design: the codes above are derived
# structurally wherever possible, and these cover the cases where the
# service records a condition only in prose. Each is a literal this codebase
# controls, not free text from elsewhere.
_PHRASE_CODES: tuple[tuple[str, ReasonCode], ...] = (
    ("suppressed by Data Health", ReasonCode.HEALTH_GATE_SUPPRESSED),
    ("staleness limit", ReasonCode.PRICE_STALE),
    ("no model probability", ReasonCode.NO_MODEL_PROBABILITY),
    ("below policy minimum", ReasonCode.COMPLETENESS_BELOW_MINIMUM),
)


def reason_codes(
    *,
    status: str,
    reasons: list[str] | None,
    filled: bool | None,
) -> list[str]:
    """The typed reasons for one evaluation, deterministically ordered.

    Sorted, so two paths that derived the same set in a different order
    still agree. Duplicates collapse: a code is a fact about the decision,
    not a count of how many sentences mentioned it.
    """
    codes: set[ReasonCode] = set()
    if status in _STATUS_CODES:
        codes.add(_STATUS_CODES[status])

    blob = " ".join(reasons or [])
    for phrase, code in _PHRASE_CODES:
        if phrase in blob:
            codes.add(code)
    if ReasonCode.HEALTH_GATE_SUPPRESSED not in codes:
        codes.add(ReasonCode.HEALTH_GATE_CLEAR)

    if status == "RESEARCH_CANDIDATE":
        codes.add(ReasonCode.FILLED if filled else ReasonCode.NOT_FILLED)
    else:
        # Only candidates are executed, so "not filled" would misdescribe a
        # PASS: nothing was attempted.
        codes.add(ReasonCode.NOT_EXECUTED)

    return sorted(c.value for c in codes)


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
