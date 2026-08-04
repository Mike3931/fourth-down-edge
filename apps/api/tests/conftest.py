"""Shared fixtures: in-memory DB + a deterministic synthetic league.

The synthetic league exists for tests where real historical data are
unnecessary (unit/property tests). Nothing synthetic is ever written to
the real data directory.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

import pytest
from hypothesis import settings
from sqlalchemy import String, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from fde_api.db.models import Base, Game, OddsSnapshot, Team, TeamGameStat

TEAMS = ["AAA", "BBB", "CCC", "DDD", "EEE", "FFF", "GGG", "HHH"]
STRENGTH = dict(zip(TEAMS, [6, 4, 2, 1, -1, -2, -4, -6], strict=True))


# --------------------------------------------------------------------------- #
# SQLite must not be more permissive than PostgreSQL
# --------------------------------------------------------------------------- #
# SQLite ignores VARCHAR(n) entirely: it stores whatever it is handed. So a
# value that PostgreSQL rejects with StringDataRightTruncation is written
# without complaint locally, and the whole suite reports green while the
# real database would refuse the insert. That is not hypothetical - an
# unbounded idempotency key passed 539 SQLite tests and failed the first
# time it met PostgreSQL in CI.
#
# This listener closes the gap for every ORM write in the suite rather than
# for the one column that happened to break. It is a TEST-ONLY control:
# production behaviour is whatever the real backend enforces, and the point
# is that the fast local suite now enforces the same thing.
#
# Coverage limit worth stating: mapper events see ORM inserts and updates,
# not Core `insert()` statements or raw SQL. Those still depend on the
# PostgreSQL gate.


class StringWidthExceeded(AssertionError):
    """An ORM write would overflow a declared VARCHAR width."""


def _enforce_string_widths(mapper, _connection, target) -> None:
    for attr in mapper.column_attrs:
        col = attr.columns[0]
        limit = getattr(col.type, "length", None)
        if not limit or not isinstance(col.type, String):
            continue
        value = getattr(target, attr.key, None)
        if isinstance(value, str) and len(value) > limit:
            raise StringWidthExceeded(
                f"{mapper.local_table.name}.{col.name} is VARCHAR({limit}) but the "
                f"value is {len(value)} characters. SQLite would store this; "
                f"PostgreSQL rejects it with StringDataRightTruncation. "
                f"Value begins: {value[:60]!r}"
            )


event.listen(Base, "before_insert", _enforce_string_widths, propagate=True)
event.listen(Base, "before_update", _enforce_string_widths, propagate=True)


@pytest.fixture()
def session() -> Session:
    engine = create_engine("sqlite://", future=True)

    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_conn, _record):
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, future=True)()


def seed_synthetic_league(
    session: Session, seasons: range = range(2020, 2026), noise_seed: int = 42
) -> None:
    """Deterministic league: score = 21 + strength + home edge + noise."""
    rng = random.Random(noise_seed)
    for code in TEAMS:
        session.merge(Team(id=code, name=f"Team {code}"))
    session.flush()
    for season in seasons:
        week = 0
        # simple double round-robin, 14 weeks
        for rnd in range(2):
            order = TEAMS if rnd == 0 else list(reversed(TEAMS))
            for shift in range(len(TEAMS) - 1):
                week += 1
                rotated = [order[0], *order[1:][shift:], *order[1:][:shift]]
                half = len(rotated) // 2
                for i in range(half):
                    home, away = rotated[i], rotated[-(i + 1)]
                    kickoff = datetime(season, 9, 1, 17, tzinfo=UTC) + timedelta(weeks=week - 1)
                    hs = max(0, round(21 + STRENGTH[home] + 2.0 + rng.gauss(0, 9)))
                    as_ = max(0, round(21 + STRENGTH[away] + rng.gauss(0, 9)))
                    gid = f"{season}_{week:02d}_{away}_{home}"
                    observed = kickoff + timedelta(hours=4, minutes=30)
                    session.add(
                        Game(
                            id=gid, season=season, week=week, game_type="REG", kickoff_utc=kickoff,
                            home_team_id=home, away_team_id=away, home_score=hs, away_score=as_,
                            overtime=False, home_rest_days=7, away_rest_days=7, div_game=False,
                            result_observed_at=observed,
                        )
                    )
                    for team, own, opp in ((home, hs, as_), (away, as_, hs)):
                        session.add(
                            TeamGameStat(
                                game_id=gid, team_id=team, season=season, week=week,
                                metrics={
                                    "epa_per_play": (own - opp) / 30 + rng.gauss(0, 0.03),
                                    "epa_per_dropback": (own - opp) / 40 + rng.gauss(0, 0.04),
                                    "rush_epa_per_play": rng.gauss(0, 0.05),
                                    "success_rate": 0.45 + (own - opp) / 200,
                                    "early_down_epa": rng.gauss(0, 0.05),
                                    "neutral_epa": rng.gauss(0, 0.05),
                                    "explosive_rate": 0.08 + rng.random() * 0.04,
                                    "sack_rate": 0.06, "int_rate": 0.02, "dropbacks": 35.0,
                                    "drives": 11.0, "plays": 63.0, "red_zone_plays": 9.0,
                                    "red_zone_td": 2.0, "st_epa_total": rng.gauss(0, 1),
                                    "st_plays": 20.0,
                                },
                                observed_at=observed,
                            )
                        )
                    # closing benchmark: true expectation + small market noise
                    true_margin = STRENGTH[home] - STRENGTH[away] + 2.0
                    close_line = -round((true_margin + rng.gauss(0, 1)) * 2) / 2
                    session.add(
                        OddsSnapshot(
                            game_id=gid, book="synthetic-close", market="SPREAD", line=close_line,
                            home_price_american=-110, away_price_american=-110,
                            snapshot_kind="CLOSING_BENCHMARK", observed_at=kickoff,
                        )
                    )
                    session.add(
                        OddsSnapshot(
                            game_id=gid, book="synthetic-close", market="TOTAL",
                            line=round((44 + rng.gauss(0, 1.5)) * 2) / 2,
                            over_price_american=-110, under_price_american=-110,
                            snapshot_kind="CLOSING_BENCHMARK", observed_at=kickoff,
                        )
                    )
                    session.add(
                        OddsSnapshot(
                            game_id=gid, book="synthetic-close", market="MONEYLINE",
                            home_price_american=-130 if true_margin > 0 else 110,
                            away_price_american=110 if true_margin > 0 else -130,
                            snapshot_kind="CLOSING_BENCHMARK", observed_at=kickoff,
                        )
                    )
    session.commit()


@pytest.fixture()
def league_session(session: Session) -> Session:
    seed_synthetic_league(session)
    return session

# Hypothesis's per-example deadline measures wall time, which varies with
# load when the whole suite runs in parallel with other work. These are
# pure-math property tests, so a slow example is not a failure signal.
settings.register_profile("fde", deadline=None)
settings.load_profile("fde")
