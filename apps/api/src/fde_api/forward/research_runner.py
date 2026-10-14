"""Auditable research preparation attempts, separate from official evaluations."""
from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from fde_api.db.models import Job
from fde_api.forward.briefing import KIND, VERSION, prepare_briefing


def run_cycle(factory: Callable[[], Session], capture: Callable[[], dict[str, int]],
              *, dates: str) -> dict[str, Any]:
    now = datetime.now(UTC)
    job_id = str(uuid.uuid4())
    with factory() as s:
        s.add(Job(id=job_id, kind=KIND, params={"version": VERSION, "dates": dates,
              "source": "espn-public-scoreboard-v2"}, status="running", created_at=now,
              started_at=now))
        s.commit()
    try:
        counts = capture()
        with factory() as s:
            result = prepare_briefing(s, now=datetime.now(UTC))
            job = s.get(Job, job_id)
            assert job is not None
            job.status = "finished"
            job.finished_at = datetime.now(UTC)
            job.result = {"capture": counts, "briefing": result}
            s.commit()
        return {"run_id": job_id, "status": "finished", "capture": counts}
    except Exception as exc:
        with factory() as s:
            job = s.get(Job, job_id)
            assert job is not None
            job.status = "failed"
            job.finished_at = datetime.now(UTC)
            job.error = f"Research capture failed ({type(exc).__name__}); no current-source claim"
            s.commit()
        return {"run_id": job_id, "status": "failed"}
