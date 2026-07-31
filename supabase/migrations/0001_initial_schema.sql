-- Fourth Down Edge — initial schema.
-- All timestamps are UTC (timestamptz). Point-in-time integrity columns:
--   event_at            when the real-world event occurred
--   source_updated_at   when the upstream source updated the information
--   observed_at         when this application first observed it
--   ingested_at         when this application persisted it
--   as_of_at            prediction cutoff; predictions may only use records
--                       with observed_at <= as_of_at
-- Append-only financial history is enforced by triggers in 0003.

create extension if not exists pgcrypto;

-- ---------------------------------------------------------------------------
-- Users & settings
-- ---------------------------------------------------------------------------

create table users (
  id uuid primary key references auth.users (id) on delete cascade,
  email text not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table user_settings (
  user_id uuid primary key references users (id) on delete cascade,
  odds_format text not null default 'AMERICAN' check (odds_format in ('AMERICAN', 'DECIMAL')),
  timezone text not null default 'UTC',
  mode text not null default 'PAPER' check (mode in ('PAPER', 'REAL_TRACKING')),
  risk_controls jsonb not null default '{}'::jsonb,
  monthly_loss_budget numeric(12,2),
  real_tracking_acknowledged_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- ---------------------------------------------------------------------------
-- Ingestion & provenance
-- ---------------------------------------------------------------------------

create table providers (
  id text primary key,
  name text not null,
  kind text not null, -- schedule | roster | injury | weather | odds | officials | model
  is_critical boolean not null default false,
  created_at timestamptz not null default now()
);

create table ingestion_runs (
  id uuid primary key default gen_random_uuid(),
  provider_id text not null references providers (id),
  started_at timestamptz not null,
  finished_at timestamptz,
  status text not null check (status in ('RUNNING', 'SUCCESS', 'FAILED', 'PARTIAL')),
  record_count integer not null default 0,
  error text,
  created_at timestamptz not null default now()
);

create table raw_objects (
  id uuid primary key default gen_random_uuid(),
  ingestion_run_id uuid not null references ingestion_runs (id),
  provider_id text not null references providers (id),
  object_key text not null,
  payload jsonb not null,
  content_hash text not null,
  source_updated_at timestamptz,
  observed_at timestamptz not null,
  ingested_at timestamptz not null default now()
);

create table source_records (
  id uuid primary key default gen_random_uuid(),
  raw_object_id uuid references raw_objects (id),
  provider_id text not null references providers (id),
  source text not null,
  source_record_id text,
  entity_type text not null,
  normalized jsonb not null,
  event_at timestamptz,
  source_updated_at timestamptz,
  observed_at timestamptz not null,
  ingested_at timestamptz not null default now()
);

create table entity_mappings (
  id uuid primary key default gen_random_uuid(),
  provider_id text not null references providers (id),
  entity_type text not null,
  external_id text not null,
  internal_id text not null,
  valid_from timestamptz not null default now(),
  valid_to timestamptz,
  created_at timestamptz not null default now(),
  unique (provider_id, entity_type, external_id, valid_from)
);

-- ---------------------------------------------------------------------------
-- Reference entities
-- ---------------------------------------------------------------------------

create table teams (
  id text primary key,
  name text not null,           -- plain text only; no logos anywhere
  abbreviation text not null unique,
  conference text not null check (conference in ('AFC', 'NFC')),
  division text not null check (division in ('EAST', 'NORTH', 'SOUTH', 'WEST')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table stadiums (
  id text primary key,
  name text not null,
  city text not null,
  surface text not null check (surface in ('GRASS', 'TURF')),
  roof text not null check (roof in ('OUTDOOR', 'DOME', 'RETRACTABLE')),
  altitude_ft integer not null default 0,
  timezone text not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table officials (
  id text primary key,
  referee_name text not null,
  crew_penalty_rate numeric(5,2),
  crew_over_rate numeric(5,4),
  sample_games integer,
  source text,
  source_updated_at timestamptz,
  observed_at timestamptz,
  created_at timestamptz not null default now()
);

create table players (
  id text primary key,
  team_id text references teams (id),
  name text not null,
  position text not null,
  depth_role text,
  source text,
  source_record_id text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table games (
  id text primary key,
  season integer not null,
  week integer not null,
  kickoff_utc timestamptz not null,
  away_team_id text not null references teams (id),
  home_team_id text not null references teams (id),
  stadium_id text not null references stadiums (id),
  roof_status text not null check (roof_status in ('OUTDOOR', 'DOME', 'RETRACTABLE_OPEN', 'RETRACTABLE_CLOSED')),
  status text not null default 'SCHEDULED' check (status in ('SCHEDULED', 'IN_PROGRESS', 'FINAL', 'POSTPONED')),
  final_away_score integer,
  final_home_score integer,
  ended_at timestamptz,
  source text,
  source_record_id text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index games_season_week_idx on games (season, week);

create table game_officials (
  game_id text not null references games (id),
  official_id text not null references officials (id),
  role text not null default 'REFEREE',
  source text,
  observed_at timestamptz,
  primary key (game_id, official_id, role)
);

-- ---------------------------------------------------------------------------
-- Point-in-time snapshots
-- ---------------------------------------------------------------------------

create table roster_snapshots (
  id uuid primary key default gen_random_uuid(),
  team_id text not null references teams (id),
  as_of_at timestamptz not null,
  players jsonb not null,
  source text not null,
  source_record_id text,
  source_updated_at timestamptz,
  observed_at timestamptz not null,
  ingested_at timestamptz not null default now()
);
create index roster_snapshots_team_asof_idx on roster_snapshots (team_id, as_of_at);

create table depth_chart_snapshots (
  id uuid primary key default gen_random_uuid(),
  team_id text not null references teams (id),
  as_of_at timestamptz not null,
  chart jsonb not null,
  source text not null,
  source_updated_at timestamptz,
  observed_at timestamptz not null,
  ingested_at timestamptz not null default now()
);

create table participation (
  id uuid primary key default gen_random_uuid(),
  game_id text not null references games (id),
  player_id text not null references players (id),
  snaps integer,
  snap_share numeric(5,4),
  active boolean,
  event_at timestamptz,
  source text not null,
  source_updated_at timestamptz,
  observed_at timestamptz not null,
  ingested_at timestamptz not null default now()
);

create table injury_reports (
  id uuid primary key default gen_random_uuid(),
  game_id text not null references games (id),
  player_id text not null references players (id),
  injury text not null,
  practice_wed text check (practice_wed in ('FULL','LIMITED','DNP','NOT_LISTED','NO_DATA')),
  practice_thu text check (practice_thu in ('FULL','LIMITED','DNP','NOT_LISTED','NO_DATA')),
  practice_fri text check (practice_fri in ('FULL','LIMITED','DNP','NOT_LISTED','NO_DATA')),
  designation text check (designation in ('NONE','QUESTIONABLE','DOUBTFUL','OUT','IR')),
  conflict_warning text,
  event_at timestamptz,
  source text not null,
  source_record_id text,
  source_updated_at timestamptz not null,
  observed_at timestamptz not null,
  ingested_at timestamptz not null default now()
);
create index injury_reports_game_observed_idx on injury_reports (game_id, observed_at);

create table player_availability_snapshots (
  id uuid primary key default gen_random_uuid(),
  game_id text not null references games (id),
  player_id text not null references players (id),
  active_probability numeric(5,4) not null check (active_probability between 0 and 1),
  expected_snap_share_if_active numeric(5,4),
  restriction_probability numeric(5,4),
  replacement_player_id text references players (id),
  replacement_quality numeric(5,4),
  estimated_team_impact_pts numeric(5,2),
  confidence numeric(5,4),
  source text not null,
  source_updated_at timestamptz,
  observed_at timestamptz not null,
  ingested_at timestamptz not null default now()
);

create table weather_snapshots (
  id uuid primary key default gen_random_uuid(),
  game_id text not null references games (id),
  forecast_generated_at timestamptz not null,
  temperature_f numeric(5,1),
  wind_mph numeric(5,1),
  gust_mph numeric(5,1),
  precipitation_chance numeric(5,4),
  precipitation_type text check (precipitation_type in ('NONE','RAIN','SNOW','MIXED')),
  severity text check (severity in ('NONE','LOW','MODERATE','HIGH','CRITICAL')),
  source text not null,
  source_record_id text,
  source_updated_at timestamptz,
  observed_at timestamptz not null,
  ingested_at timestamptz not null default now()
);
create index weather_snapshots_game_observed_idx on weather_snapshots (game_id, observed_at);

create table odds_snapshots (
  id uuid primary key default gen_random_uuid(),
  game_id text not null references games (id),
  market text not null check (market in ('MONEYLINE','SPREAD','TOTAL')),
  book text not null,
  line numeric(6,2),
  away_american integer,
  home_american integer,
  over_american integer,
  under_american integer,
  is_opening boolean not null default false,
  is_closing boolean not null default false,
  source text not null,
  source_record_id text,
  source_updated_at timestamptz,
  observed_at timestamptz not null,
  ingested_at timestamptz not null default now()
);
create index odds_snapshots_game_market_observed_idx on odds_snapshots (game_id, market, observed_at);

-- Manual sportsbook prices: user-entered, immutable, append-only.
create table manual_book_prices (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references users (id) on delete cascade,
  game_id text not null references games (id),
  sportsbook text not null,
  market text not null check (market in ('MONEYLINE','SPREAD','TOTAL')),
  selection text not null check (selection in ('HOME','AWAY','OVER','UNDER')),
  line numeric(6,2),
  american integer not null check (abs(american) >= 100),
  price_observed_at timestamptz not null,
  entered_at timestamptz not null default now(),
  confirmed_visible boolean not null check (confirmed_visible = true)
);
create index manual_book_prices_user_game_idx on manual_book_prices (user_id, game_id, entered_at);

-- ---------------------------------------------------------------------------
-- Models & predictions
-- ---------------------------------------------------------------------------

create table feature_sets (
  id text primary key,
  version text not null,
  description text,
  schema jsonb,
  created_at timestamptz not null default now()
);

create table game_feature_snapshots (
  id text primary key,
  game_id text not null references games (id),
  feature_set_id text not null references feature_sets (id),
  as_of_at timestamptz not null,
  features jsonb not null,
  content_hash text not null,
  created_at timestamptz not null default now()
);
create index game_feature_snapshots_game_asof_idx on game_feature_snapshots (game_id, as_of_at);

create table model_versions (
  id text primary key,
  name text not null,
  version text not null,
  status text not null check (status in ('APPROVED_DEMO','PLACEHOLDER','IN_DEVELOPMENT','RETIRED')),
  target text not null,
  algorithm text not null,
  training_period text,
  validation_period text,
  feature_set_version text,
  calibration_version text,
  artifact_hash text,
  git_commit text,
  approved_at timestamptz,
  known_limitations jsonb not null default '[]'::jsonb,
  data_dependencies jsonb not null default '[]'::jsonb,
  prediction_horizons jsonb not null default '[]'::jsonb,
  drift_status text not null default 'UNKNOWN',
  is_placeholder boolean not null default false,
  performance_summary text,
  last_retrained_at timestamptz,
  next_review_at timestamptz,
  created_at timestamptz not null default now(),
  unique (name, version)
);

create table calibration_models (
  id text primary key,
  model_version_id text not null references model_versions (id),
  method text not null,
  coefficients jsonb not null,
  fitted_period text,
  created_at timestamptz not null default now()
);

create table predictions (
  id text primary key,
  game_id text not null references games (id),
  model_version_id text not null references model_versions (id),
  vintage text not null check (vintage in ('OPENING','EARLY_WEEK','PRACTICE_UPDATE','FINAL_INJURY_REPORT','PREGAME','CLOSING_CAPTURE')),
  as_of_at timestamptz not null,
  feature_snapshot_id text references game_feature_snapshots (id),
  home_win_probability numeric(6,5) not null,
  away_win_probability numeric(6,5) not null,
  expected_home_score numeric(5,2) not null,
  expected_away_score numeric(5,2) not null,
  expected_margin numeric(5,2) not null,
  expected_total numeric(5,2) not null,
  margin_interval_80 jsonb not null,
  total_interval_80 jsonb not null,
  margin_std numeric(5,2) not null,
  total_std numeric(5,2) not null,
  data_completeness_score numeric(5,4) not null,
  is_official boolean not null default false,
  created_at timestamptz not null default now(),
  -- one prediction per (game, model, vintage): later vintages are new rows
  unique (game_id, model_version_id, vintage)
);
create index predictions_game_asof_idx on predictions (game_id, as_of_at);

create table prediction_components (
  id text primary key,
  prediction_id text not null references predictions (id) on delete cascade,
  component_name text not null,
  home_win_probability numeric(6,5),
  expected_margin numeric(5,2),
  expected_total numeric(5,2),
  weight numeric(5,4) not null default 0,
  is_placeholder boolean not null default false,
  created_at timestamptz not null default now()
);

create table recommendations (
  id text primary key,
  user_id uuid references users (id) on delete cascade,
  game_id text not null references games (id),
  prediction_id text not null references predictions (id),
  manual_price_id uuid references manual_book_prices (id),
  market text not null check (market in ('MONEYLINE','SPREAD','TOTAL')),
  selection text not null check (selection in ('HOME','AWAY','OVER','UNDER')),
  line numeric(6,2),
  american integer not null,
  status text not null check (status in ('BET','WATCH','PASS','DATA INCOMPLETE')),
  model_probability numeric(6,5),
  conservative_probability numeric(6,5),
  market_no_vig_probability numeric(6,5),
  break_even_probability numeric(6,5),
  edge numeric(7,5),
  ev_per_dollar numeric(8,5),
  push_probability numeric(6,5),
  fair_american integer,
  confidence text check (confidence in ('LOW','MEDIUM','HIGH')),
  stake_breakdown jsonb,
  supporting_factors jsonb not null default '[]'::jsonb,
  opposing_factors jsonb not null default '[]'::jsonb,
  reasons_to_pass jsonb not null default '[]'::jsonb,
  invalidation_conditions jsonb not null default '[]'::jsonb,
  status_reasons jsonb not null default '[]'::jsonb,
  target_price integer,
  invalidation_price integer,
  created_at timestamptz not null default now(),
  invalidated_at timestamptz,
  invalidation_reason text
);
create index recommendations_game_idx on recommendations (game_id, created_at);

-- ---------------------------------------------------------------------------
-- Bankroll, bets, settlement — append-only financial history
-- ---------------------------------------------------------------------------

create table bankroll_accounts (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references users (id) on delete cascade,
  mode text not null check (mode in ('PAPER','REAL_TRACKING')),
  currency text not null default 'USD',
  starting_balance numeric(12,2) not null,
  current_balance numeric(12,2) not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table bankroll_transactions (
  id uuid primary key default gen_random_uuid(),
  bankroll_account_id uuid not null references bankroll_accounts (id),
  user_id uuid not null references users (id) on delete cascade,
  kind text not null check (kind in ('DEPOSIT','WITHDRAWAL','BET_SETTLEMENT','CORRECTION','ADJUSTMENT')),
  amount numeric(12,2) not null,
  balance_after numeric(12,2) not null,
  bet_id uuid,
  detail text,
  event_at timestamptz not null default now(),
  created_at timestamptz not null default now()
);

create table bets (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references users (id) on delete cascade,
  bankroll_account_id uuid not null references bankroll_accounts (id),
  recommendation_id text references recommendations (id),
  prediction_id text references predictions (id),
  model_version_id text references model_versions (id),
  feature_snapshot_id text references game_feature_snapshots (id),
  game_id text not null references games (id),
  market text not null check (market in ('MONEYLINE','SPREAD','TOTAL')),
  selection text not null check (selection in ('HOME','AWAY','OVER','UNDER')),
  line numeric(6,2),
  american integer not null,
  stake numeric(12,2) not null check (stake > 0),
  mode text not null check (mode in ('PAPER','REAL_TRACKING')),
  placed_at timestamptz not null,
  result text not null default 'PENDING' check (result in ('PENDING','WIN','LOSS','PUSH','VOID')),
  payout numeric(12,2),
  closing_line numeric(6,2),
  closing_american integer,
  closing_line_value_pct numeric(7,3),
  settled_at timestamptz,
  created_at timestamptz not null default now()
);
create index bets_user_placed_idx on bets (user_id, placed_at);

create table bet_events (
  id uuid primary key default gen_random_uuid(),
  bet_id uuid not null references bets (id),
  event_type text not null check (event_type in ('PLACED','SETTLED','VOIDED','CORRECTED')),
  detail text not null,
  actor text not null,
  created_at timestamptz not null default now()
);

create table settlements (
  id uuid primary key default gen_random_uuid(),
  bet_id uuid not null references bets (id),
  result text not null check (result in ('WIN','LOSS','PUSH','VOID')),
  bankroll_delta numeric(12,2) not null,
  closing_line numeric(6,2),
  closing_american integer,
  closing_line_value_pct numeric(7,3),
  settled_at timestamptz not null,
  is_correction boolean not null default false,
  correction_reason text,
  created_at timestamptz not null default now()
);

create table exposure_snapshots (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references users (id) on delete cascade,
  as_of_at timestamptz not null,
  open_stake numeric(12,2) not null,
  weekly_exposure_pct numeric(7,5) not null,
  by_game jsonb not null default '{}'::jsonb,
  by_team jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

-- ---------------------------------------------------------------------------
-- Evaluation, data quality, audit
-- ---------------------------------------------------------------------------

create table model_evaluations (
  id uuid primary key default gen_random_uuid(),
  model_version_id text not null references model_versions (id),
  period text not null,
  slice jsonb not null default '{}'::jsonb,
  log_loss numeric(8,5),
  brier_score numeric(8,5),
  calibration_error numeric(8,5),
  calibration_slope numeric(8,5),
  calibration_intercept numeric(8,5),
  mean_clv_pct numeric(7,3),
  roi_after_vig numeric(8,5),
  max_drawdown numeric(8,5),
  n_predictions integer not null default 0,
  created_at timestamptz not null default now()
);

create table data_quality_events (
  id uuid primary key default gen_random_uuid(),
  feed text not null,
  severity text not null check (severity in ('NONE','LOW','MODERATE','HIGH','CRITICAL')),
  message text not null,
  impacted_game_ids jsonb not null default '[]'::jsonb,
  impacted_prediction_ids jsonb not null default '[]'::jsonb,
  created_at timestamptz not null default now(),
  resolved_at timestamptz
);

create table audit_log (
  id uuid primary key default gen_random_uuid(),
  user_id uuid references users (id) on delete set null,
  action text not null,
  entity text not null,
  entity_id text not null,
  detail text,
  created_at timestamptz not null default now()
);
create index audit_log_entity_idx on audit_log (entity, entity_id, created_at);
