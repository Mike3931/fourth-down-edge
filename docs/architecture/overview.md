# Architecture overview

## System shape

Fourth Down Edge is a monorepo with a React PWA frontend and a Python analytical backend.

`apps/api` is BUILT and running — this section said "(future)" long after it
stopped being one. The two are joined by HTTP, not by the hand-written
contract the diagram used to cite: the engine generates its own at
`/openapi.json`, and `docs/api/openapi.yaml` is a superseded design sketch
describing seven paths the engine does not serve.

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
                │ HTTP · contract generated at /openapi.json
┌───────────────▼──────────────── apps/api (BUILT) ─────────────────────┐
│ Python · FastAPI · Pydantic · SQLAlchemy · Alembic                    │
│ versioned statistical models · Monte Carlo · calibration              │
└───────────────┬───────────────────────────────────────────────────────┘
                │
┌───────────────▼──────────────── Supabase ─────────────────────────────┐
│ PostgreSQL (35 tables) · Auth (email OTP) · RLS · append-only guards  │
└───────────────────────────────────────────────────────────────────────┘
```

## Key decisions

- **All final numbers come from deterministic code.** `@fde/calculations` is pure TypeScript with 171
  unit/integration tests. The UI renders those outputs; no language model produces any probability,
  price, stake, or recommendation status.
- **The data-access seam is `FdeApi`.** The DEMO screens bind `MockFdeApi` (deterministic demo
  dataset, clearly labeled). The six engine-backed screens do not go through it at all — they call
  the engine directly through `apps/web/src/lib/engine.ts`, because they render a different KIND of
  number and the app's whole separation depends on never letting the two paths substitute for each
  other. See `docs/limitations.md` item 1 for which screen is which.
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
- **No outbound network calls exist in FRONTEND source.** The only network capability in `apps/web`
  is the Supabase auth client plus same-origin fetches to the engine; the service worker is
  same-origin only. The ENGINE does make outbound calls — nflverse, ESPN, The Odds API, NWS — every
  one to a documented provider, and never to a sportsbook. `npm run audit:bundle` proves no
  credential reaches client code.

## Data flow (DEMO screens)

1. `MockFdeApi.getDataset()` generates the deterministic demo dataset (seeded PRNG, frozen demo clock).
2. `evaluateGame()` joins the latest official prediction vintage + freshest odds snapshot + any
   confirmed manual price, then calls the deterministic engine:
   probabilities → no-vig → EV/edge → `decideRecommendation()` → `computeStake()`.
3. Pages render via TanStack Query. The paper-bet ledger lives in localStorage (mirroring the DB
   shapes 1:1) and flows through the same append-only ledger functions used by the tests.

## Modes

- **PAPER (default):** simulated stakes only.
- **REAL TRACKING (opt-in):** records the user's INTENT to track wagers placed manually elsewhere,
  plus a monthly loss budget and an explicit risk acknowledgment. It does **not** change what the
  ledger stores: `placeBet` refuses any mode that is not exactly `PAPER`, both call sites pass the
  literal, and `settings.mode` never reaches the ledger at all.

  This entry used to say the mode "records wagers the user already placed manually elsewhere",
  which the build does not do — the same sentence was removed from Settings and the header badge in
  7a2a6b6 and survived here. Every wager in this build is recorded as paper, and nothing is ever
  transmitted to any sportsbook.
