# ADR-001: Mock-first analytical layer behind a typed API contract

**Status:** Accepted 2026-07-31 · **partially superseded 2026-08-12**

> The premise below — "this build environment cannot host a Python service"
> — no longer holds. `apps/api` exists, and the six engine-backed screens
> call it directly rather than through `MockFdeApi`.
>
> Two consequences below did not survive contact. Swapping in the engine was
> **not** "a one-line binding change plus deleting the mock": the engine
> screens are separate pages on separate routes, precisely because engine
> output and demo output are different kinds of number and must never
> substitute for one another. And the mock was not deleted — the demo
> screens still use it, and are labelled as such on every page.
>
> Decisions 1, 3 and 4 stand. Decision 2's OpenAPI half does not: the engine
> generates its own contract at `/openapi.json`, and `docs/api/openapi.yaml`
> describes seven paths it does not serve.
>
> An ADR is a record of a decision at a moment, so the original text is left
> intact below rather than edited into agreement with what happened.

## Context

The product requires a Python/FastAPI analytical service for real statistical models (Bayesian
hierarchical, gradient boosting, Monte Carlo simulation, calibration). This build environment delivers
the frontend, database, and calculation engine, but cannot host a Python service. The specification
mandates a fallback: complete frontend + Supabase foundation + typed API client + mock responses +
OpenAPI spec, with mock model logic clearly isolated.

## Decision

1. Define `FdeApi` (TypeScript interface in `@fde/api-client`) and an equivalent OpenAPI 3.1 spec
   (`docs/api/openapi.yaml`) as the single contract.
2. Ship `MockFdeApi` in v1: a deterministic, seeded demo dataset plus a deterministic evaluation
   adapter, all clearly labeled DEMONSTRATION DATA.
3. Keep every reusable, non-proprietary calculation (odds math, EV, Kelly, caps, recommendation state
   machine, point-in-time guards, ledger) in `@fde/calculations` — these are correct production code
   and stay in TypeScript for client-side display math and validation.
4. Advanced model components (Bayesian, GBM, Monte Carlo) appear in the registry as PLACEHOLDER with
   ensemble weight 0, so nothing presents unvalidated models as real.

## Consequences

- The UI is fully demonstrable and testable offline today.
- Swapping in the Python service is a one-line binding change (`api.ts`), plus deleting the mock.
- Risk: mock logic drifting into UI. Mitigated by the lint-level rule that pages import only from
  `@fde/api-client` (never from its `dataset`/`evaluate` internals directly for computation) and by
  the "temporary mock" banner comments in those files.
