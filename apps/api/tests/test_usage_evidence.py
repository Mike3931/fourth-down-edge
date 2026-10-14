import pytest

from fde_api.db.research_models import ResearchFact
from fde_api.forward.usage_evidence import usage_evidence


def fact(*, player=False, **changes):
    values = {"attempts": "10", "carries": "5", "targets": "2"} if player else {
        "attempts": "40", "carries": "20", "targets": "10"}
    fields = {"id": "player" if player else "team", "source_id": "player-source" if player else "team-source",
              "team_id": "KC", "canonical_game_id": "2026_05_KC_BUF", "week": 5,
              "player_id": "00-0000001" if player else None, "values": values}
    fields.update(changes)
    return ResearchFact(**fields)


def test_exact_game_usage_keeps_denominators_and_lineage_without_route_or_starter_inference():
    result = usage_evidence(fact(player=True), [fact()])
    assert result["shares"]["attempts"] == {"player_count": 10, "team_count": 40, "share": .25}
    assert result["shares"]["targets"]["share"] == .2
    assert result["source_row_ids"] == ["player", "team"]
    assert result["source_ids"] == ["player-source", "team-source"]
    assert result["missing"] == []
    assert result["sample_games"] == 1
    assert result["route_participation"] is result["expected_starter"] is result["projected_opportunity"] is None


@pytest.mark.parametrize("changes", [{"team_id": "BUF"}, {"canonical_game_id": "OTHER"},
                                    {"week": 4}, {"player_id": "00-0000002"}])
def test_usage_refuses_wrong_team_game_week_or_player_denominator(changes):
    result = usage_evidence(fact(player=True), [fact(**changes)])
    assert "TEAM_GAME_DENOMINATOR_MISSING" in result["missing"]
    assert all(item["share"] is None for item in result["shares"].values())


def test_duplicate_denominator_is_withheld_and_both_sources_retained():
    result = usage_evidence(fact(player=True), [fact(), fact(id="other", source_id="revision")])
    assert "TEAM_GAME_DENOMINATOR_AMBIGUOUS" in result["missing"]
    assert result["source_row_ids"] == ["other", "player", "team"]
    assert all(item["share"] is None for item in result["shares"].values())


@pytest.mark.parametrize("value", [None, "", "nan", "inf", "-1", "1.5", True, "0", "1"])
def test_missing_invalid_zero_and_inconsistent_denominators_never_become_valid_shares(value):
    result = usage_evidence(fact(player=True), [fact(values={"targets": value})])
    assert result["shares"]["targets"]["share"] is None


def test_observed_zero_is_distinct_from_missing_count():
    result = usage_evidence(fact(player=True, values={"targets": "0"}), [fact()])
    assert result["shares"]["targets"]["share"] == 0
    assert result["shares"]["carries"]["share"] is None


def test_canonical_player_required_even_with_matching_team_totals():
    result = usage_evidence(fact(player=True, player_id=None), [fact()])
    assert "PLAYER_ID_MISSING" in result["missing"]
    assert all(item["share"] is None for item in result["shares"].values())
