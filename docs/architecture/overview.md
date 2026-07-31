# Architecture overview

## System shape

Fourth Down Edge is a monorepo with a React PWA frontend and a (future) Python analytical backend,
joined by a typed API contract.

```
┌─────────────────────────── apps/web (PWA) ────────────────────────────┐
│ React Router pages · TanStack Query/Table · Recharts · RHF + Zod      │
│         │                                                             │
│   @fde/ui (primitives)     @fde/shared-types (domain model)           │
│         │                                                             │
│   @fde/api-client  ←— typed FdeApi interface ——→  MockFdeApi (v1)     │
│         │                                          │                  │
│   @fde/calculations (deterministic engine)  ←──────┘                  │
└───────────────┬───────────────────────────────────────────────────────┘
                │ same OpenAPI contract (docs/api/openapi.yaml)
┌───────────────▼──────────────── apps/api (future) ────────────────────┐
│ Python · FastAPI · Pydantic · SQLAlchemy · Alembic                    │
│ versioned statistical models · Monte Carlo · calibration              │
└───────────────┬───────────────────────────────────────────────────────┘
                │
┌───────────────▼──────────────── Supabase ─────────────────────────────┐
│ PostgreSQL (35 tables) · Auth (email OTP) · RLS · append-only guards  │
└───────────────────────────────────────────────────────────────────────┘
```

## Key decisions

- **All final numbers come from deterministic code.** `@fde/calculations` is pure TypeScript with 92
  unit/integration tests. The UI renders those outputs; no language model produces any probability,
  price, stake, or recommendation status.
- **The data-access seam is `FdeApi`.** Version 1 binds `MockFdeApi` (deterministic demo dataset,
  clearly labeled). The Python service later implements the same OpenAPI contract; the UI does not change.
- **Mock model logic is temporary and isolated** in `packages/api-client/src/{dataset,evaluate}.ts`.
  It must not migrate into UI components, and proprietary model logic must not permanently live in the
  browser.
- **Point-in-time integrity is enforced three times:** in `@fde/calculations/pointInTime` (tested
  utilities), in the dataset generator (every record carries sourceUpdatedAt/observedAt/ingestedAt),
  and in PostgreSQL triggers (predictions immutable, as-of guard).
- **Append-only money.** Bets/settlements/corrections/audit events are append-only in both the client
  ledger (`@fde/calculations/ledger`) and the database (0003 triggers).
- **Tauri-ready.** The frontend uses no Node APIs, no SSR, hash-free routing, and a single static
  bundle, so packaging with Tauri later requires only a webview shell.
- **Failures are visible, never silent.** A per-route error boundary renders an explicit fault panel
  stating that nothing on the screen should be read as a prediction or price. A blank panel could be
  mistaken for "no opportunities found", which is a materially different claim.
- **No outbound network calls exist in application source.** The only network capability is the
  Supabase auth client; the service worker is same-origin only. Verified by grep in the QA pass.

## Data flow (v1 demo)

1. `MockFdeApi.getDataset()` generates the deterministic demo dataset (seeded PRNG, frozen demo clock).
2. `evaluateGame()` joins the latest official prediction vintage + freshest odds snapshot + any
   confirmed manual price, then calls the deterministic engine:
   probabilities → no-vig → EV/edge → `decideRecommendation()` → `computeStake()`.
3. Pages render via TanStack Query. The paper-bet ledger lives in localStorage (mirroring the DB
   shapes 1:1) and flows through the same append-only ledger functions used by the tests.

## Modes

- **PAPER (default):** simulated stakes only.
- **REAL TRACKING (opt-in):** records wagers the user already placed manually elsewhere. Requires a
  positive monthly loss budget and an explicit risk acknowledgment. Still never transmits anything to
  any sportsbook.
