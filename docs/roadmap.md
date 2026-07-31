# Modeling roadmap — toward the Python analytical engine

## Phase A — service scaffold (first next step)

- `apps/api`: FastAPI + Pydantic v2 + SQLAlchemy 2 + Alembic, implementing `docs/api/openapi.yaml`.
- Port `@fde/calculations` math to a `fde_calculations` Python package with the **same test vectors**
  (share JSON fixtures so TS and Python implementations are provably identical).
- Wire Supabase Postgres; Alembic migrations generated from the existing SQL as baseline.
- Auth: verify Supabase JWTs; service role only inside the service.

## Phase B — data ingestion

- Real providers for schedule/roster/depth chart/injuries/weather/odds (documented, licensed APIs
  only — never sportsbook scraping).
- Ingestion runs → raw_objects → source_records with full point-in-time stamping; entity mapping
  reconciliation with conflict detection (drives CONFLICTING states).

## Phase C — models (in governance order)

1. Market baseline (no-vig consensus) — the benchmark every model must beat.
2. Dynamic team rating (Elo/Glicko-style with QB adjustment).
3. Regularized statistical baseline (ridge on opponent-adjusted efficiency).
4. Player-availability adjustment (replacement-quality weighted).
5. Bayesian hierarchical margin model (PyMC/NumPyro).
6. Gradient boosting on engineered features (LightGBM) with monotonic constraints.
7. Drive-level Monte Carlo simulator for margin/total distributions and key-number mass.
8. Calibration layer (isotonic or Platt, walk-forward fitted).

Every model: registered version, artifact hash, git commit, training/validation windows,
walk-forward evaluation only (no test-set leakage), approval workflow before its predictions can
mark `is_official`.

## Phase D — evaluation discipline

- Walk-forward backtests with point-in-time feature snapshots (the DB already stores them).
- Primary metrics: log loss, Brier, ECE/slope, CLV vs closing no-vig. ROI is tracked but never the
  approval criterion.
- Promotion rule example (to be validated): a model may replace the market baseline only after ≥2
  seasons of walk-forward CLV > 0 with p < 0.05 and no calibration regression.

## Phase E — product

- Real odds provider with multi-book median/best and closing capture.
- Live invalidation pushes (Supabase realtime) for injury/weather/line-move events.
- Tauri desktop packaging (frontend is already structured for it).
