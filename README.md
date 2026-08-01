# Fourth Down Edge

[![Deploy](https://github.com/Mike3931/fourth-down-edge/actions/workflows/deploy.yml/badge.svg)](https://github.com/Mike3931/fourth-down-edge/actions/workflows/deploy.yml)

A private NFL probability, pricing, research, and bankroll **decision-support** platform, styled as an
institutional quantitative research terminal.

**Live demo:** https://mike3931.github.io/fourth-down-edge/ — redeploys automatically from `master`
once the full test suite passes (see `.github/workflows/deploy.yml`).

**What it is not:** a sportsbook, a gambling entertainment app, an automated betting bot, or an AI pick
generator. It never places wagers, never connects to a sportsbook, never stores sportsbook credentials,
and never scrapes bet365. Paper betting is the default mode. All v1 data is
**DEMONSTRATION DATA — NOT FOR REAL-MONEY DECISIONS.**

## Core principles

1. A language model never creates final numerical probabilities — every number comes from versioned,
   deterministic, tested code (`packages/calculations`).
2. Point-in-time integrity: predictions may only use information observed at or before their cutoff.
3. The market is a powerful baseline; recommendations are tied to a specific line and price.
4. Missing critical data produces **DATA INCOMPLETE**, never a fabricated recommendation.
5. PASS is the normal posture; capital preservation over activity.

## Repository layout

```
fourth-down-edge/
  apps/
    web/                 React + TypeScript + Vite + Tailwind PWA (the terminal)
    api/                 Future Python FastAPI analytical service (see docs/api)
  packages/
    shared-types/        Canonical domain types (mirror the DB schema)
    calculations/        Deterministic odds/EV/Kelly/risk/recommendation engine + tests
    api-client/          Typed API abstraction + deterministic mock dataset + tests
    ui/                  Reusable terminal-styled UI primitives
  supabase/
    migrations/          PostgreSQL schema, RLS policies, append-only guards
    seed/                Demo seed SQL
  docs/
    architecture/        Overview + ADRs
    api/                 OpenAPI spec for the future Python service
    data-dictionary/     Column-level documentation
    model-governance/    Model registry rules and placeholder policy
  infrastructure/        Deployment notes
  tests/                 End-to-end specs (Playwright)
```

## Quick start (local)

Prerequisites: Node.js ≥ 20.

```
npm install
npm test          # 112 unit + integration tests (calculations, mock api)
npm run test:db   # validates supabase/migrations + seed against a real Postgres engine
npm run dev       # http://localhost:5173
```

Sign in with **“Enter local demo session”** — no Supabase needed. Everything runs on-device against the
deterministic demo dataset.

### With Supabase (optional)

1. Create a Supabase project.
2. Apply `supabase/migrations/*.sql` in order (SQL editor or `supabase db push`).
3. Run `supabase/seed/seed.sql`.
4. Copy `.env.example` to `apps/web/.env.local` and fill `VITE_SUPABASE_URL` and `VITE_SUPABASE_ANON_KEY`.
5. `npm run dev` — email OTP sign-in is now active; Row Level Security protects all user-owned rows.

## Production build & deploy

```
npm run build     # typecheck + vite build → apps/web/dist
npm run preview   # serve the production build locally
```

Deploy `apps/web/dist` to any HTTPS static host (Vercel/Netlify/Cloudflare Pages). HTTPS is required for
service-worker installability. See `docs/deployment.md` for exact steps and headers.

## Install as a desktop PWA

1. Open the deployed HTTPS URL (or `npm run preview`) in Chrome/Edge.
2. Click the install icon in the address bar (“Install Fourth Down Edge”).
3. The app opens standalone, works offline (app shell), and never shows cached market data without a
   visible staleness warning.

## Testing

- `npm run test -w @fde/calculations` — odds conversion, no-vig, EV with pushes, Kelly, stake caps,
  recommendation states, freshness, point-in-time cutoffs, append-only ledger integration flows.
- `npm run test -w @fde/api-client` — dataset determinism, scenario coverage (all four recommendation
  states), immutability of manual prices.
- `npm run test:db` — runs `supabase/migrations/*.sql` and `supabase/seed/seed.sql` against a real
  embedded Postgres engine (no Docker required), then exercises the append-only guard triggers end to
  end (confirms a direct `UPDATE` on `predictions` and an unevented bet re-settlement are both
  rejected by the database itself, not just by application code), and confirms Row Level Security
  actually isolates users rather than just having policies defined — a second, genuinely non-owner
  `authenticated` role is created and switched to, and verified to see zero rows of another user's
  manual prices or bankroll account (with no `WHERE` clause needed), to be blocked from inserting a
  row that claims another user's id, and to still see shared reference data; an anonymous session is
  verified to see zero rows of anyone's data.
- `npm run test:e2e` — Playwright spec covering the full user journey (sign in → slate → Game Lab →
  manual price entry → paper bet → settlement → Performance Lab → Model Audit → Data Health) against
  the production build. Requires `npx playwright install chromium` once.

## Documentation

- `docs/architecture/overview.md` — system design and data flow
- `docs/architecture/adr-001-mock-first-analytics.md` — why v1 ships a mock analytical layer
- `docs/api/openapi.yaml` — contract for the future Python FastAPI service
- `docs/data-dictionary.md` — table/column reference
- `docs/model-governance.md` — model registry and placeholder policy
- `docs/security.md` — security controls and hard prohibitions
- `docs/limitations.md` — known limitations (read this first)
- `docs/roadmap.md` — modeling roadmap toward the Python analytical engine
