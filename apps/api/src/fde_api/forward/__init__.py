"""Phase 3 — forward data capture and live research mode.

Everything in this package concerns *prospective* capture: observations
recorded as they become known, predictions frozen at defined cutoffs,
and an evaluation cohort that is kept strictly separate from the
historical backtests.

The governing rule of the phase: a forward record may never be created,
revised, or evaluated using information that did not exist at the
moment it claims to represent.
"""

from fde_api.forward.modes import DataMode, mode_banner
from fde_api.forward.policy import ForwardTestPolicy, active_policy, freeze_policy

__all__ = [
    "DataMode",
    "ForwardTestPolicy",
    "active_policy",
    "freeze_policy",
    "mode_banner",
]
