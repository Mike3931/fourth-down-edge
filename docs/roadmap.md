# Modeling roadmap — toward the Python analytical engine

> **Status, 2026-08-12: this document predates the engine and is kept as the
> plan it was.** `apps/api` exists and runs. Phase A is done; Phase B is done
> for schedule, odds, weather and injuries; Phase C has models 1–4 and 8
> registered `research_only`; Phase D's walk-forward machinery, metrics and
> forward-test policy are built. Phase E is where the live work is.
>
> Read the phases below as the reasoning, not as a to-do list. What is
> actually built, and what it is and is not allowed to claim, is in
> `docs/forward-test.md`, `docs/model-governance.md` and
> `reports/integrity/RUNBOOK.md`. Nothing here has been approved for
> real-money use and no approval path is implemented.

## Phase A — service scaffold (built)

- `apps/api`: FastAPI + Pydantic v2 + SQLAlchemy 2 + Alembic, serving its own generated contract at `/openapi.json` (the hand-written `docs/api/openapi.yaml` is a superseded design sketch).
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
