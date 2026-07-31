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
