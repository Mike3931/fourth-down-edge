from datetime import UTC, datetime, timedelta

import pytest

from fde_api.forward.injuries import (
    AVAILABILITY_METHOD_VERSION,
    AvailabilityState,
    InjuryValidationError,
    SourceCategory,
    classify,
    observations_as_of,
    record_injury_observation,
)
from fde_api.pit.guards import LookaheadError

NOW = datetime(2026, 10, 13, tzinfo=UTC)


def record(s, designation, **kwargs):
    return record_injury_observation(s, canonical_game_id="GAME", team_id="KC", player_id="00-0000001",
        report_date="2026-10-13", observed_at=NOW, source_category=SourceCategory.OFFICIAL_VERIFIED,
        game_designation=designation, now=kwargs.pop("now", NOW), **kwargs)


def test_out_is_not_a_confirmed_inactive_and_version_changes(session):
    assert AVAILABILITY_METHOD_VERSION == "availability-v1"
    assert classify(record(session, "OUT")) == AvailabilityState.EXPECTED_INACTIVE
    with pytest.raises(InjuryValidationError):
        record(session, "INACTIVE")
    obs = record(session, "INACTIVE", source_updated_at=NOW - timedelta(minutes=1),
                 source_reference="https://www.nfl.com/news/official-game-inactives")
    assert classify(obs) == AvailabilityState.CONFIRMED_INACTIVE


def test_late_entry_and_future_issuance_cannot_leak(session):
    record(session, "OUT", now=NOW + timedelta(hours=1))
    assert observations_as_of(session, canonical_game_id="GAME", as_of_at=NOW) == []
    with pytest.raises(LookaheadError):
        record(session, "OUT", source_updated_at=NOW + timedelta(minutes=1))
