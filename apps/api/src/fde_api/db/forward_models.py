"""Phase 3 — forward-capture tables.

Every table here carries `data_mode` ('DEMO' | 'LIVE_RESEARCH') so that
demonstration and live-research records are separable by query rather
than by convention. Observation tables are append-only: a revision is a
NEW row that supersedes an earlier one, never an UPDATE.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Float,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from fde_api.db.models import Base


class DomainIdentityMixin:
    """Logical identity and content hashes, persisted beside the record.

    Two hashes because two different questions were being conflated: WHICH
    record this is (the slot), and WHAT it says. Same slot with the same
    content is a retry; same slot with different content is a
    contradiction. The unique index is on the LOGICAL identity, so the
    database is what decides which of two racing callers established the
    slot - a service-level pre-check cannot.

    The versions travel with the hashes because a stored hash is
    uninterpretable without the rules that produced it.
    """

    logical_identity_version: Mapped[str | None] = mapped_column(String(48))
    logical_identity_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    content_hash_version: Mapped[str | None] = mapped_column(String(48))
    content_hash: Mapped[str | None] = mapped_column(String(64))


class ForwardTestPolicyRecord(Base):
    """A frozen forward-test policy. Immutable: changing any rule requires a
    new policy_version, which starts a separate evaluation cohort."""

    __tablename__ = "forward_test_policies"
    policy_version: Mapped[str] = mapped_column(String(48), primary_key=True)
    policy_hash: Mapped[str] = mapped_column(String(64))
    schema_version: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict[str, Any]] = mapped_column()
    model_version: Mapped[str] = mapped_column(String(80))
    feature_set_version: Mapped[str] = mapped_column(String(32))
    calibration_version: Mapped[str | None] = mapped_column(String(64))
    start_date: Mapped[str] = mapped_column(String(10))
    end_date: Mapped[str] = mapped_column(String(10))
    code_commit: Mapped[str] = mapped_column(String(48))
    dependency_lock_hash: Mapped[str] = mapped_column(String(64))
    artifact_path: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column()


class ScheduleObservation(Base):
    """One observation of a scheduled game state. Kickoff changes,
    postponements, and cancellations append new rows; the originally
    observed state is never overwritten."""

    __tablename__ = "schedule_observations"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    data_mode: Mapped[str] = mapped_column(String(16), index=True)
    canonical_game_id: Mapped[str] = mapped_column(String(32), index=True)
    provider: Mapped[str] = mapped_column(String(32))
    provider_game_id: Mapped[str] = mapped_column(String(64))
    season: Mapped[int] = mapped_column(Integer, index=True)
    season_type: Mapped[str] = mapped_column(String(8))  # PRE | REG | POST
    week: Mapped[int] = mapped_column(Integer)
    home_team_id: Mapped[str] = mapped_column(String(8))
    away_team_id: Mapped[str] = mapped_column(String(8))
    kickoff_utc: Mapped[datetime | None] = mapped_column()
    venue_timezone: Mapped[str | None] = mapped_column(String(48))
    stadium_id: Mapped[str | None] = mapped_column(String(24))
    stadium_name: Mapped[str | None] = mapped_column(String(120))
    neutral_site: Mapped[bool] = mapped_column(Boolean, default=False)
    international: Mapped[bool] = mapped_column(Boolean, default=False)
    game_status: Mapped[str] = mapped_column(String(16))  # SCHEDULED|POSTPONED|CANCELLED
    content_hash: Mapped[str] = mapped_column(String(64))
    source_manifest_version: Mapped[str | None] = mapped_column(String(64))
    source_updated_at: Mapped[datetime | None] = mapped_column()
    observed_at: Mapped[datetime] = mapped_column(index=True)
    supersedes_id: Mapped[int | None] = mapped_column(Integer)
    change_summary: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (Index("ix_sched_obs_game_observed", "canonical_game_id", "observed_at"),)


class OddsQuote(Base):
    """A single timestamped sportsbook quote, exactly as received. Raw
    quotes are never replaced by consensus records."""

    __tablename__ = "odds_quotes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    data_mode: Mapped[str] = mapped_column(String(16), index=True)
    canonical_game_id: Mapped[str] = mapped_column(String(32), index=True)
    provider: Mapped[str] = mapped_column(String(32))
    # Where this quote actually came from. Without it a fixture payload is
    # indistinguishable from live market data at the row level, and no
    # downstream consumer can honour "fixture output is never live output".
    provider_mode: Mapped[str] = mapped_column(
        String(16), index=True, server_default="UNKNOWN_LEGACY"
    )
    provider_event_id: Mapped[str | None] = mapped_column(String(64))
    sportsbook: Mapped[str] = mapped_column(String(48))
    market: Mapped[str] = mapped_column(String(16))  # SPREAD|TOTAL|MONEYLINE
    selection: Mapped[str] = mapped_column(String(16))  # HOME|AWAY|OVER|UNDER
    line: Mapped[float | None] = mapped_column(Float)
    american: Mapped[int] = mapped_column(Integer)
    decimal_odds: Mapped[float] = mapped_column(Float)
    is_live: Mapped[bool] = mapped_column(Boolean, default=False)
    provider_timestamp: Mapped[datetime | None] = mapped_column()
    observed_at: Mapped[datetime] = mapped_column(index=True)
    request_id: Mapped[str | None] = mapped_column(String(64))
    raw_hash: Mapped[str] = mapped_column(String(64))
    __table_args__ = (
        # Idempotency: the same book/market/selection/line/price bearing the
        # same provider timestamp is ONE observation however many polls see it.
        UniqueConstraint(
            "canonical_game_id",
            "sportsbook",
            "market",
            "selection",
            "line",
            "american",
            "provider_timestamp",
            name="uq_odds_quote_identity",
        ),
        Index("ix_odds_game_market_observed", "canonical_game_id", "market", "observed_at"),
        CheckConstraint(
            "provider_mode IN ('FIXTURE', 'SANDBOX', 'LIVE', 'UNAVAILABLE', "
            "'KEY_MISSING', 'QUOTA_EXHAUSTED', 'MIXED', 'UNKNOWN_LEGACY')",
            name="ck_odds_quotes_provider_mode_vocabulary",
        ),
    )


class ConsensusSnapshot(Base, DomainIdentityMixin):
    """A versioned consensus computed from eligible raw quotes at an
    instant. Additive: never mutates or replaces the underlying quotes."""

    __tablename__ = "consensus_snapshots"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    data_mode: Mapped[str] = mapped_column(String(16), index=True)
    # Which experiment this belongs to. `data_mode` cannot answer that:
    # BURN_IN and OFFICIAL_FORWARD_TEST both write LIVE_RESEARCH, so a
    # logical identity built from the mode gives the two cohorts one slot.
    # No default, deliberately — a write that omits the cohort must fail
    # rather than inherit one.
    cohort: Mapped[str] = mapped_column(String(24), index=True)
    # The book minimum that admitted this snapshot. `eligible_books` says
    # how many showed up; this says how many were required, which is the
    # part a reader cannot reconstruct later — burn-in permits one.
    min_books_applied: Mapped[int] = mapped_column(Integer)
    canonical_game_id: Mapped[str] = mapped_column(String(32), index=True)
    market: Mapped[str] = mapped_column(String(16))
    method_version: Mapped[str] = mapped_column(String(32))
    # Derived from the constituent quotes, not supplied by the caller: a
    # consensus is only as live as its least-live input.
    provider_mode: Mapped[str] = mapped_column(
        String(16), index=True, server_default="UNKNOWN_LEGACY"
    )
    median_line: Mapped[float | None] = mapped_column(Float)
    home_price_american: Mapped[int | None] = mapped_column(Integer)
    away_price_american: Mapped[int | None] = mapped_column(Integer)
    over_price_american: Mapped[int | None] = mapped_column(Integer)
    under_price_american: Mapped[int | None] = mapped_column(Integer)
    no_vig_home_prob: Mapped[float | None] = mapped_column(Float)
    no_vig_over_prob: Mapped[float | None] = mapped_column(Float)
    eligible_books: Mapped[int] = mapped_column(Integer)
    line_dispersion: Mapped[float | None] = mapped_column(Float)
    price_dispersion: Mapped[float | None] = mapped_column(Float)
    oldest_quote_age_seconds: Mapped[int | None] = mapped_column(Integer)
    newest_quote_age_seconds: Mapped[int | None] = mapped_column(Integer)
    quote_ids: Mapped[dict[str, Any]] = mapped_column()  # lineage to raw quotes
    is_closing_capture: Mapped[bool] = mapped_column(Boolean, default=False)
    observed_at: Mapped[datetime] = mapped_column(index=True)
    __table_args__ = (
        # The logical-identity constraint is declared HERE as well as in
        # migration b7e2f9c41a68. The migration alone is not enough: every
        # test database is built by `Base.metadata.create_all`, which reads
        # the model and not the migration history - so the constraint was
        # absent everywhere except a migrated database, and four concurrent
        # callers all reported CREATED. The PostgreSQL race gate is what
        # surfaced it; sequential tests could not, because they never
        # contend.
        UniqueConstraint(
            "logical_identity_version",
            "logical_identity_hash",
            name="uq_consensus_snapshots_logical_identity",
        ),
        CheckConstraint(
            "cohort IN ('fixture', 'demo', 'burn_in', 'official_forward_test', "
            "'unknown_legacy')",
            name="ck_consensus_snapshots_cohort_vocabulary",
        ),

        Index("ix_consensus_game_market_observed", "canonical_game_id", "market", "observed_at"),
        CheckConstraint(
            "provider_mode IN ('FIXTURE', 'SANDBOX', 'LIVE', 'UNAVAILABLE', "
            "'KEY_MISSING', 'QUOTA_EXHAUSTED', 'MIXED', 'UNKNOWN_LEGACY')",
            name="ck_consensus_snapshots_provider_mode_vocabulary",
        ),
    )


class ManualBookPriceEntry(Base, DomainIdentityMixin):
    """A price the user personally observed at bet365 and typed in. The
    software never retrieves, refreshes, or communicates with bet365."""

    __tablename__ = "manual_book_price_entries"
    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    data_mode: Mapped[str] = mapped_column(String(16), index=True)
    canonical_game_id: Mapped[str] = mapped_column(String(32), index=True)
    market: Mapped[str] = mapped_column(String(16))
    selection: Mapped[str] = mapped_column(String(16))
    line: Mapped[float | None] = mapped_column(Float)
    american: Mapped[int] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(24), default="manual_bet365")
    observed_at: Mapped[datetime] = mapped_column()  # when the USER saw it
    entered_at: Mapped[datetime] = mapped_column()  # when it was typed
    confirmed_at: Mapped[datetime | None] = mapped_column()
    superseded_by_id: Mapped[str | None] = mapped_column(String(48))
    correction_of_id: Mapped[str | None] = mapped_column(String(48))
    correction_reason: Mapped[str | None] = mapped_column(Text)
    user_id: Mapped[str] = mapped_column(String(64))

    # Governance. A manually entered price is the one record in the chain
    # with no provider to vouch for it, so it carries MORE provenance than a
    # captured quote, not less.
    cohort: Mapped[str] = mapped_column(
        String(24), default="fixture", server_default="fixture", index=True
    )
    provider_mode: Mapped[str] = mapped_column(
        String(16), default="UNKNOWN_LEGACY", server_default="UNKNOWN_LEGACY"
    )
    policy_version: Mapped[str | None] = mapped_column(String(32))
    code_commit: Mapped[str | None] = mapped_column(String(48))
    # Stored, not recomputed on read: the record states the arithmetic it was
    # evaluated under, so a later change to the conversion cannot silently
    # restate history.
    decimal_odds: Mapped[float | None] = mapped_column(Float)
    break_even_probability: Mapped[float | None] = mapped_column(Float)

    __table_args__ = (
        # The logical-identity constraint is declared HERE as well as in
        # migration b7e2f9c41a68. The migration alone is not enough: every
        # test database is built by `Base.metadata.create_all`, which reads
        # the model and not the migration history - so the constraint was
        # absent everywhere except a migrated database, and four concurrent
        # callers all reported CREATED. The PostgreSQL race gate is what
        # surfaced it; sequential tests could not, because they never
        # contend.
        UniqueConstraint(
            "logical_identity_version",
            "logical_identity_hash",
            name="uq_manual_book_price_entries_logical_identity",
        ),
    )

class Venue(Base):
    """Governed stadium reference. Roof type and `weather_applicable`
    decide whether forecasts matter; verification status is explicit."""

    __tablename__ = "venues"
    id: Mapped[str] = mapped_column(String(24), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    timezone: Mapped[str | None] = mapped_column(String(48))
    roof_type: Mapped[str] = mapped_column(String(24))  # OUTDOOR|DOME|RETRACTABLE|UNKNOWN
    surface: Mapped[str | None] = mapped_column(String(32))
    elevation_ft: Mapped[float | None] = mapped_column(Float)
    weather_applicable: Mapped[bool] = mapped_column(Boolean, default=True)
    country: Mapped[str] = mapped_column(String(8), default="US")
    source: Mapped[str] = mapped_column(String(64))
    verification_status: Mapped[str] = mapped_column(String(24))  # VERIFIED|UNVERIFIED
    updated_at: Mapped[datetime] = mapped_column()


class WeatherForecastVintage(Base):
    """One NWS forecast vintage for a game. Every vintage is retained; an
    updated forecast is a new row, never an overwrite."""

    __tablename__ = "weather_forecast_vintages"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    data_mode: Mapped[str] = mapped_column(String(16), index=True)
    canonical_game_id: Mapped[str] = mapped_column(String(32), index=True)
    provider: Mapped[str] = mapped_column(String(24), default="nws")
    office: Mapped[str | None] = mapped_column(String(8))
    grid_x: Mapped[int | None] = mapped_column(Integer)
    grid_y: Mapped[int | None] = mapped_column(Integer)
    forecast_period_start: Mapped[datetime | None] = mapped_column()
    forecast_period_end: Mapped[datetime | None] = mapped_column()
    temp_f: Mapped[float | None] = mapped_column(Float)
    wind_mph: Mapped[float | None] = mapped_column(Float)
    wind_gust_mph: Mapped[float | None] = mapped_column(Float)
    precip_probability: Mapped[float | None] = mapped_column(Float)
    precip_description: Mapped[str | None] = mapped_column(String(120))
    humidity: Mapped[float | None] = mapped_column(Float)
    narrative: Mapped[str | None] = mapped_column(Text)
    source_updated_at: Mapped[datetime | None] = mapped_column()  # NWS issuance
    observed_at: Mapped[datetime] = mapped_column(index=True)
    raw_hash: Mapped[str] = mapped_column(String(64))
    raw_artifact_path: Mapped[str | None] = mapped_column(Text)
    is_gameday_observation: Mapped[bool] = mapped_column(Boolean, default=False)


class RoofStateObservation(Base):
    """Roof state as known at an instant. Never inferred from the result or
    a postgame report."""

    __tablename__ = "roof_state_observations"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    data_mode: Mapped[str] = mapped_column(String(16), index=True)
    canonical_game_id: Mapped[str] = mapped_column(String(32), index=True)
    # OPEN|CLOSED|EXPECTED_OPEN|EXPECTED_CLOSED|UNKNOWN|NOT_APPLICABLE
    state: Mapped[str] = mapped_column(String(24))
    source_category: Mapped[str] = mapped_column(String(32))
    source_reference: Mapped[str | None] = mapped_column(Text)
    observed_at: Mapped[datetime] = mapped_column(index=True)
    entered_at: Mapped[datetime] = mapped_column()
    superseded_by_id: Mapped[int | None] = mapped_column(Integer)


class InjuryObservation(Base):
    """A prospective injury/availability observation. Append-only; a revised
    designation supersedes rather than overwrites."""

    __tablename__ = "injury_observations"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    data_mode: Mapped[str] = mapped_column(String(16), index=True)
    canonical_game_id: Mapped[str] = mapped_column(String(32), index=True)
    team_id: Mapped[str] = mapped_column(String(8))
    player_id: Mapped[str] = mapped_column(String(16))  # canonical GSIS id
    report_date: Mapped[str] = mapped_column(String(10))
    body_part: Mapped[str | None] = mapped_column(String(48))
    practice_status: Mapped[str | None] = mapped_column(String(24))  # DNP|LIMITED|FULL
    game_designation: Mapped[str | None] = mapped_column(String(24))  # OUT|DOUBTFUL|QUESTIONABLE|NONE
    # OFFICIAL_VERIFIED | LICENSED_PROVIDER | USER_VERIFIED | UNVERIFIED
    source_category: Mapped[str] = mapped_column(String(32), index=True)
    source_reference: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[str] = mapped_column(String(16))  # LOW|MEDIUM|HIGH
    verification_status: Mapped[str] = mapped_column(String(24))
    source_updated_at: Mapped[datetime | None] = mapped_column()
    observed_at: Mapped[datetime] = mapped_column(index=True)
    entered_at: Mapped[datetime] = mapped_column()
    superseded_by_id: Mapped[int | None] = mapped_column(Integer)
    user_id: Mapped[str | None] = mapped_column(String(64))


class AvailabilityAssessment(Base, DomainIdentityMixin):
    """Rules-based availability state derived from injury observations at a
    cutoff. Ranges, not false precision."""

    __tablename__ = "availability_assessments"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    data_mode: Mapped[str] = mapped_column(String(16), index=True)
    cohort: Mapped[str] = mapped_column(String(24), index=True)
    canonical_game_id: Mapped[str] = mapped_column(String(32), index=True)
    team_id: Mapped[str] = mapped_column(String(8))
    player_id: Mapped[str] = mapped_column(String(16))
    position: Mapped[str | None] = mapped_column(String(8))
    state: Mapped[str] = mapped_column(String(32))
    active_prob_low: Mapped[float] = mapped_column(Float)
    active_prob_high: Mapped[float] = mapped_column(Float)
    snap_share_low: Mapped[float | None] = mapped_column(Float)
    snap_share_high: Mapped[float | None] = mapped_column(Float)
    confidence_tier: Mapped[str] = mapped_column(String(16))
    source_freshness_hours: Mapped[float | None] = mapped_column(Float)
    reason: Mapped[str] = mapped_column(Text)
    missing_data: Mapped[bool] = mapped_column(Boolean, default=False)
    is_starting_qb: Mapped[bool] = mapped_column(Boolean, default=False)
    as_of_at: Mapped[datetime] = mapped_column(index=True)
    created_at: Mapped[datetime] = mapped_column()

    __table_args__ = (
        # The logical-identity constraint is declared HERE as well as in
        # migration b7e2f9c41a68. The migration alone is not enough: every
        # test database is built by `Base.metadata.create_all`, which reads
        # the model and not the migration history - so the constraint was
        # absent everywhere except a migrated database, and four concurrent
        # callers all reported CREATED. The PostgreSQL race gate is what
        # surfaced it; sequential tests could not, because they never
        # contend.
        UniqueConstraint(
            "logical_identity_version",
            "logical_identity_hash",
            name="uq_availability_assessments_logical_identity",
        ),
        CheckConstraint(
            "cohort IN ('fixture', 'demo', 'burn_in', 'official_forward_test', "
            "'unknown_legacy')",
            name="ck_availability_assessments_cohort_vocabulary",
        ),
    )

class ForwardPrediction(Base):
    """An immutable forward prediction vintage with full source lineage."""

    __tablename__ = "forward_predictions"
    id: Mapped[str] = mapped_column(String(120), primary_key=True)
    data_mode: Mapped[str] = mapped_column(String(16), index=True)
    canonical_game_id: Mapped[str] = mapped_column(String(32), index=True)
    horizon: Mapped[str] = mapped_column(String(40), index=True)
    as_of_at: Mapped[datetime] = mapped_column(index=True)
    model_version: Mapped[str] = mapped_column(String(80))
    calibration_version: Mapped[str | None] = mapped_column(String(64))
    feature_set_version: Mapped[str] = mapped_column(String(32))
    policy_version: Mapped[str] = mapped_column(String(48), index=True)
    lineage: Mapped[dict[str, Any]] = mapped_column()  # every source record used
    consensus_snapshot_id: Mapped[int | None] = mapped_column(Integer)
    weather_vintage_id: Mapped[int | None] = mapped_column(Integer)
    availability_snapshot: Mapped[dict[str, Any] | None] = mapped_column()
    expected_margin: Mapped[float | None] = mapped_column(Float)
    expected_total: Mapped[float | None] = mapped_column(Float)
    home_win_prob: Mapped[float | None] = mapped_column(Float)
    spread_cover_prob: Mapped[float | None] = mapped_column(Float)
    spread_push_prob: Mapped[float | None] = mapped_column(Float)
    total_over_prob: Mapped[float | None] = mapped_column(Float)
    total_push_prob: Mapped[float | None] = mapped_column(Float)
    margin_p10: Mapped[float | None] = mapped_column(Float)
    margin_p90: Mapped[float | None] = mapped_column(Float)
    total_p10: Mapped[float | None] = mapped_column(Float)
    total_p90: Mapped[float | None] = mapped_column(Float)
    data_completeness: Mapped[float] = mapped_column(Float)
    warnings: Mapped[dict[str, Any]] = mapped_column()
    artifact_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column()
    __table_args__ = (
        UniqueConstraint(
            "canonical_game_id",
            "horizon",
            "model_version",
            "policy_version",
            name="uq_forward_prediction_vintage",
        ),
    )


class ForwardLedgerEntry(Base, DomainIdentityMixin):
    """The forward evaluation cohort. Retains every state including PASS and
    DATA_INCOMPLETE; never merged with historical backtest rows."""

    __tablename__ = "forward_ledger"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    data_mode: Mapped[str] = mapped_column(String(16), index=True)
    cohort: Mapped[str] = mapped_column(String(24), index=True)
    canonical_game_id: Mapped[str] = mapped_column(String(32), index=True)
    forward_prediction_id: Mapped[str | None] = mapped_column(String(120))
    policy_version: Mapped[str] = mapped_column(String(48), index=True)
    model_version: Mapped[str] = mapped_column(String(80))
    horizon: Mapped[str] = mapped_column(String(40))
    market: Mapped[str] = mapped_column(String(16))
    selection: Mapped[str | None] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(24), index=True)
    qualifying_line: Mapped[float | None] = mapped_column(Float)
    qualifying_american: Mapped[int | None] = mapped_column(Integer)
    price_source: Mapped[str | None] = mapped_column(String(32))
    price_age_seconds: Mapped[int | None] = mapped_column(Integer)
    model_probability: Mapped[float | None] = mapped_column(Float)
    conservative_probability: Mapped[float | None] = mapped_column(Float)
    break_even_probability: Mapped[float | None] = mapped_column(Float)
    expected_value: Mapped[float | None] = mapped_column(Float)
    simulated_line: Mapped[float | None] = mapped_column(Float)
    simulated_american: Mapped[int | None] = mapped_column(Integer)
    simulated_delay_seconds: Mapped[int | None] = mapped_column(Integer)
    filled: Mapped[bool | None] = mapped_column(Boolean)
    fill_note: Mapped[str | None] = mapped_column(Text)
    closing_line: Mapped[float | None] = mapped_column(Float)
    closing_american: Mapped[int | None] = mapped_column(Integer)
    closing_no_vig_prob: Mapped[float | None] = mapped_column(Float)
    clv_line: Mapped[float | None] = mapped_column(Float)
    clv_probability: Mapped[float | None] = mapped_column(Float)
    result: Mapped[str | None] = mapped_column(String(12))
    pnl_units: Mapped[float | None] = mapped_column(Float)
    data_completeness: Mapped[float | None] = mapped_column(Float)
    exclusion_reason: Mapped[str | None] = mapped_column(Text)
    reasons: Mapped[dict[str, Any]] = mapped_column()
    as_of_at: Mapped[datetime] = mapped_column()
    created_at: Mapped[datetime] = mapped_column()
    settled_at: Mapped[datetime | None] = mapped_column()

    __table_args__ = (
        # The logical-identity constraint is declared HERE as well as in
        # migration b7e2f9c41a68. The migration alone is not enough: every
        # test database is built by `Base.metadata.create_all`, which reads
        # the model and not the migration history - so the constraint was
        # absent everywhere except a migrated database, and four concurrent
        # callers all reported CREATED. The PostgreSQL race gate is what
        # surfaced it; sequential tests could not, because they never
        # contend.
        UniqueConstraint(
            "logical_identity_version",
            "logical_identity_hash",
            name="uq_forward_ledger_logical_identity",
        ),
        CheckConstraint(
            "cohort IN ('fixture', 'demo', 'burn_in', 'official_forward_test', "
            "'unknown_legacy')",
            name="ck_forward_ledger_cohort_vocabulary",
        ),
    )

class ScheduledJobRun(Base):
    """One execution of a scheduled job. `idempotency_key` is unique so a
    retry can never duplicate odds, predictions, or settlements."""

    __tablename__ = "scheduled_job_runs"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    job_kind: Mapped[str] = mapped_column(String(48), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(160), unique=True)
    data_mode: Mapped[str] = mapped_column(String(16))
    # The provider mode this run executed under. Persisted so a recovery can
    # be refused when it would change the mode - a fixture capture must not
    # be recovered as a live one. Backfilled rows are UNKNOWN_LEGACY, which
    # is a reason to require review, not permission to proceed.
    provider_mode: Mapped[str] = mapped_column(
        String(16), default="UNKNOWN_LEGACY", server_default="UNKNOWN_LEGACY", index=True
    )
    scheduled_for: Mapped[datetime | None] = mapped_column()
    started_at: Mapped[datetime | None] = mapped_column()
    completed_at: Mapped[datetime | None] = mapped_column()
    # Legacy single-state column, retained so historical rows stay queryable
    # and so the migration mapping can be audited after the fact.
    status: Mapped[str] = mapped_column(String(16))  # queued|running|finished|failed|skipped

    # Two independent axes, promoted out of free text into typed columns.
    # These are AUTHORITATIVE; `status` above is compatibility-only.
    job_outcome: Mapped[str | None] = mapped_column(String(24), index=True)
    domain_state: Mapped[str | None] = mapped_column(String(24), index=True)

    # Where the row's state came from. Enforced against domain_state by a
    # CHECK constraint so no ORM path, bulk insert, or raw SQL can create a
    # live row claiming unreconstructable history.
    state_origin: Mapped[str] = mapped_column(String(20), default="LIVE", index=True)

    # --- recovery lineage -------------------------------------------------
    root_run_id: Mapped[str | None] = mapped_column(String(64), index=True)
    recovery_of_run_id: Mapped[str | None] = mapped_column(String(64))
    recovery_sequence: Mapped[int] = mapped_column(Integer, default=0)
    logical_slot: Mapped[datetime | None] = mapped_column()
    original_idempotency_key: Mapped[str | None] = mapped_column(String(160))
    recovery_reason: Mapped[str | None] = mapped_column(Text)
    reconciled_at: Mapped[datetime | None] = mapped_column()
    prior_effects_detected: Mapped[bool | None] = mapped_column(Boolean)
    replay_decision: Mapped[str | None] = mapped_column(String(40))
    administrative_override: Mapped[bool] = mapped_column(Boolean, default=False)
    override_operator: Mapped[str | None] = mapped_column(String(80))
    override_reason: Mapped[str | None] = mapped_column(Text)

    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    provider_calls: Mapped[int] = mapped_column(Integer, default=0)
    records_received: Mapped[int] = mapped_column(Integer, default=0)
    records_written: Mapped[int] = mapped_column(Integer, default=0)
    error_summary: Mapped[str | None] = mapped_column(Text)
    code_commit: Mapped[str] = mapped_column(String(48))
    created_at: Mapped[datetime] = mapped_column()
    __table_args__ = (
        # UNKNOWN_LEGACY is reconstructable-history only. A LIVE row may never
        # carry it, whatever writes the row.
        CheckConstraint(
            "domain_state <> 'UNKNOWN_LEGACY' OR state_origin = 'MIGRATED_LEGACY'",
            name="ck_unknown_legacy_requires_migrated_origin",
        ),
        CheckConstraint(
            "state_origin IN ('LIVE', 'MIGRATED_LEGACY')",
            name="ck_state_origin_vocabulary",
        ),
        # One active member per (root, logical slot): no parallel branches.
        Index("ix_run_root_slot", "root_run_id", "logical_slot"),
    )


class ProviderQuotaUsage(Base):
    """Provider call accounting so polling cadence can respect quotas."""

    __tablename__ = "provider_quota_usage"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(32), index=True)
    window_start: Mapped[datetime] = mapped_column()
    calls_used: Mapped[int] = mapped_column(Integer, default=0)
    calls_remaining: Mapped[int | None] = mapped_column(Integer)
    quota_limit: Mapped[int | None] = mapped_column(Integer)
    last_response_at: Mapped[datetime | None] = mapped_column()


class MigrationAudit(Base):
    """Record of a semantic reinterpretation performed by a migration.

    Kept so a state split can be explained after the fact: which rule ran,
    what the original value was, and whether the mapping was exact or a
    conservative default. Deliberately carries no payload content.
    """

    __tablename__ = "migration_audit"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    revision: Mapped[str] = mapped_column(String(48), index=True)
    # Distinguishes repeated upgrade cycles (upgrade -> downgrade -> upgrade)
    # so audit rows from different cycles are never indistinguishable.
    migration_cycle: Mapped[int] = mapped_column(Integer, default=1)
    table_name: Mapped[str] = mapped_column(String(64))
    record_id: Mapped[str] = mapped_column(String(96))
    original_value: Mapped[str | None] = mapped_column(String(48))
    new_outcome: Mapped[str | None] = mapped_column(String(24))
    new_domain_state: Mapped[str | None] = mapped_column(String(24))
    mapping_rule: Mapped[str] = mapped_column(String(120))
    exact: Mapped[bool] = mapped_column(Boolean)
    migrated_at: Mapped[datetime] = mapped_column()
    __table_args__ = (
        UniqueConstraint("revision", "migration_cycle", "table_name", "record_id",
                         name="uq_migration_audit_identity"),
    )


class ClosingCapture(Base):
    """The close, as an immutable record that REFERENCES a snapshot.

    The previous implementation flipped `is_closing_capture` on the chosen
    `ConsensusSnapshot`. That mutated a record whose whole contract is
    immutability, and it made the close unable to carry anything the
    snapshot did not already have: no rule version, no capture slot, no
    conflict reason, no explicit missing-close state. A close that cannot
    say "no eligible snapshot existed, and here is why" is a close that
    disappears when it is most informative.

    So the close is now its own entity. It points at a snapshot and never
    touches it. Its identity is (game, market, selection, cohort, rule
    version), which is what makes a repeat capture idempotent and a
    DIFFERENT selection under the same identity a conflict rather than an
    overwrite.
    """

    __tablename__ = "closing_captures"
    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    canonical_game_id: Mapped[str] = mapped_column(String(32), index=True)
    market: Mapped[str] = mapped_column(String(16))
    # Null for markets whose close is a single two-sided snapshot; set where
    # a selection genuinely has its own close.
    selection: Mapped[str | None] = mapped_column(String(16))

    # The referenced snapshot. Null when the status is MISSING: there was
    # nothing eligible to point at, and inventing a reference would be a
    # fabricated close.
    consensus_snapshot_id: Mapped[int | None] = mapped_column(Integer, index=True)

    # The rule is recorded WITH its version. Without the version a close
    # captured under one rule and one captured under a revised rule are
    # indistinguishable, so a rule change would silently restate history.
    selection_rule: Mapped[str] = mapped_column(Text)
    selection_rule_version: Mapped[str] = mapped_column(String(32), index=True)

    scheduled_slot: Mapped[datetime | None] = mapped_column()
    captured_at: Mapped[datetime] = mapped_column()

    cohort: Mapped[str] = mapped_column(String(24), index=True)
    data_mode: Mapped[str] = mapped_column(String(16), index=True)
    provider_mode: Mapped[str] = mapped_column(String(16))
    policy_version: Mapped[str | None] = mapped_column(String(32))
    code_commit: Mapped[str | None] = mapped_column(String(48))

    # CAPTURED | MISSING | CONFLICT
    status: Mapped[str] = mapped_column(String(16), index=True)
    missing_close_reason: Mapped[str | None] = mapped_column(Text)
    conflict_reason: Mapped[str | None] = mapped_column(Text)
    # The capture this one conflicts with. The original is never rewritten.
    conflicts_with_id: Mapped[str | None] = mapped_column(String(48))

    source_lineage: Mapped[dict[str, Any] | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column()

    __table_args__ = (
        Index(
            "ix_closing_capture_identity",
            "canonical_game_id", "market", "selection", "cohort", "selection_rule_version",
        ),
        CheckConstraint(
            "status IN ('CAPTURED', 'MISSING', 'CONFLICT')",
            name="ck_closing_captures_status_vocabulary",
        ),
        CheckConstraint(
            "cohort IN ('fixture', 'demo', 'burn_in', 'official_forward_test')",
            name="ck_closing_captures_cohort_vocabulary",
        ),
        # A CAPTURED close must reference a snapshot; a MISSING one must not.
        # Enforced in the database because a captured close with no
        # reference is unusable and a missing close with one is a lie.
        CheckConstraint(
            "(status = 'CAPTURED' AND consensus_snapshot_id IS NOT NULL) OR "
            "(status = 'MISSING' AND consensus_snapshot_id IS NULL) OR "
            "(status = 'CONFLICT')",
            name="ck_closing_captures_reference_matches_status",
        ),
        CheckConstraint(
            "status <> 'MISSING' OR missing_close_reason IS NOT NULL",
            name="ck_closing_captures_missing_has_reason",
        ),
        CheckConstraint(
            "status <> 'CONFLICT' OR conflict_reason IS NOT NULL",
            name="ck_closing_captures_conflict_has_reason",
        ),
    )
