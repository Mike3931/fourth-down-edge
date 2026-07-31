# ADR-001: Mock-first analytical layer behind a typed API contract

**Status:** Accepted · 2026-07-31

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
