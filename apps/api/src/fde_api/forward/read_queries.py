"""Read current revisions in SQL without materializing historical payloads."""
from sqlalchemy import func, select

from fde_api.db.forward_models import ScheduleObservation


def latest_schedule(*criteria):
    # Apply cutoff/mode/identity before ranking, but status/kickoff-window filters
    # after it: a reschedule or cancellation must supersede its older revision.
    ranked = select(
        ScheduleObservation.id,
        func.row_number().over(
            partition_by=ScheduleObservation.canonical_game_id,
            order_by=(ScheduleObservation.observed_at.desc(), ScheduleObservation.id.desc()),
        ).label("revision"),
    ).where(*criteria).subquery()
    return select(ScheduleObservation).where(ScheduleObservation.id.in_(
        select(ranked.c.id).where(ranked.c.revision == 1)
    ))
