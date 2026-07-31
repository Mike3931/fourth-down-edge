# Infrastructure

Version 1 requires no bespoke infrastructure: a static HTTPS host for `apps/web/dist` plus a Supabase
project (see `docs/deployment.md`).

This directory is reserved for the Python analytical service phase:

- `docker/` — FastAPI service container
- `terraform/` — Supabase config, host DNS, secrets management
- CI: GitHub Actions suggested pipeline — `npm ci && npm test && npm run build`, then Playwright E2E
  against a preview deploy, then static deploy.
