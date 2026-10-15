from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select

from fde_api.db.models import Player
from fde_api.db.research_models import PlayerBinding, ResearchFact, ResearchPoll
from fde_api.forward.official_reports import collect_official, ingest_official
from test_prospective import NOW, cohort  # noqa: F401

HTML = b"""<h1>2020 NFL Injury Report</h1><h2>Injuries - WEEK 6</h2><span>Seahawks</span>
<table><tr><td><a href="/players/test-player/">Test player</a></td><td>RB</td><td>Knee</td><td>Limited Participation in Practice</td><td>Out</td></tr></table>"""


def test_official_page_preserves_unmapped_out_without_inactive_or_issue_time(cohort):  # noqa: F811
    s = cohort
    source = ingest_official(s, HTML, captured_at=NOW, season=2020)
    assert source.published_at is None and not source.coverage["complete_availability"]
    rows = list(s.scalars(select(ResearchFact).where(ResearchFact.source_id == source.id)))
    assert len(rows) == 1 and rows[0].player_id is None
    assert rows[0].canonical_game_id == "2020_06_SEA_DEN"
    assert rows[0].values["designation"] == "Out" and not rows[0].values["confirmed_inactive"]
    assert ingest_official(s, HTML, captured_at=NOW + timedelta(minutes=1), season=2020).id == source.id
    s.add(
        PlayerBinding(
            provider="nfl",
            provider_id="test-player",
            player_id="00-0000001",
            observed_at=NOW,
            evidence={"review": "explicit"},
        )
    )
    s.flush()
    # An NFL slug alone cannot partially publish a canonical player identity.
    assert ingest_official(s,HTML,captured_at=NOW+timedelta(minutes=1),season=2020).id==source.id
    s.add(Player(id="00-0000001",name="Explicit canonical test player"))
    s.add(PlayerBinding(provider="gsis",provider_id="00-0000001",player_id="00-0000001",
        observed_at=NOW,evidence={"review":"explicit GSIS identity"}))
    s.flush()
    mapped = ingest_official(s, HTML, captured_at=NOW + timedelta(minutes=2), season=2020)
    assert mapped.id != source.id and mapped.coverage["mapped_players"] == 1
    assert s.get(ResearchFact, rows[0].id).player_id is None


def test_wrong_week_layout_and_season_refused(cohort):  # noqa: F811
    with pytest.raises(ValueError):
        ingest_official(cohort, HTML, captured_at=NOW, season=2098)
    with pytest.raises(ValueError):
        ingest_official(cohort, b"<html>Unavailable</html>", captured_at=NOW, season=2020)


def test_official_outage_is_audited_without_erasing_evidence(cohort):  # noqa: F811
    s = cohort
    source = ingest_official(s, HTML, captured_at=NOW, season=2020)
    client = httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(503, request=req)))
    assert collect_official(s, season=2020, now=NOW, client=client)["official_failures"] == 1
    assert s.get(type(source), source.id) is not None
    assert s.scalars(select(ResearchPoll)).first().status == "FAILED"
    assert collect_official(s, season=2020, now=NOW + timedelta(minutes=1), client=client) == {
        "official_not_due": 1
    }
