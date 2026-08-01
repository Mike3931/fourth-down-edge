"""Execution realism: prices, fills, settlement, determinism."""

from __future__ import annotations

from fde_api.backtest.execution import (
    ExecutionConfig,
    ExecutionModel,
    _worsen_price,
    break_even_prob,
)


def test_price_worsening_crosses_even_money() -> None:
    assert _worsen_price(-110, 5) == -115
    assert _worsen_price(102, 5) == -103  # +102 → +101 → +100 → −101 → −102 → −103
    assert _worsen_price(100, 1) == -101


def test_break_even_prob() -> None:
    assert abs(break_even_prob(-110) - 110 / 210) < 1e-12
    assert abs(break_even_prob(120) - 100 / 220) < 1e-12


def test_settlement_push_and_void() -> None:
    s = ExecutionModel.settle(
        market="SPREAD", selection="HOME", line=-3.0, price_american=-110, home_score=24, away_score=21
    )
    assert s.result == "PUSH" and s.pnl_units == 0.0
    v = ExecutionModel.settle(
        market="SPREAD", selection="HOME", line=-3.0, price_american=-110, home_score=None, away_score=None
    )
    assert v.result == "VOID" and v.pnl_units == 0.0


def test_settlement_win_loss_amounts() -> None:
    w = ExecutionModel.settle(
        market="TOTAL", selection="OVER", line=44.5, price_american=-110, home_score=28, away_score=20
    )
    assert w.result == "WIN" and abs(w.pnl_units - 100 / 110) < 1e-9
    loss = ExecutionModel.settle(
        market="TOTAL", selection="UNDER", line=44.5, price_american=130, home_score=28, away_score=20
    )
    assert loss.result == "LOSS" and loss.pnl_units == -1.0


def test_fill_is_deterministic_per_seed() -> None:
    a = ExecutionModel(ExecutionConfig(seed=1))
    b = ExecutionModel(ExecutionConfig(seed=1))
    c = ExecutionModel(ExecutionConfig(seed=2))
    key = {"game_id": "2025_01_A_B", "market": "SPREAD", "selection": "HOME", "line": -3.0, "price_american": -110}
    fa, fb, fc = a.attempt_fill(**key), b.attempt_fill(**key), c.attempt_fill(**key)
    assert fa == fb
    # different seed may differ; at minimum the object is well-formed
    assert fc.filled in (True, False)


def test_fill_deteriorates_price() -> None:
    m = ExecutionModel(ExecutionConfig(no_fill_prob=0.0, suspended_prob=0.0, price_deterioration_cents=5))
    f = m.attempt_fill(game_id="g", market="SPREAD", selection="HOME", line=-3.0, price_american=-110)
    assert f.filled and f.price_american == -115
    assert f.line in (-3.0, -3.5)  # possible half-point deterioration against HOME
