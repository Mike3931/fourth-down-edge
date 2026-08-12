# Data dictionary

Timestamps: every persistent timestamp is UTC (`timestamptz`). The point-in-time envelope appears on
all source-derived tables:

| Column | Meaning |
| --- | --- |
| `event_at` | When the real-world event occurred |
| `source_updated_at` | When the upstream source last updated the information |
| `observed_at` | When this application first observed the information. Never in the future: it is this application's own clock, so a value ahead of it is seeded or clock-skewed data. Enforced by the `observation_instant_in_future` health check, and it matters because the point-in-time rule below (`observed_at <= as_of_at`) is satisfied *trivially* by such a row — it is excluded from every snapshot taken before its own timestamp, and then silently admitted as the freshest record available |
| `ingested_at` | When this application persisted it |
| `as_of_at` | Prediction cutoff — a prediction may only use records with `observed_at <= as_of_at` |
| `valid_from` / `valid_to` | Validity interval for slowly changing mappings |
| `source` / `source_record_id` | Provenance pointer to the upstream feed |

## Tables (35)

| Table | Purpose | Notes |
| --- | --- | --- |
| `users` | Application users (mirrors `auth.users`) | RLS: self only |
| `user_settings` | Odds format, timezone, mode, risk controls | RLS: self only; PAPER default |
| `providers` | Registered data feeds | criticality flag drives DATA INCOMPLETE |
| `ingestion_runs` | Every fetch attempt | success/failure, counts |
| `raw_objects` | Raw payloads with content hashes | replayable provenance |
| `source_records` | Normalized per-entity records | links raw → domain |
| `entity_mappings` | External↔internal id maps | validity-interval versioned |
| `teams` | Plain-text team reference | **no logos anywhere** |
| `players` | Player reference | demo uses fictional names |
| `stadiums` | Venue reference | surface/roof/altitude/timezone |
| `officials` | Referee crews + tendencies | demo values |
| `games` | Schedule + results | `ended_at` gates result usability |
| `game_officials` | Crew assignments | |
| `roster_snapshots` | Point-in-time rosters | as-of queried |
| `depth_chart_snapshots` | Point-in-time depth charts | |
| `participation` | Post-game snap data | never usable pre-cutoff |
| `injury_reports` | Practice status + designation | conflict warnings |
| `player_availability_snapshots` | P(active), snap share, restriction, replacement quality, impact | probabilistic availability |
| `weather_snapshots` | Forecasts | `forecast_generated_at` gated vs cutoff |
| `odds_snapshots` | Market prices over time | opening/closing flags; append-only in practice |
| `manual_book_prices` | User-typed bet365 prices | **immutable, append-only, confirmation required** |
| `feature_sets` | Feature schema registry | |
| `game_feature_snapshots` | Exact features per prediction | content-hashed |
| `model_versions` | Model registry | placeholder policy; artifact hash; git commit |
| `calibration_models` | Calibration layers | |
| `predictions` | Immutable vintages | UNIQUE(game, model, vintage); UPDATE/DELETE forbidden by trigger |
| `prediction_components` | Per-component outputs | weight 0 = placeholder |
| `recommendations` | Full decision records | status ∈ BET/WATCH/PASS/DATA INCOMPLETE |
| `bankroll_accounts` | Bankrolls per mode | RLS: owner |
| `bankroll_transactions` | Balance history | append-only (trigger) |
| `bets` | Paper + manually recorded wagers | identity fields frozen; correction path only |
| `bet_events` | Placement/settlement/correction events | append-only |
| `settlements` | Settlement records incl. corrections | append-only |
| `exposure_snapshots` | Exposure over time | |
| `model_evaluations` | Sliced performance metrics | log loss, Brier, ECE, CLV, ROI, drawdown |
| `data_quality_events` | Feed failures and conflicts | drives invalidation |
| `audit_log` | Application audit trail | append-only |

## Status vocabularies

- Recommendation: `BET`, `WATCH`, `PASS`, `DATA INCOMPLETE`
- Data health: `CURRENT`, `AGING`, `STALE`, `MISSING`, `CONFLICTING`
- Player availability states: `INACTIVE`, `ACTIVE_RESTRICTED`, `ACTIVE_ORDINARY`
- Prediction vintages: `OPENING`, `EARLY_WEEK`, `PRACTICE_UPDATE`, `FINAL_INJURY_REPORT`, `PREGAME`, `CLOSING_CAPTURE`
