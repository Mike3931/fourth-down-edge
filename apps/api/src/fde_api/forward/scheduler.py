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
from fde_api.util import current_code_commit, utc_now

log = logging.getLogger("fde.scheduler")


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

    def reconcile_startup(self) -> dict[str, Any]:
        """Mark runs orphaned by a crash, so they are never counted as
        finished and never silently re-execute under the same key."""
        reconciled: list[str] = []
        with self._session() as s:
            rows = s.scalars(
                select(ScheduledJobRun).where(ScheduledJobRun.status == JobStatus.RUNNING.value)
            ).all()
            for r in rows:
                r.status = JobStatus.INTERRUPTED.value
                r.completed_at = self.clock.now()
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

    def _claim(self, key: str, job: JobDefinition, slot: datetime, attempt: int) -> str | None:
        """Atomically claim a slot. Returns the run id, or None if another
        worker already holds it (UNIQUE constraint on idempotency_key)."""
        run_id = f"run_{uuid.uuid4().hex[:16]}"
        try:
            with self._session() as s:
                s.add(
                    ScheduledJobRun(
                        id=run_id,
                        job_kind=job.name,
                        idempotency_key=key,
                        data_mode=self.data_mode.value,
                        scheduled_for=slot,
                        started_at=self.clock.now(),
                        status=JobStatus.RUNNING.value,
                        retry_count=attempt - 1,
                        provider_calls=0,
                        records_received=0,
                        records_written=0,
                        code_commit=current_code_commit(),
                        created_at=self.clock.now(),
                    )
                )
        except IntegrityError:
            return None
        return run_id

    def _recovery_key(self, key: str) -> str:
        """Return a fresh key when the prior run for this slot was interrupted.

        Without this, a post-crash retry loses the UNIQUE-insert race against
        its own abandoned row and is skipped, leaving no record of the
        recovery attempt.
        """
        with self._session() as s:
            prior = s.scalars(
                select(ScheduledJobRun)
                .where(ScheduledJobRun.idempotency_key.like(f"{key}%"))
                .order_by(ScheduledJobRun.created_at.desc())
            ).all()
            if not prior:
                return key
            latest = prior[0]
            if latest.status != JobStatus.INTERRUPTED.value:
                return key
            recoveries = sum(1 for p in prior if "#recovery" in p.idempotency_key)
        return f"{key}#recovery{recoveries + 1}"

    def already_completed(self, key: str) -> bool:
        with self._session() as s:
            row = s.scalars(
                select(ScheduledJobRun).where(ScheduledJobRun.idempotency_key == key)
            ).first()
            return row is not None and row.status in (
                JobStatus.FINISHED.value,
                JobStatus.SKIPPED.value,
                JobStatus.DEAD_LETTER.value,
            )

    def run_job(
        self,
        job_name: str,
        *,
        slot: datetime | None = None,
        params: dict[str, Any] | None = None,
        manual: bool = False,
    ) -> dict[str, Any]:
        """Execute one job for one slot, honoring idempotency and retries."""
        job = self.jobs[job_name]
        now = self.clock.now()
        slot = slot or (slot_for(job.interval, now) if job.interval else now)
        key = make_idempotency_key(job_name=job_name, slot=slot, cohort=self.cohort, params=params)

        if self.already_completed(key):
            return {"job": job_name, "status": "skipped", "reason": "idempotency key already completed",
                    "idempotency_key": key}

        # A slot whose previous run was INTERRUPTED (process died) is eligible
        # for recovery under a distinct key, so the retry is recorded rather
        # than silently skipped. Domain-level idempotency — not the run key —
        # is what prevents duplicate records on that retry.
        key = self._recovery_key(key)

        last_error: str | None = None
        for attempt in range(1, job.retry.max_attempts + 1):
            run_id = self._claim(key, job, slot, attempt)
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
            row.status = status.value
            row.completed_at = self.clock.now()
            if result is not None:
                row.records_received = result.records_read
                row.records_written = result.records_written
                row.provider_calls = result.provider_calls
                row.error_summary = "; ".join(result.warnings)[:2000] or None
            if error is not None:
                row.error_summary = error[:2000]

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
