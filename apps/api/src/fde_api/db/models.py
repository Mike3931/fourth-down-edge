"""Canonical relational model.

Conventions:
  * Natural keys from nflverse where stable (game_id, GSIS player id,
    team abbreviation); surrogate string PKs elsewhere.
  * Every point-in-time-relevant row carries observed_at; append-only
    tables are guarded at the service layer (and by trigger in Postgres).
  * JSON columns hold typed payloads validated by Pydantic at the edges;
    the schema stays portable across SQLite (dev) and PostgreSQL (prod).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, ClassVar

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    type_annotation_map: ClassVar = {dict[str, Any]: JSON, datetime: DateTime(timezone=True)}


# --------------------------------------------------------------------------- #
# Reference entities
# --------------------------------------------------------------------------- #


class Team(Base):
    __tablename__ = "teams"
    id: Mapped[str] = mapped_column(String(8), primary_key=True)  # e.g. "KC"
    name: Mapped[str] = mapped_column(String(80))
    conference: Mapped[str | None] = mapped_column(String(8))
    division: Mapped[str | None] = mapped_column(String(16))


class Stadium(Base):
    __tablename__ = "stadiums"
    id: Mapped[str] = mapped_column(String(16), primary_key=True)  # nflverse stadium_id
    name: Mapped[str] = mapped_column(String(120))
    roof: Mapped[str | None] = mapped_column(String(16))
    surface: Mapped[str | None] = mapped_column(String(32))
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    timezone: Mapped[str | None] = mapped_column(String(48))


class Player(Base):
    __tablename__ = "players"
    id: Mapped[str] = mapped_column(String(16), primary_key=True)  # GSIS id "00-00xxxxx"
    name: Mapped[str] = mapped_column(String(120))
    position: Mapped[str | None] = mapped_column(String(8))
    birth_date: Mapped[str | None] = mapped_column(String(10))
    first_season: Mapped[int | None] = mapped_column(Integer)


class Official(Base):
    __tablename__ = "officials"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)  # normalized referee name
    name: Mapped[str] = mapped_column(String(120))


class ProviderEntityMap(Base):
    """Explicit provider-id → canonical-id mapping. Players are mapped by
    provider id (GSIS/PFR/ESPN...), never by display name alone."""

    __tablename__ = "provider_entity_map"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(32))
    entity_type: Mapped[str] = mapped_column(String(24))  # team | player | stadium | official
    provider_id: Mapped[str] = mapped_column(String(80))
    canonical_id: Mapped[str] = mapped_column(String(80))
    __table_args__ = (UniqueConstraint("provider", "entity_type", "provider_id"),)


# --------------------------------------------------------------------------- #
# Games and per-game facts
# --------------------------------------------------------------------------- #


class Game(Base):
    __tablename__ = "games"
    id: Mapped[str] = mapped_column(String(24), primary_key=True)  # nflverse "2023_01_DET_KC"
    season: Mapped[int] = mapped_column(Integer, index=True)
    week: Mapped[int] = mapped_column(Integer)
    game_type: Mapped[str] = mapped_column(String(8))  # REG | WC | DIV | CON | SB
    kickoff_utc: Mapped[datetime | None] = mapped_column()
    home_team_id: Mapped[str] = mapped_column(ForeignKey("teams.id"))
    away_team_id: Mapped[str] = mapped_column(ForeignKey("teams.id"))
    home_score: Mapped[int | None] = mapped_column(Integer)
    away_score: Mapped[int | None] = mapped_column(Integer)
    overtime: Mapped[bool | None] = mapped_column(Boolean)
    stadium_id: Mapped[str | None] = mapped_column(ForeignKey("stadiums.id"))
    roof: Mapped[str | None] = mapped_column(String(16))
    surface: Mapped[str | None] = mapped_column(String(32))
    temp_f: Mapped[float | None] = mapped_column(Float)
    wind_mph: Mapped[float | None] = mapped_column(Float)
    referee_id: Mapped[str | None] = mapped_column(ForeignKey("officials.id"))
    home_rest_days: Mapped[int | None] = mapped_column(Integer)
    away_rest_days: Mapped[int | None] = mapped_column(Integer)
    div_game: Mapped[bool | None] = mapped_column(Boolean)
    home_qb_id: Mapped[str | None] = mapped_column(String(16))
    away_qb_id: Mapped[str | None] = mapped_column(String(16))
    home_coach: Mapped[str | None] = mapped_column(String(80))
    away_coach: Mapped[str | None] = mapped_column(String(80))
    # Result availability instant: scores may only enter features/settlement
    # when observed_at <= as_of_at. Approximated as kickoff + 4h30m for
    # historical data (documented limitation; the true publish instant is
    # not recorded by the source).
    result_observed_at: Mapped[datetime | None] = mapped_column()
    source_manifest_version: Mapped[str | None] = mapped_column(String(64))
    __table_args__ = (Index("ix_games_season_week", "season", "week"),)


class RosterEntry(Base):
    __tablename__ = "roster_entries"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    season: Mapped[int] = mapped_column(Integer, index=True)
    team_id: Mapped[str] = mapped_column(ForeignKey("teams.id"))
    player_id: Mapped[str] = mapped_column(ForeignKey("players.id"))
    position: Mapped[str | None] = mapped_column(String(8))
    status: Mapped[str | None] = mapped_column(String(16))
    depth_chart_order: Mapped[int | None] = mapped_column(Integer)
    observed_at: Mapped[datetime | None] = mapped_column()
    __table_args__ = (UniqueConstraint("season", "team_id", "player_id"),)


class ParticipationRecord(Base):
    """Per-game player participation. Table exists per contract; historical
    fills depend on source availability and are tracked via data-quality
    events rather than silently missing."""

    __tablename__ = "participation_records"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    game_id: Mapped[str] = mapped_column(ForeignKey("games.id"))
    player_id: Mapped[str] = mapped_column(ForeignKey("players.id"))
    team_id: Mapped[str] = mapped_column(ForeignKey("teams.id"))
    snap_share: Mapped[float | None] = mapped_column(Float)
    observed_at: Mapped[datetime | None] = mapped_column()
    __table_args__ = (UniqueConstraint("game_id", "player_id"),)


class InjuryReport(Base):
    """Point-in-time injury designations. Historical PIT injury data are not
    reliably available from free sources; rows only enter via forward capture
    or a licensed feed. Absence is surfaced as DATA INCOMPLETE, never
    backfilled from retrospective knowledge."""

    __tablename__ = "injury_reports"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    season: Mapped[int] = mapped_column(Integer)
    week: Mapped[int] = mapped_column(Integer)
    team_id: Mapped[str] = mapped_column(ForeignKey("teams.id"))
    player_id: Mapped[str] = mapped_column(ForeignKey("players.id"))
    practice_status: Mapped[str | None] = mapped_column(String(32))
    game_status: Mapped[str | None] = mapped_column(String(32))
    observed_at: Mapped[datetime] = mapped_column()


class WeatherSnapshot(Base):
    __tablename__ = "weather_snapshots"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    game_id: Mapped[str] = mapped_column(ForeignKey("games.id"))
    provider: Mapped[str] = mapped_column(String(32))
    temp_f: Mapped[float | None] = mapped_column(Float)
    wind_mph: Mapped[float | None] = mapped_column(Float)
    wind_gust_mph: Mapped[float | None] = mapped_column(Float)
    precip_prob: Mapped[float | None] = mapped_column(Float)
    forecast_for: Mapped[datetime | None] = mapped_column()
    observed_at: Mapped[datetime] = mapped_column()


class OddsSnapshot(Base):
    """A timestamped market quote. snapshot_kind separates evaluation-only
    closing benchmarks from tradeable observations."""

    __tablename__ = "odds_snapshots"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    game_id: Mapped[str] = mapped_column(ForeignKey("games.id"), index=True)
    book: Mapped[str] = mapped_column(String(48))  # "nflverse-consensus" | "bet365-manual" | ...
    market: Mapped[str] = mapped_column(String(16))  # SPREAD | TOTAL | MONEYLINE
    line: Mapped[float | None] = mapped_column(Float)  # home-relative for SPREAD
    home_price_american: Mapped[int | None] = mapped_column(Integer)
    away_price_american: Mapped[int | None] = mapped_column(Integer)
    over_price_american: Mapped[int | None] = mapped_column(Integer)
    under_price_american: Mapped[int | None] = mapped_column(Integer)
    snapshot_kind: Mapped[str] = mapped_column(String(24))  # CLOSING_BENCHMARK | MANUAL_ENTRY | FEED
    observed_at: Mapped[datetime] = mapped_column()
    source_manifest_version: Mapped[str | None] = mapped_column(String(64))
    __table_args__ = (Index("ix_odds_game_market", "game_id", "market"),)


class TeamGameStat(Base):
    """Per-team per-game aggregates derived from play-by-play. observed_at is
    the result-availability instant of the game (post-final)."""

    __tablename__ = "team_game_stats"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    game_id: Mapped[str] = mapped_column(ForeignKey("games.id"), index=True)
    team_id: Mapped[str] = mapped_column(ForeignKey("teams.id"))
    season: Mapped[int] = mapped_column(Integer, index=True)
    week: Mapped[int] = mapped_column(Integer)
    metrics: Mapped[dict[str, Any]] = mapped_column()  # epa/play, success rate, ... (typed at edges)
    observed_at: Mapped[datetime] = mapped_column()
    __table_args__ = (UniqueConstraint("game_id", "team_id"),)


class DataQualityEvent(Base):
    __tablename__ = "data_quality_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    severity: Mapped[str] = mapped_column(String(8))  # INFO | WARN | ERROR
    scope: Mapped[str] = mapped_column(String(48))
    message: Mapped[str] = mapped_column(Text)
    context: Mapped[dict[str, Any] | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column()


class IngestionRun(Base):
    """DB mirror of raw-store manifests for queryability; the filesystem
    manifest remains the source of truth."""

    __tablename__ = "ingestion_runs"
    version_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    provider: Mapped[str] = mapped_column(String(32))
    dataset: Mapped[str] = mapped_column(String(48))
    params: Mapped[dict[str, Any]] = mapped_column()
    payload_sha256: Mapped[str | None] = mapped_column(String(64))
    schema_version: Mapped[str] = mapped_column(String(48))
    code_commit: Mapped[str] = mapped_column(String(48))
    row_count: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(12))
    error: Mapped[str | None] = mapped_column(Text)
    source_version: Mapped[str | None] = mapped_column(String(128))
    source_updated_at: Mapped[str | None] = mapped_column(String(48))
    observed_at: Mapped[datetime] = mapped_column()
    ingested_at: Mapped[datetime] = mapped_column()


# --------------------------------------------------------------------------- #
# Feature snapshots, models, predictions, evaluation
# --------------------------------------------------------------------------- #


class FeatureSnapshot(Base):
    __tablename__ = "feature_snapshots"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    game_id: Mapped[str] = mapped_column(ForeignKey("games.id"), index=True)
    feature_set: Mapped[str] = mapped_column(String(32))  # "nfl-core-v1"
    horizon: Mapped[str] = mapped_column(String(24))  # PredictionHorizon value
    as_of_at: Mapped[datetime] = mapped_column()
    values: Mapped[dict[str, Any]] = mapped_column()
    unavailable_features: Mapped[dict[str, Any]] = mapped_column()  # name -> reason
    content_hash: Mapped[str] = mapped_column(String(64))
    code_commit: Mapped[str] = mapped_column(String(48))
    created_at: Mapped[datetime] = mapped_column()
    __table_args__ = (UniqueConstraint("game_id", "feature_set", "horizon", "as_of_at"),)


class ModelVersion(Base):
    __tablename__ = "model_versions"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)  # "market-benchmark-v1"
    name: Mapped[str] = mapped_column(String(80))
    target: Mapped[str] = mapped_column(String(48))
    algorithm: Mapped[str] = mapped_column(String(80))
    train_window: Mapped[str | None] = mapped_column(String(48))
    validation_window: Mapped[str | None] = mapped_column(String(48))
    test_window: Mapped[str | None] = mapped_column(String(48))
    feature_set: Mapped[str | None] = mapped_column(String(32))
    calibration_version: Mapped[str | None] = mapped_column(String(64))
    hyperparameters: Mapped[dict[str, Any]] = mapped_column()
    random_seed: Mapped[int | None] = mapped_column(Integer)
    dataset_hashes: Mapped[dict[str, Any]] = mapped_column()
    artifact_hash: Mapped[str | None] = mapped_column(String(64))
    code_commit: Mapped[str] = mapped_column(String(48))
    dependency_lock_hash: Mapped[str] = mapped_column(String(64))
    metrics: Mapped[dict[str, Any]] = mapped_column()
    known_limitations: Mapped[str] = mapped_column(Text)
    approval_status: Mapped[str] = mapped_column(String(24), default="research_only")
    created_at: Mapped[datetime] = mapped_column()


class Prediction(Base):
    """Immutable. Later vintages are new rows; nothing here is updated."""

    __tablename__ = "predictions"
    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    game_id: Mapped[str] = mapped_column(ForeignKey("games.id"), index=True)
    model_version_id: Mapped[str] = mapped_column(ForeignKey("model_versions.id"))
    feature_snapshot_id: Mapped[str | None] = mapped_column(ForeignKey("feature_snapshots.id"))
    horizon: Mapped[str] = mapped_column(String(24))
    as_of_at: Mapped[datetime] = mapped_column()
    outputs: Mapped[dict[str, Any]] = mapped_column()  # GamePredictionOutputs schema
    created_at: Mapped[datetime] = mapped_column()
    __table_args__ = (UniqueConstraint("game_id", "model_version_id", "horizon", "as_of_at"),)


class PredictionComponent(Base):
    __tablename__ = "prediction_components"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    prediction_id: Mapped[str] = mapped_column(ForeignKey("predictions.id"), index=True)
    name: Mapped[str] = mapped_column(String(64))
    value: Mapped[float | None] = mapped_column(Float)
    detail: Mapped[dict[str, Any] | None] = mapped_column()


class ModelEvaluation(Base):
    __tablename__ = "model_evaluations"
    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    model_version_id: Mapped[str] = mapped_column(ForeignKey("model_versions.id"), index=True)
    scope: Mapped[str] = mapped_column(String(64))  # e.g. "test:2025", "val:2024:PREGAME"
    metrics: Mapped[dict[str, Any]] = mapped_column()
    sample_size: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column()


class CalibrationArtifact(Base):
    __tablename__ = "calibration_artifacts"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    method: Mapped[str] = mapped_column(String(24))  # none | platt | beta | isotonic
    target: Mapped[str] = mapped_column(String(48))
    fitted_on: Mapped[str] = mapped_column(String(64))  # prior OOF window description
    parameters: Mapped[dict[str, Any]] = mapped_column()
    sample_size: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column()


# --------------------------------------------------------------------------- #
# Backtesting and jobs
# --------------------------------------------------------------------------- #


class BacktestRun(Base):
    __tablename__ = "backtest_runs"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    config: Mapped[dict[str, Any]] = mapped_column()
    status: Mapped[str] = mapped_column(String(16))  # queued | running | finished | failed
    error: Mapped[str | None] = mapped_column(Text)
    metrics: Mapped[dict[str, Any] | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column()
    finished_at: Mapped[datetime | None] = mapped_column()


class BacktestRecommendation(Base):
    """Append-only: every evaluated opportunity is retained, including PASS
    and DATA INCOMPLETE. Statuses are research-grade only — the engine never
    emits a production BET."""

    __tablename__ = "backtest_recommendations"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    backtest_run_id: Mapped[str] = mapped_column(ForeignKey("backtest_runs.id"), index=True)
    game_id: Mapped[str] = mapped_column(ForeignKey("games.id"))
    prediction_id: Mapped[str | None] = mapped_column(String(96))
    market: Mapped[str] = mapped_column(String(16))
    selection: Mapped[str | None] = mapped_column(String(8))
    line: Mapped[float | None] = mapped_column(Float)
    price_american: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(24))  # RESEARCH_CANDIDATE | WATCH | PASS | DATA_INCOMPLETE
    reasons: Mapped[dict[str, Any]] = mapped_column()
    execution: Mapped[dict[str, Any] | None] = mapped_column()  # fill/no-fill, deterioration, vig...
    settlement: Mapped[dict[str, Any] | None] = mapped_column()  # result, pnl, clv vs fixed close
    as_of_at: Mapped[datetime] = mapped_column()
    created_at: Mapped[datetime] = mapped_column()


class Job(Base):
    """Long-running operations exposed by the API (ingest, features,
    backtests, prediction generation)."""

    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(String(32))
    params: Mapped[dict[str, Any]] = mapped_column()
    status: Mapped[str] = mapped_column(String(16))  # queued | running | finished | failed
    result: Mapped[dict[str, Any] | None] = mapped_column()
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column()
    started_at: Mapped[datetime | None] = mapped_column()
    finished_at: Mapped[datetime | None] = mapped_column()
