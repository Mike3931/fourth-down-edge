# Fourth Down Edge — analytical engine (`apps/api`)

> **RESEARCH MODE — MODEL NOT APPROVED FOR REAL-MONEY DECISIONS**
>
> Nothing in this service is a betting recommendation. It emits
> `RESEARCH_CANDIDATE` / `WATCH` / `PASS` / `DATA_INCOMPLETE` only; a
> production `BET` state does not exist here. Every model artifact is
> registered `research_only`, and no approval path is implemented.

Python 3.12 · FastAPI · Pydantic v2 · SQLAlchemy 2 + Alembic · Polars ·
DuckDB/PyArrow · scikit-learn · statsmodels · pytest + Hypothesis ·
Ruff · mypy. Dependencies are pinned and locked in `uv.lock`.

## Quick start

```bash
cd apps/api
py -3.12 -m pip install uv
py -3.12 -m uv sync
.venv/Scripts/python -m alembic upgrade head
printf 'FDE_ALLOW_UNAUTHENTICATED=1\n' > .env
.venv/Scripts/python -m uvicorn fde_api.api.main:app --port 8000
```

`apps/api/.env` is gitignored and holds the service's environment. It must
exist before the engine starts: without a token the API answers 503 rather
than serving unauthenticated by accident, and `FDE_ALLOW_UNAUTHENTICATED=1`
is how you say you meant it. `FDE_ODDS_API_KEY` goes in the same file — see
[../../docs/security.md](../../docs/security.md); it is read from the
environment only, never logged, and never reaches client code.

The repo's `.claude/launch.json` starts the same process (`engine`, port
8000) with `--env-file apps/api/.env`, alongside the `web` dev server on
5173 whose proxy points at it.

Then drive it through the API — every long operation returns a job:

```bash
curl -X POST localhost:8000/v1/data/ingest/nflverse -H 'content-type: application/json' -d '{"seasons":[2018,2019,2020,2021,2022,2023,2024,2025]}'
curl localhost:8000/v1/jobs/<job_id>
curl -X POST localhost:8000/v1/backtests/run -H 'content-type: application/json' -d '{"test_season":2025}'
curl localhost:8000/v1/performance/model-comparison
```

Reports land in `data/reports/` (`phase2_reports.json` / `.md`).

## Architecture

| Layer | Module | Responsibility |
| --- | --- | --- |
| Providers | `providers/` | Adapter contract; nflverse implementation; NWS and odds adapter shapes |
| Raw store | `raw/store.py` | Immutable, content-addressed artifacts + full provenance manifests |
| Canonical | `canonical/` | Source → typed relational model, with validation and data-quality events |
| Point-in-time | `pit/` | `observed_at <= as_of_at` guards, horizons, forward-only replay clock |
| Features | `features/` | `nfl-core-v1`: decayed, opponent-adjusted, prior-games-only |
| Models | `models_ml/` | A naive · B market benchmark · C dynamic ratings · D ridge · E market residual |
| Distribution | `models_ml/distribution.py` | One discrete Normal-derived distribution → all market probabilities |
| Backtest | `backtest/` | Expanding-window walk-forward, weekly replay, execution realism |
| Calibration | `calibration.py` | none/Platt/beta/isotonic, selected on prior OOF only |
| Registry | `registry.py` | `research_only` artifacts with full audit metadata (MLflow-compatible) |
| API | `api/` | Typed endpoints + job model |
| Reports | `reports.py` | The 16 required reports, including negative findings |

## Point-in-time integrity (release-blocking)

A record enters a feature snapshot only when `observed_at <= as_of_at`.
Records with no observation instant are treated as **future**, never as
safely-past. Horizons are `OPENING`, `EARLY_WEEK`, `PRACTICE_UPDATE`,
`FINAL_INJURY_REPORT`, `PREGAME`; `CLOSING_CAPTURE` exists for evaluation
only and is excluded from prediction horizons. The replay clock cannot
rewind. Adversarial tests in `tests/test_leakage.py` plant future
results, odds, injuries, and roster state and assert rejection.

Selection discipline: decay half-life, ridge alpha, ratings `k`,
calibration method, and the candidate edge threshold are chosen on the
validation season only. The test season is scored once.

## Database

SQLite locally (zero setup); PostgreSQL in CI/production via
`FDE_DATABASE_URL`. The schema is portable across both, and migrations
live in `migrations/versions/`.

## Testing

```bash
.venv/Scripts/python -m pytest tests -m "not network"   # 64 tests
.venv/Scripts/python -m ruff check src tests
.venv/Scripts/python -m mypy
```

## Relationship to the TypeScript app

The web app talks to this service only through
[`packages/api-client/src/research.ts`](../../packages/api-client/src/research.ts),
which validates every response with zod at runtime. If the engine is
unreachable or returns something unexpected, the UI shows
`DATA INCOMPLETE` — demo numbers are never substituted for research
numbers, and research mode never changes paper-betting behavior. Model
calculations are not duplicated in React.

In development, set the engine URL in Settings to `/research-api` to use
the Vite proxy (same-origin, no CORS round-trip). In other deployments,
point it at the engine's real URL and set `FDE_CORS_ORIGINS`.

## Honest limitations

* The only historical market data available are **closing** lines. They
  serve as the evaluation benchmark and the residual model's baseline —
  never as features at earlier horizons. There is no line movement,
  cross-book dispersion, or quote age in this phase.
* Because fills are simulated at-or-worse-than close, **closing-line
  value is structurally unavailable** and is reported as such rather
  than fabricated.
* No point-in-time historical injury, participation, depth-chart, or
  weather-forecast feed exists; 18 features are declared unavailable
  rather than backfilled with retrospective knowledge.
* Result-availability instants are approximated (kickoff + 4h30m) and
  horizon cutoffs are fixed offsets from kickoff, because the source
  records no intra-week observation timeline.
* The demo slate in the web app is synthetic, so its game IDs do not
  exist in this engine; research predictions appear for real historical
  games only. Replacing the demo slate with ingested real schedules is
  the next phase's work.
* Sample sizes are ~285 games per test season; reported ROI intervals
  are wide and consistent with zero edge. **No profitability claim is
  made or supported.**
* bet365 is never scraped, automated, or credential-stored. Manually
  entered prices from the app are the only sportsbook-specific input.
