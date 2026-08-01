"""Execution realism for historical simulation.

The simulator never assumes the user got the best historical price.
Every simulated ticket goes through, deterministically per game+market
(seeded hash → reproducible runs):

    1. manual-entry delay (a human typed this into the app),
    2. price deterioration in cents,
    3. possible half-point line deterioration against the selection,
    4. possible no-fill / suspended market,
    5. settlement with pushes and voids honored,

all against the FIXED closing-benchmark definition (nflverse close at
kickoff). Because the only historical prices available are closing
prices, simulated fills are *at or worse than* close — consequently
closing-line value is structurally ≈ 0 minus deterioration in this
phase, and reports must (and do) say that rather than inventing CLV.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Literal

Selection = Literal["HOME", "AWAY", "OVER", "UNDER"]


@dataclass(frozen=True)
class ExecutionConfig:
    manual_delay_minutes: float = 3.0
    price_deterioration_cents: int = 5
    halfpoint_deterioration_prob: float = 0.25
    no_fill_prob: float = 0.02
    suspended_prob: float = 0.005
    seed: int = 20260801


@dataclass(frozen=True)
class Fill:
    filled: bool
    reason: str
    line: float | None
    price_american: int | None


@dataclass(frozen=True)
class Settlement:
    result: Literal["WIN", "LOSS", "PUSH", "VOID"]
    pnl_units: float  # stake = 1 unit


class ExecutionModel:
    def __init__(self, config: ExecutionConfig | None = None) -> None:
        self.cfg = config or ExecutionConfig()

    def _u(self, key: str) -> float:
        """Deterministic uniform in [0,1) from run seed + ticket key."""
        h = hashlib.sha256(f"{self.cfg.seed}:{key}".encode()).digest()
        return int.from_bytes(h[:8], "big") / 2**64

    def attempt_fill(
        self, *, game_id: str, market: str, selection: Selection, line: float | None, price_american: int
    ) -> Fill:
        key = f"{game_id}:{market}:{selection}"
        if self._u(key + ":susp") < self.cfg.suspended_prob:
            return Fill(False, "market suspended at entry time", None, None)
        if self._u(key + ":fill") < self.cfg.no_fill_prob:
            return Fill(False, "quote gone before manual entry completed", None, None)
        price = _worsen_price(price_american, self.cfg.price_deterioration_cents)
        new_line = line
        if line is not None and self._u(key + ":line") < self.cfg.halfpoint_deterioration_prob:
            new_line = _worsen_line(line, selection)
        return Fill(True, f"filled after ~{self.cfg.manual_delay_minutes:g}min manual entry", new_line, price)

    @staticmethod
    def settle(
        *,
        market: str,
        selection: Selection,
        line: float | None,
        price_american: int,
        home_score: int | None,
        away_score: int | None,
    ) -> Settlement:
        if home_score is None or away_score is None:
            return Settlement("VOID", 0.0)
        margin = home_score - away_score
        total = home_score + away_score
        if market == "SPREAD":
            assert line is not None
            v = (margin + line) if selection == "HOME" else (-margin - line)
        elif market == "TOTAL":
            assert line is not None
            v = (total - line) if selection == "OVER" else (line - total)
        elif market == "MONEYLINE":
            v = float(margin if selection == "HOME" else -margin)
        else:
            raise ValueError(f"unknown market {market}")
        if abs(v) < 1e-9:
            return Settlement("PUSH", 0.0)
        if v > 0:
            win = price_american / 100.0 if price_american > 0 else 100.0 / -price_american
            return Settlement("WIN", win)
        return Settlement("LOSS", -1.0)


def _worsen_price(american: int, cents: int) -> int:
    """Move an American price `cents` against the bettor, stepping through
    even money correctly (+102 → +101 → +100 → −101 → −102 …)."""
    for _ in range(cents):
        if american > 100:
            american -= 1
        elif american == 100:
            american = -101
        else:
            american -= 1
    return american


def _worsen_line(line: float, selection: Selection) -> float:
    if selection in ("HOME", "AWAY"):
        # Home line worsens for HOME by −0.5, for AWAY by +0.5 (line is home-relative).
        return line - 0.5 if selection == "HOME" else line + 0.5
    return line - 0.5 if selection == "OVER" else line + 0.5


def break_even_prob(price_american: int) -> float:
    if price_american < 0:
        return -price_american / (-price_american + 100.0)
    return 100.0 / (price_american + 100.0)
