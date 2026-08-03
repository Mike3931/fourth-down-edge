"""Persistent, process-safe job scheduler.

Design constraints that shaped this:

  * **Idempotency before concurrency.** Every run derives a deterministic
    idempotency key from (job, cohort, logical slot). The key is UNIQUE in
    the database, so a duplicate attempt — from a retry, a second worker,
    or a restart mid-flight — loses the insert race and is skipped rather
    than duplicating captures, vintages, or settlements.

  * **Locking is the same insert.** Claiming a run and preventing
    concurrent duplicates are one atomic operation, so there is no window
    between "decided to run" and "recorded that I am running".

  * **Restart is reconciliation, not replay.** On startup, runs left in
    `running` by a killed process are marked `interrupted`; whether they
    re-execute is decided by each job's catch-up policy, not by blindly
    re-running everything.

  * **The clock is injected.** Every scheduling decision reads from a
    Clock object, so cadence, missed-run detection, and backoff are
    deterministically testable without sleeping.
"""

from __future__ import annotations

import logging
import random
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from fde_api.db.forward_models import ScheduledJobRun
from fde_api.forward.cohort import Cohort, ProviderMode
from fde_api.forward.modes import DataMode
from fde_api.forward.state import DomainState, Outcome, StateOrigin, apply_state
from fde_api.util import current_code_commit, redact_secrets, utc_now

log = logging.getLogger("fde.scheduler")

# Execution-status -> authoritative outcome. The scheduler still tracks its
# own lifecycle vocabulary internally; this is the single translation point.
_JOB_STATUS_TO_OUTCOME: dict[JobStatus, Outcome] = {}


def _domain_from(result: JobResult | None) -> DomainState | None:
    """Read the domain state a handler reported, if any."""
    if result is None:
        return None
    raw = (result.detail or {}).get("domain_state")
    return DomainState(raw) if raw else None


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return utc_now()


@dataclass
class FrozenClock:
    """Deterministic clock for tests and accelerated dry runs."""

    current: datetime

    def now(self) -> datetime:
        return self.current

    def advance(self, delta: timedelta) -> datetime:
        self.current = self.current + delta
        return self.current

    def set(self, when: datetime) -> datetime:
        if when < self.current:
            raise ValueError("scheduler clock may not move backwards")
        self.current = when
        return self.current


class JobStatus(StrEnum):
    RUNNING = "running"
    FINISHED = "finished"
    FAILED = "failed"
    SKIPPED = "skipped"
    INTERRUPTED = "interrupted"
    DEAD_LETTER = "dead_letter"


_JOB_STATUS_TO_OUTCOME.update({})  # populated after JobStatus is defined


class CatchUpPolicy(StrEnum):
    """What to do about a slot whose scheduled time has passed."""

    SKIP = "skip"  # the moment is gone (e.g. a closing capture)
    RUN_LATEST_ONLY = "run_latest_only"  # collapse missed slots into one
    RUN_ALL = "run_all"  # every missed slot still matters


@dataclass
class JobResult:
    records_read: int = 0
    records_written: int = 0
    provider_calls: int = 0
    provider_credits: int = 0
    warnings: list[str] = field(default_factory=list)
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class RetryPolicy:
    max_attempts: int = 4
    base_seconds: float = 30.0
    max_seconds: float = 900.0
    jitter_ratio: float = 0.25

    def delay_for(self, attempt: int, rng: random.Random | None = None) -> float:
        """Bounded exponential backoff with jitter."""
        generator = rng if rng is not None else random.Random()
        raw = min(self.base_seconds * (2 ** max(0, attempt - 1)), self.max_seconds)
        jitter = raw * self.jitter_ratio
        return max(0.0, raw + generator.uniform(-jitter, jitter))


@dataclass
class JobDefinition:
    name: str
    handler: Callable[[JobContext], JobResult]
    interval: timedelta | None = None
    catch_up: CatchUpPolicy = CatchUpPolicy.RUN_LATEST_ONLY
    timeout: timedelta = timedelta(minutes=10)
    retry: RetryPolicy = field(default_factory=RetryPolicy)
    critical: bool = False
    description: str = ""


@dataclass
class JobContext:
    """Everything a handler is allowed to know."""

    session: Session
    clock: Clock
    job: JobDefinition
    slot: datetime
    attempt: int
    cohort: Cohort
    provider_mode: ProviderMode
    policy_version: str | None
    data_mode: DataMode
    idempotency_key: str
    params: dict[str, Any] = field(default_factory=dict)

    def now(self) -> datetime:
        return self.clock.now()


class JobSkipped(RuntimeError):
    """Raised by a handler to record a deliberate no-op."""


_JOB_STATUS_TO_OUTCOME.update(
    {
        JobStatus.RUNNING: Outcome.RUNNING,
        JobStatus.FINISHED: Outcome.SUCCESS,
        JobStatus.FAILED: Outcome.RETRYABLE_FAILURE,
        JobStatus.SKIPPED: Outcome.SKIPPED,
        JobStatus.INTERRUPTED: Outcome.INTERRUPTED,
        JobStatus.DEAD_LETTER: Outcome.TERMINAL_FAILURE,
    }
)


def slot_for(interval: timedelta, now: datetime) -> datetime:
    """Floor `now` to the job's logical slot.

    Two workers waking a second apart land on the same slot and therefore
    the same idempotency key, so only one executes.
    """
    seconds = max(1, int(interval.total_seconds()))
    epoch = datetime(2026, 1, 1, tzinfo=now.tzinfo)
    elapsed = int((now - epoch).total_seconds())
    return epoch + timedelta(seconds=(elapsed // seconds) * seconds)


def make_idempotency_key(
    *, job_name: str, slot: datetime, cohort: Cohort, params: dict[str, Any] | None = None
) -> str:
    suffix = ""
    if params:
        suffix = ":" + ",".join(f"{k}={params[k]}" for k in sorted(params))
    return f"{job_name}:{cohort.value}:{slot.strftime('%Y%m%dT%H%M%SZ')}{suffix}"


class Scheduler:
    """Database-backed scheduler. Schedules live in code; run history and
    locking live in the database, so both survive restart."""

    def __init__(
        self,
        session_factory: Callable[[], Session],
        *,
        clock: Clock | None = None,
        cohort: Cohort = Cohort.BURN_IN,
        provider_mode: ProviderMode = ProviderMode.FIXTURE,
        policy_version: str | None = None,
        data_mode: DataMode = DataMode.LIVE_RESEARCH,
        rng: random.Random | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.clock = clock or SystemClock()
        self.cohort = cohort
        self.provider_mode = provider_mode
        self.policy_version = policy_version
        self.data_mode = data_mode
        self.rng = rng or random.Random(20260801)
        self.jobs: dict[str, JobDefinition] = {}
        self._shutdown = False

    # -- registration --------------------------------------------------- #

    def register(self, job: JobDefinition) -> JobDefinition:
        if job.name in self.jobs:
            raise ValueError(f"job {job.name} is already registered")
        self.jobs[job.name] = job
        return job

    # -- lifecycle ------------------------------------------------------- #

    def request_shutdown(self) -> None:
        """Graceful stop: finish the in-flight job, start no new ones."""
        self._shutdown = True

    @property
    def shutting_down(self) -> bool:
        return self._shutdown

    def _recovery_lineage(
        self, prior: ScheduledJobRun, run_id: str, key: str, reason: str
    ) -> dict[str, Any]:
        """A recovery inherits the root and slot, and increments by one."""
        return {
            "root_run_id": prior.root_run_id or prior.id,
            "recovery_of_run_id": prior.id,
            "recovery_sequence": (prior.recovery_sequence or 0) + 1,
            "logical_slot": prior.logical_slot or prior.scheduled_for,
            "original_idempotency_key": prior.original_idempotency_key or prior.idempotency_key,
            "recovery_reason": reason,
            "reconciled_at": self.clock.now(),
            "prior_effects_detected": None,
            "replay_decision": None,
            "administrative_override": False,
            "override_operator": None,
            "override_reason": None,
        }

    def recovery_chain(self, root_run_id: str) -> list[ScheduledJobRun]:
        """Chain members in chronological order."""
        with self._session() as s:
            rows = list(
                s.scalars(
                    select(ScheduledJobRun)
                    .where(ScheduledJobRun.root_run_id == root_run_id)
                    .order_by(ScheduledJobRun.recovery_sequence, ScheduledJobRun.created_at)
                )
            )
            for r in rows:
                s.expunge(r)
        return rows

    def reconcile_startup(self) -> dict[str, Any]:
        """Mark runs orphaned by a crash, so they are never counted as
        finished and never silently re-execute under the same key."""
        reconciled: list[str] = []
        with self._session() as s:
            # Query the AUTHORITATIVE axis, and write through apply_state so
            # the compatibility column is projected rather than set by hand.
            rows = s.scalars(
                select(ScheduledJobRun).where(
                    ScheduledJobRun.job_outcome == Outcome.RUNNING.value
                )
            ).all()
            for r in rows:
                apply_state(r, outcome=Outcome.INTERRUPTED)
                r.completed_at = self.clock.now()
                r.reconciled_at = self.clock.now()
                r.error_summary = "process terminated before completion; reconciled at startup"
                reconciled.append(r.idempotency_key)
        log.info("startup reconciliation", extra={"interrupted": len(reconciled)})
        return {"interrupted_runs": reconciled, "count": len(reconciled)}

    # -- execution ------------------------------------------------------- #

    @contextmanager
    def _session(self) -> Iterator[Session]:
        s = self.session_factory()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    def _claim(
        self, key: str, job: JobDefinition, slot: datetime, attempt: int,
        lineage: dict[str, Any] | None = None,
    ) -> str | None:
        """Atomically claim a slot. Returns the run id, or None if another
        worker already holds it (UNIQUE constraint on idempotency_key)."""
        run_id = f"run_{uuid.uuid4().hex[:16]}"
        # Lineage is populated immediately, not lazily when something fails:
        # an initial run is a complete chain of one, and leaving the fields
        # null until an interruption makes the chain unqueryable.
        # A recovery carries its predecessor's chain; a fresh run is its own root.
        if lineage is not None:
            lineage = {**lineage, "root_run_id": lineage["root_run_id"]}
            lineage.pop("idempotency_key", None)
        else:
            lineage = self._initial_lineage(run_id, key, slot)
        try:
            with self._session() as s:
                run = ScheduledJobRun(
                    id=run_id,
                    job_kind=job.name,
                    idempotency_key=key,
                    data_mode=self.data_mode.value,
                    scheduled_for=slot,
                    started_at=self.clock.now(),
                    retry_count=attempt - 1,
                    provider_calls=0,
                    records_received=0,
                    records_written=0,
                    code_commit=current_code_commit(),
                    created_at=self.clock.now(),
                    **lineage,
                )
                apply_state(run, outcome=Outcome.RUNNING, state_origin=StateOrigin.LIVE)
                s.add(run)
        except IntegrityError:
            return None
        return run_id

    @staticmethod
    def _initial_lineage(run_id: str, key: str, slot: datetime) -> dict[str, Any]:
        """An initial run is its own root at sequence zero."""
        return {
            "root_run_id": run_id,
            "recovery_of_run_id": None,
            "recovery_sequence": 0,
            "logical_slot": slot,
            "original_idempotency_key": key,
            "recovery_reason": None,
            "reconciled_at": None,
            "prior_effects_detected": False,
            "replay_decision": None,
            "administrative_override": False,
            "override_operator": None,
            "override_reason": None,
        }

    def _plan_recovery(
        self, job: JobDefinition, key: str, *, reason: str,
        administrative_override: bool = False,
        override_operator: str | None = None,
        override_reason: str | None = None,
    ) -> tuple[str, dict[str, Any] | None]:
        """Decide whether this slot is a fresh run or a recovery.

        Returns (idempotency_key, lineage). A lineage of None means there is
        nothing to recover and the original key stands. When the prior run
        was interrupted, the domain is inspected for committed effects and
        the resulting typed replay decision is carried on the new row — a
        crashed process may well have committed its write before dying, and
        only the domain can say so.
        """
        from fde_api.forward.recovery import (
            RecoveryError,
            build_recovery_lineage,
            inspect_prior_effects,
        )

        with self._session() as s:
            prior = s.scalars(
                select(ScheduledJobRun)
                .where(ScheduledJobRun.idempotency_key.like(f"{key}%"))
                # Order by recovery_sequence, not created_at: members of a
                # chain can share a timestamp (frozen clock, or two recoveries
                # inside the same second), and then created_at ordering is
                # arbitrary. Sequence is monotonic by construction.
                .order_by(
                    ScheduledJobRun.recovery_sequence.desc(),
                    ScheduledJobRun.created_at.desc(),
                )
            ).all()
            if not prior:
                return key, None
            latest = prior[0]
            if latest.job_outcome != Outcome.INTERRUPTED.value:
                return key, None

            root = latest.root_run_id or latest.id
            seq = (latest.recovery_sequence or 0) + 1
            recovery_key = f"{key}#recovery{seq}"

            # Inspect using the CHAIN's stable key, not this attempt's key.
            # Domain rows are written under the original key; a recovery whose
            # writes were idempotent no-ops has no rows of its own, so keying
            # off `latest.idempotency_key` would make the second recovery in a
            # chain conclude "nothing ever happened" and replay freely. Harmless
            # for idempotent observation jobs, wrong for terminal ones.
            inspection = inspect_prior_effects(
                s,
                job_kind=job.name,
                idempotency_key=latest.original_idempotency_key or latest.idempotency_key,
                logical_slot=latest.logical_slot or latest.scheduled_for,
            )
            try:
                lineage = build_recovery_lineage(
                    s, latest,
                    run_id="",  # assigned by _claim
                    recovery_key=recovery_key,
                    reason=reason,
                    now=self.clock.now(),
                    inspection=inspection,
                    administrative_override=administrative_override,
                    override_operator=override_operator,
                    override_reason=override_reason,
                )
            except RecoveryError as e:
                log.warning("recovery refused", extra={"job": job.name, "root": root,
                                                       "error": str(e)})
                raise
            lineage["_inspection"] = inspection.as_dict()
        return recovery_key, lineage

    def already_completed(self, key: str) -> bool:
        with self._session() as s:
            row = s.scalars(
                select(ScheduledJobRun).where(ScheduledJobRun.idempotency_key == key)
            ).first()
            if row is None:
                return False
            return row.job_outcome in (
                Outcome.SUCCESS.value,
                Outcome.SUCCESS_WITH_WARNINGS.value,
                Outcome.SKIPPED.value,
                Outcome.TERMINAL_FAILURE.value,
            )

    def run_job(
        self,
        job_name: str,
        *,
        slot: datetime | None = None,
        params: dict[str, Any] | None = None,
        manual: bool = False,
        recovery_reason: str = "prior run was interrupted",
        administrative_override: bool = False,
        override_operator: str | None = None,
        override_reason: str | None = None,
    ) -> dict[str, Any]:
        """Execute one job for one slot, honoring idempotency and retries."""
        job = self.jobs[job_name]
        now = self.clock.now()
        slot = slot or (slot_for(job.interval, now) if job.interval else now)
        key = make_idempotency_key(job_name=job_name, slot=slot, cohort=self.cohort, params=params)

        if self.already_completed(key):
            return {"job": job_name, "status": "skipped", "reason": "idempotency key already completed",
                    "idempotency_key": key}

        # A slot whose previous run was INTERRUPTED is eligible for recovery
        # under a distinct key, so the attempt is recorded rather than
        # silently skipped. The domain is inspected for committed effects and
        # the typed replay decision travels on the recovery row.
        from fde_api.forward.recovery import RecoveryError, ReplayDecision

        try:
            key, recovery_lineage = self._plan_recovery(
                job, key, reason=recovery_reason,
                administrative_override=administrative_override,
                override_operator=override_operator,
                override_reason=override_reason,
            )
        except RecoveryError as e:
            return {"job": job_name, "status": "refused", "reason": str(e),
                    "idempotency_key": key}

        inspection = (recovery_lineage or {}).pop("_inspection", None)
        decision = (recovery_lineage or {}).get("replay_decision")

        # A decision of MANUAL_REVIEW_REQUIRED stops automatic recovery: the
        # effects are ambiguous and a human has to reconcile them.
        if decision == ReplayDecision.MANUAL_REVIEW_REQUIRED.value and not administrative_override:
            self._record_blocked_recovery(job, key, slot, recovery_lineage, inspection)
            return {"job": job_name, "status": "manual_review_required",
                    "reason": (inspection or {}).get("reason", "ambiguous prior effects"),
                    "idempotency_key": key, "inspection": inspection}

        last_error: str | None = None
        for attempt in range(1, job.retry.max_attempts + 1):
            run_id = self._claim(key, job, slot, attempt, lineage=recovery_lineage)
            if run_id is None:
                return {"job": job_name, "status": "skipped",
                        "reason": "another worker holds this slot", "idempotency_key": key}

            started = time.monotonic()
            try:
                with self._session() as s:
                    ctx = JobContext(
                        session=s, clock=self.clock, job=job, slot=slot, attempt=attempt,
                        cohort=self.cohort, provider_mode=self.provider_mode,
                        policy_version=self.policy_version, data_mode=self.data_mode,
                        idempotency_key=key, params=params or {},
                    )
                    result = job.handler(ctx)
                elapsed = time.monotonic() - started
                if elapsed > job.timeout.total_seconds():
                    raise TimeoutError(
                        f"{job_name} exceeded timeout {job.timeout.total_seconds():.0f}s"
                    )
                self._finish(run_id, JobStatus.FINISHED, result=result)
                log.info("job finished", extra={"job": job_name, "key": key, "attempt": attempt})
                return {"job": job_name, "status": "finished", "attempt": attempt,
                        "idempotency_key": key, "run_id": run_id,
                        "replay_decision": decision,
                        "recovery_sequence": (recovery_lineage or {}).get("recovery_sequence", 0),
                        "records_written": result.records_written,
                        "provider_calls": result.provider_calls,
                        "provider_credits": result.provider_credits,
                        "warnings": result.warnings}
            except JobSkipped as e:
                self._finish(run_id, JobStatus.SKIPPED, error=str(e))
                return {"job": job_name, "status": "skipped", "reason": str(e),
                        "idempotency_key": key, "run_id": run_id}
            except Exception as e:
                last_error = f"{type(e).__name__}: {e}"
                terminal = attempt >= job.retry.max_attempts
                self._finish(
                    run_id,
                    JobStatus.DEAD_LETTER if terminal else JobStatus.FAILED,
                    error=last_error,
                )
                log.warning("job attempt failed",
                            extra={"job": job_name, "attempt": attempt, "error": last_error})
                if terminal:
                    return {"job": job_name, "status": "dead_letter", "attempt": attempt,
                            "error": last_error, "idempotency_key": key}
                # A retry is a NEW attempt under a distinct key suffix, so the
                # failed row is preserved as history rather than overwritten.
                key = f"{key}#r{attempt}"
        return {"job": job_name, "status": "dead_letter", "error": last_error}

    def _record_blocked_recovery(
        self, job: JobDefinition, key: str, slot: datetime,
        lineage: dict[str, Any] | None, inspection: dict[str, Any] | None,
    ) -> None:
        """Persist the fact that automatic recovery was refused.

        Without this row the refusal is invisible and an operator has no
        record of why the chain stopped.
        """
        if lineage is None:
            return
        run_id = f"run_{uuid.uuid4().hex[:16]}"
        payload = {k: v for k, v in lineage.items() if k != "idempotency_key"}
        with self._session() as s:
            run = ScheduledJobRun(
                id=run_id, job_kind=job.name, idempotency_key=key,
                data_mode=self.data_mode.value, scheduled_for=slot,
                started_at=self.clock.now(), completed_at=self.clock.now(),
                retry_count=0, provider_calls=0, records_received=0, records_written=0,
                code_commit=current_code_commit(), created_at=self.clock.now(),
                error_summary=(inspection or {}).get("reason", "manual review required")[:2000],
                **payload,
            )
            apply_state(run, outcome=Outcome.SKIPPED,
                        domain_state=DomainState.DATA_INCOMPLETE,
                        state_origin=StateOrigin.LIVE)
            s.add(run)

    def _finish(
        self,
        run_id: str,
        status: JobStatus,
        *,
        result: JobResult | None = None,
        error: str | None = None,
    ) -> None:
        with self._session() as s:
            row = s.get(ScheduledJobRun, run_id)
            if row is None:
                return
            row.completed_at = self.clock.now()
            apply_state(row, outcome=_JOB_STATUS_TO_OUTCOME[status],
                        domain_state=_domain_from(result))
            if result is not None:
                row.records_received = result.records_read
                row.records_written = result.records_written
                row.provider_calls = result.provider_calls
                # redact_secrets at the point of PERSISTENCE, not at each
                # producer: error summaries are built from arbitrary
                # exception messages and written to the database, so this
                # has to cover handlers that do not exist yet.
                row.error_summary = redact_secrets("; ".join(result.warnings)[:2000]) or None
            if error is not None:
                row.error_summary = redact_secrets(error[:2000])

    # -- tick ------------------------------------------------------------ #

    def due_jobs(self, now: datetime | None = None) -> list[tuple[JobDefinition, datetime]]:
        now = now or self.clock.now()
        out: list[tuple[JobDefinition, datetime]] = []
        for job in self.jobs.values():
            if job.interval is None:
                continue
            out.append((job, slot_for(job.interval, now)))
        return out

    def tick(self) -> list[dict[str, Any]]:
        """One scheduling pass. Safe to call from multiple processes."""
        if self._shutdown:
            return []
        results = []
        for job, slot in self.due_jobs():
            if self._shutdown:
                break
            results.append(self.run_job(job.name, slot=slot))
        return results

    def missed_runs(self, *, lookback: timedelta) -> list[dict[str, Any]]:
        """Slots that should have executed in the window but have no
        terminal run recorded."""
        now = self.clock.now()
        missed: list[dict[str, Any]] = []
        with self._session() as s:
            for job in self.jobs.values():
                if job.interval is None:
                    continue
                slot = slot_for(job.interval, now)
                cursor = slot
                while cursor > now - lookback:
                    key = make_idempotency_key(
                        job_name=job.name, slot=cursor, cohort=self.cohort
                    )
                    row = s.scalars(
                        select(ScheduledJobRun).where(ScheduledJobRun.idempotency_key == key)
                    ).first()
                    if row is None:
                        missed.append({"job": job.name, "slot": cursor.isoformat(),
                                       "catch_up": job.catch_up.value})
                    cursor -= job.interval
        return missed

    def health(self) -> dict[str, Any]:
        """Scheduler process health for Data Health and readiness probes."""
        # Select the column, not the ORM object: instances detach when the
        # session closes and reading an attribute would then raise.
        with self._session() as s:
            statuses = list(s.scalars(select(ScheduledJobRun.status)))
        by_status: dict[str, int] = {}
        for st in statuses:
            by_status[st] = by_status.get(st, 0) + 1
        return {
            "registered_jobs": sorted(self.jobs),
            "job_count": len(self.jobs),
            "runs_by_status": by_status,
            "failed": by_status.get(JobStatus.FAILED.value, 0),
            "dead_letter": by_status.get(JobStatus.DEAD_LETTER.value, 0),
            "interrupted": by_status.get(JobStatus.INTERRUPTED.value, 0),
            "shutting_down": self._shutdown,
            "cohort": self.cohort.value,
            "provider_mode": self.provider_mode.value,
            "policy_version": self.policy_version,
            "clock": self.clock.now().isoformat(),
        }


# --------------------------------------------------------------------------- #
# Job catalogue — cadence expressed once, so the quota forecast is generated
# from the same numbers the scheduler actually uses.
# --------------------------------------------------------------------------- #

JOB_CADENCE: dict[str, timedelta] = {
    "schedule_refresh": timedelta(hours=12),
    "odds_capture": timedelta(minutes=5),
    # Consensus is derived from quotes and makes no provider calls, so it
    # tracks the capture cadence. It was previously absent from this table
    # and silently fell back to an hourly default, meaning predictions
    # could be built on a consensus up to an hour behind the quotes it
    # was supposed to summarise.
    "consensus_build": timedelta(minutes=5),
    "weather_capture": timedelta(hours=3),
    "injury_reconciliation": timedelta(hours=6),
    "availability_computation": timedelta(hours=6),
    "feature_snapshot": timedelta(hours=6),
    "prediction_vintage": timedelta(hours=1),
    "manual_price_expiration": timedelta(minutes=15),
    "closing_capture": timedelta(minutes=2),
    "result_ingestion": timedelta(hours=1),
    "settlement": timedelta(hours=1),
    "forward_evaluation": timedelta(hours=6),
    "data_health_reconciliation": timedelta(minutes=30),
}

JOB_CATCH_UP: dict[str, CatchUpPolicy] = {
    # A closing capture missed is gone; replaying it later would record a
    # price that was never observable at the close.
    "closing_capture": CatchUpPolicy.SKIP,
    "odds_capture": CatchUpPolicy.SKIP,
    "weather_capture": CatchUpPolicy.SKIP,
    # Vintages and settlements are still correct when computed late.
    "prediction_vintage": CatchUpPolicy.RUN_ALL,
    "settlement": CatchUpPolicy.RUN_ALL,
    "result_ingestion": CatchUpPolicy.RUN_ALL,
}

CRITICAL_JOBS = frozenset({"odds_capture", "closing_capture", "prediction_vintage", "settlement"})
