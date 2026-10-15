# Official report access — Release 1B.4 correction

Status (October 15, 2026): **AUTOMATED NFL.COM RETRIEVAL DISABLED — AUTHORIZATION NOT ESTABLISHED**.

NFL requires prior written consent for systematic retrieval into collections/databases ([published terms](https://www.nfl.com/legal/terms/)). Public pages, successful HTTP responses, robots rules and research-only use do not establish permission.

## Enforced boundary

`collect_official` returns `official_disabled_pending_authorization: 1` before any database query, poll write, HTTP client creation or request. Its authorization function always returns false. There is no enabling CLI flag, environment variable, default configuration or credentials-based override. The standard prospective worker reports the disabled state, including when the legacy `--skip-official-capture` compatibility flag is present or omitted. Disabling is not recorded as a successful poll or source outage.

The parser, synthetic fixture, bounded transport implementation and lineage semantics remain for offline tests and a future authorized implementation. The transport regression uses only a mocked response and a test-scoped gate override. No production authorization is implied by that test.

Existing captured bytes, source rows, facts, polls, predictions, model versions, feature snapshots and evaluations are retained without rewriting their historical claims. No migration or cleanup is needed. Historical capture evidence documents what occurred, not permission. Availability remains incomplete, OUT is not INACTIVE, final-checkpoint publication remains withheld, and NO BET remains enforced.

## Exact external requirement before activation

Obtain and retain express prior written consent from NFL/the appropriate rights holder covering this application's automated retrieval of the named NFL.com injury-report resources, collection frequency and volume, original-content retention, database compilation, research/modeling use, derived outputs and any intended display. Record the grantor, grantee, effective/expiry dates, permitted endpoints, scope, restrictions and a durable authorization-document reference. No such authorization is currently on file in this repository.

Only after that evidence is reviewed may a separate change implement a scoped, default-off activation mechanism, expiration/revocation checks, permitted rate limits and associated regression tests. A boolean assertion by an operator is not the authorization document. This release deliberately has no activation path. Do not bypass restrictions, substitute proxies, or treat HTTP success as consent.

## Alternatives investigated (documentation only)

- **Sportradar NFL API:** documented Weekly Injuries and Game Roster feeds are candidates for injury reports and game-specific active/inactive coverage. [Weekly Injuries](https://developer.sportradar.com/football/reference/nfl-weekly-injuries), [Rosters and Players](https://developer.sportradar.com/football/docs/nfl-ig-rosters), [B2B API basics](https://developer.sportradar.com/football/docs/nfl-ig-api-basics). Obtain a license explicitly covering these feeds, retention, internal modeling and derived display; verify timestamps, completeness and player identifiers using authorized samples. Documentation does not grant this application access rights.
- **SportsDataIO:** documented NFL injury endpoints and status fields merit a licensed coverage assessment. [NFL API](https://sportsdata.io/developers/api-documentation/nfl), [workflow guide](https://sportsdata.io/developers/workflow-guide/nfl), [data rights](https://sportsdata.io/help/data-rights-and-licensing-questions). Their licensing guidance discusses ingestion/storage and derived outputs; the applicable executed agreement still must cover our use. Verify game-specific inactive lists, official provenance, issue timestamps and correction semantics; roster status alone is insufficient.
- **Direct league/team distribution:** seek written permission or a licensed feed for reports and declared inactive lists, including retention/modeling rights. No freely accessible replacement has been established as both authorized and complete. Existing secondary injury data cannot be relabeled official or used to clear completeness.

No outreach, subscription, paid service, trial credential or alternative collector was activated. Provider choice and adapters are outside this targeted correction.
