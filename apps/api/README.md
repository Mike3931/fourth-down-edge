# apps/api — future Python analytical service (not yet built)

This directory is intentionally empty in version 1. It is reserved for the FastAPI service described
in [`docs/roadmap.md`](../../docs/roadmap.md) and contracted by [`docs/api/openapi.yaml`](../../docs/api/openapi.yaml).

## Why it's empty right now

Per the build's explicit fallback plan, version 1 ships without a Python backend:

1. The complete frontend and Supabase foundation are built (`apps/web`, `supabase/`).
2. A typed API-client abstraction exists (`packages/api-client`, interface `FdeApi`).
3. Realistic mock responses implement that interface (`MockFdeApi`) with data clearly labeled
   `DEMONSTRATION DATA — NOT FOR REAL-MONEY DECISIONS`.
4. An OpenAPI 3.1 specification (`docs/api/openapi.yaml`) documents the contract this service must
   implement so the frontend requires no changes when it lands.
5. Temporary mock-model logic is isolated to two files —
   [`packages/api-client/src/dataset.ts`](../../packages/api-client/src/dataset.ts) and
   [`packages/api-client/src/evaluate.ts`](../../packages/api-client/src/evaluate.ts) — and is not
   duplicated into UI components, so it can be deleted cleanly once this service exists.

## What goes here (Phase A of the roadmap)

```
apps/api/
  pyproject.toml
  src/fde_api/
    main.py              # FastAPI app, mounted per docs/api/openapi.yaml
    routers/              # one module per OpenAPI path group
    models/                # Pydantic schemas mirroring packages/shared-types
    db/                    # SQLAlchemy models + Alembic migrations
  tests/
    fixtures/              # shared JSON test vectors with packages/calculations,
                            # so the TypeScript and Python odds/EV/Kelly math are
                            # provably identical, not just independently tested
```

Do not add speculative code here ahead of that phase — an empty, documented directory is more honest
than a scaffold nobody has committed to building yet.
