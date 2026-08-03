# Deployment

## GitHub Pages (automated via `.github/workflows/deploy.yml`)

Every push to `master` that passes the full test gate (unit/integration tests, the Postgres migration
validation, and the Playwright E2E journey) deploys automatically to
**https://mike3931.github.io/fourth-down-edge/**. Nothing further to run manually; `workflow_dispatch`
is also available for on-demand redeploys from the Actions tab.

GitHub Pages project sites are served under `/<repo-name>/`, not the domain root, which two things in
this codebase specifically account for:

- `apps/web/package.json`'s `build:pages` script passes `--base=/fourth-down-edge/` to both `vite
  build` and `scripts/build-sw.mjs`, so bundle references, the manifest/icon links, and the service
  worker's precache list all resolve under the subpath. The default `npm run build` (root base) is
  unaffected and remains correct for root-domain hosts below.
- `main.tsx` passes `basename={import.meta.env.BASE_URL}` to `BrowserRouter`, and the workflow copies
  `dist/index.html` to `dist/404.html` after the build — GitHub Pages has no server-side rewrite rule,
  so it serves `404.html` verbatim (HTTP 404) for any client-side route a user navigates to directly or
  refreshes; because that file is identical to `index.html`, the SPA boots anyway and `basename`
  resolves the real route from `window.location`. Verified locally against a server reproducing this
  exact behavior before being added to the workflow.

If you rename the repository, update `--base=/fourth-down-edge/` in `build:pages` to match.

## Other static hosts

```
npm run build            # -> apps/web/dist (root base path)
```

Deploy `apps/web/dist` to any HTTPS static host. Examples:

**Vercel:** project root `apps/web`, build command `npm run build -w @fde/web` (run from repo root via
`cd ../.. && npm run build -w @fde/web`), output `apps/web/dist`. Add a SPA rewrite: all routes →
`/index.html`.

**Netlify:** publish dir `apps/web/dist`, redirect rule `/* /index.html 200`.

Required headers (most hosts set sensible defaults; verify):

- The CSP is delivered via `<meta http-equiv>` in `index.html`; a host-level header may mirror it.
- `Cache-Control: no-cache` for `index.html` and `sw.js`; long-lived immutable caching for
  `/assets/*` (hashed filenames).

HTTPS is mandatory — service workers and PWA install do not function on plain HTTP (localhost is the
only exception).

## Supabase

1. Create separate dev and prod projects.
2. Apply `supabase/migrations/0001..0003` in order; run `supabase/seed/seed.sql`.
3. Auth → Email OTP enabled; disable anonymous sign-ups if not wanted.
4. API → CORS: allowlist only your deployed origin.
5. Never expose the `service_role` key to the frontend or commit it anywhere.
6. Set `VITE_SUPABASE_URL` / `VITE_SUPABASE_ANON_KEY` as build-time env vars in the host.

## Analytical engine (`apps/api`) environment

| variable | required | effect |
| --- | --- | --- |
| `FDE_API_TOKEN` | yes, in any shared deployment | Bearer token for every `/v1/*` endpoint. Compared with `secrets.compare_digest`, so the check does not leak the value through response timing. |
| `FDE_ALLOW_UNAUTHENTICATED` | only to run open on purpose | Must be exactly `1`. Ignored when `FDE_API_TOKEN` is set — the opt-out cannot disable a token that exists. |
| `FDE_CORS_ORIGINS` | no | Comma-separated allowlist. Defaults to the local Vite dev server only. Credentials are not allowed and methods are limited to GET/POST. |
| `FDE_ODDS_API_KEY` | for live capture | Read backend-only, never logged, never in a frontend bundle. Absence fails readiness rather than falling back to fixtures. |

**An unset `FDE_API_TOKEN` fails closed.** Every `/v1/*` endpoint returns
`503` naming both the token variable and the opt-out. This is deliberate:
the previous behaviour treated an unset token as "local dev, serve
everything", so a deployment whose environment failed to load would serve
the entire API unauthenticated *and report itself healthy*.

`/health` stays reachable without a token — otherwise nothing could report
that the service is misconfigured — and distinguishes all three states:

- `enabled`
- `disabled (explicitly allowed)`
- `MISCONFIGURED — no token set and unauthenticated access not allowed`

It never echoes the token value. Behaviour pinned by
`apps/api/tests/test_api_auth.py`.

### Post-deploy check

Run once against each environment that has ever performed live capture:

```bash
python -m fde_api.forward.secret_audit
```

Exit 0 clean, 1 inconclusive, 2 exposed. It looks for configured secrets
in persisted `error_summary` rows — see docs/security.md for why that
matters and what each outcome means. Not useful in CI (no database with
real history); this is an operational check.

## PWA install verification checklist

- Open the HTTPS URL in Chrome/Edge → DevTools → Application → Manifest: no warnings, installability
  "installable".
- Install; confirm standalone window, dark theme-color, icons at 192/512 + maskable.
- Toggle offline in DevTools → reload: app shell loads, screens render with their designed
  stale/disconnected states, and no market data is presented as current without a warning banner.
