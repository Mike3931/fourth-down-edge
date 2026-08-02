# Security notes

## Hard prohibitions (by design, not configuration)

- No sportsbook scraping (explicitly including bet365), no sportsbook automation, no undocumented
  endpoints. The codebase contains no HTTP client pointed at any sportsbook.
- No storage of sportsbook usernames, passwords, cookies, or session data — no fields, tables, or
  types exist for them.
- No wager placement or submission of any kind. The only "sportsbook" input is a manually typed price
  with a mandatory "I confirm this price is currently visible and was entered manually" checkbox.
- No payment-card data.

## Controls implemented

| Area | Control |
| --- | --- |
| AuthN | Supabase email OTP (when configured); clearly-labeled local demo session otherwise |
| AuthZ | Row Level Security on every table; user-owned rows keyed to `auth.uid()`; shared research data read-only for clients, writable only by the service role |
| Immutability | DB triggers forbid UPDATE/DELETE on predictions, manual prices, transactions, settlements, bet events, audit log; bets allow only PENDING→settled and evented corrections |
| Secrets | Frontend uses the anon key only; `.env` files git-ignored; template in `.env.example`; no provider secrets in frontend code |
| Input validation | Zod schemas on every form; CHECK constraints in PostgreSQL (odds magnitude, probability ranges, enum vocabularies) |
| CSP | `default-src 'self'`; connect-src limited to self + `*.supabase.co`; `frame-ancestors 'none'`; no inline scripts |
| CORS | Supabase project should allowlist only the deployed origin (see docs/deployment.md) |
| Rate limiting | API surface designed per-endpoint (OpenAPI) so a gateway can rate-limit `POST /manual-prices` etc. |
| Audit | Append-only `audit_log` + `bet_events`; every financial action creates events |
| Least privilege | service_role used only by future ingestion/analytics services; never shipped to the client |
| Environments | Separate dev/prod Supabase projects recommended; config via env vars only |
| Data rights | Settings screen: one-click JSON export and full local deletion; DB-side deletion via `on delete cascade` from `users` |

## Residual risks / notes

- Local Demo Mode stores the ledger in localStorage (unencrypted, single-device). Acceptable for demo
  data; real deployments should prefer the Supabase-backed store.
- The service worker never caches API data; market data staleness is computed from data timestamps and
  surfaced in the UI, so cached shells cannot silently present stale prices as current.
  (Verified: the cache branch is extension-allowlisted — `.js|.css|.png|.svg|.woff2?` plus the
  manifest — so an `/api/...` path cannot match it, and cross-origin requests return early.)

## Dependency audit

CI job **Dependency audit** gates deploys. Three checks:

| check | scope | blocking |
| --- | --- | --- |
| `npm audit --omit=dev --audit-level=high` | what actually ships | yes |
| `npm audit` | includes dev-only advisories | no |
| `pip-audit` against the exported `uv.lock` | Python runtime + dev | yes, for anything not listed below |

Dev-only JavaScript advisories are reported but do not gate. They are real
and worth fixing, but a linter's transitive dependency cannot reach a user,
and letting it block deploys trains everyone to bypass the gate that does
matter.

The Python audit runs against the **lockfile**, not the built environment:
run against the environment, `pip-audit` tries to resolve the local
unpublished `fde-api` package on PyPI and fails on that rather than on any
real finding.

### Outstanding advisories (acknowledged, not dismissed)

These are listed by ID in the workflow rather than suppressed by lowering
the severity threshold, so the gate still goes red for anything new.

| package | current | advisories | fix in |
| --- | --- | --- | --- |
| `starlette` | 0.48.0 | PYSEC-2026-161, -248, -249, -1942, -2280, -2281 | up to 1.3.1 |
| `pyarrow` | 21.0.0 | PYSEC-2026-113 | 23.0.1 |
| `pytest` | 8.4.2 | PYSEC-2026-1845 | 9.0.3 (dev-only) |

**Why the upgrade is deferred rather than applied.** `uv.lock` is hashed
into the frozen forward-test policy `ftp-2026-v1` via
`policy.dependency_lock_hash`, and into every model-registry entry.
Changing the lock means the recorded hash no longer describes the running
environment, so this is a governance decision about the frozen policy
rather than a routine patch. `starlette` 0.48 → 1.x is also a major-version
move underneath FastAPI.

**This is an open risk, not an accepted one.** `starlette` is the ASGI
layer the API actually serves on. The decision needed is whether to
re-freeze the policy under an upgraded lock, or to pin the deployment
behind a gateway that mitigates the specific advisories. It should be made
deliberately, and the ignore-list above removed when it is.

### JavaScript production advisories

`react-router` / `react-router-dom` carry two **moderate** advisories
(open redirect via backslash in `<Link>`/`useNavigate`; arbitrary
constructor injection in `deserializeErrors()` during SSR hydration).
They sit below the blocking `high` threshold. The SSR hydration issue does
not apply — the app is a static SPA build with no server-side rendering.
The open redirect is the one that could apply, since `<Link>` is used
throughout.

**This is not a routine bump.** The vulnerable range is `6.0.0 - 7.17.0`
with no patched 6.x release, and the app declares `^6.28.1`. The fix
therefore requires a React Router **v6 → v7 major migration**, not a patch
within the existing range. It carries no policy-freeze implications (that
constraint is Python-side only), but it does need every route re-verified
and the end-to-end journey re-run.

**Current exposure, verified rather than assumed.** The exploit path is a
backslash-prefixed target reaching `<Link>` or `useNavigate` from
untrusted input. In this app:

* `useNavigate` is not used anywhere.
* Every `<Link to=>` resolves to either a static route constant
  (`Shell.tsx`) or the template `` `/game/${gameId}` ``, where `gameId`
  comes from the app's own dataset, never from user input, a query string,
  or a hash fragment.

So the vulnerability is real in the dependency but currently unreachable
in this codebase. That is a reason to schedule the migration deliberately
rather than to rush it — and a reason not to introduce user-controlled
navigation targets before it lands.
