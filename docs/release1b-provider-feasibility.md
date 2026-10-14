# bet365 Tennessee provider feasibility — 2026-10-12

Public documentation was checked during implementation. No vendor account, subscription, sales outreach, sportsbook session or credential was created. These findings are documentary evidence, not a successful live feed acceptance or a legal opinion. No reviewed provider has established exact Tennessee NFL/SGP coverage for this application.

| Provider | Published evidence | Cost/access | TN and exact SGP conclusion |
|---|---|---|---|
| The Odds API | The US catalog does not list bet365. Its bet365_au entry is Australian, paid, and limited to AFL/NRL main markets. V4 exposes book update timestamps and historical endpoints. | Key required; paid access for the documented bet365 entry/history. | Not a suitable documented TN NFL bet365 feed. Other US books remain benchmarks only. |
| SportsGameOdds | Lists bookmakerID `bet365`; advertises props, alternate lines, pre-match/live markets. Pricing places bet365 on Pro: $299/month, advertised sub-minute updates, 300 requests/minute. Free Amateur lists nine other books. | Credential and paid entitlement required for the documented bet365 offering. The $99 Rookie tier does not explicitly establish bet365 access. | Plausible vendor to investigate, not verified TN coverage. Exact NFL market samples, provider timestamp semantics, suspension flags, historical retention, state mapping and combined SGP endpoint/prices need confirmation. |
| OpticOdds | Documents bet365 among main odds providers and supports market/sportsbook catalogs and odds streams. Published SGP sportsbook lists omit bet365. AI/consensus parlay pricing is a different product. | API credential; pricing page requests a sales quote rather than publishing a fixed plan. | Broad bet365 advertising does not prove TN NFL coverage. No documented exact bet365 SGP support was established. Do not substitute its AI or another book's parlay price. |

Sources: [The Odds API bookmaker catalog](https://the-odds-api.com/sports-odds-data/bookmaker-apis.html), [V4 contract](https://the-odds-api.com/liveapi/guides/v4/); [SportsGameOdds bookmaker catalog](https://sportsgameodds.com/bookmakers), [pricing](https://sportsgameodds.com/pricing), [API setup](https://sportsgameodds.com/docs/basics/setup), [terms](https://sportsgameodds.com/terms); [OpticOdds main odds guide](https://developer.opticodds.com/docs/odds-api-getting-started-guide), [SGP endpoint](https://developer.opticodds.com/reference/post_parlay-odds), [SGP guide](https://developer.opticodds.com/docs/same-game-parlay-sgp-pricer-bet-builder), [pricing](https://opticodds.com/pricing).

## Recommendation and acceptance before enabling a connector

Continue free public research now. Do not buy a provider to populate a dashboard. SportsGameOdds and OpticOdds warrant a separately authorized evaluation only if they establish jurisdiction and licensing in writing, then supply permitted live samples. No vendor marketing page establishes usage rights, operational reliability, state-specific NFL coverage or a supported combined bet365 price. No reliability/SLA measurement was performed without a live credential. Prices above are current public list claims, not purchase quotes.

Require a canonical game/player identity crosswalk, exact market/period/line/selection identifiers, TN source identity, UTC provider/book price time distinct from ingestion, pre-match/in-play and suspension semantics, actual available quote, pagination completeness, entitlement and usage rights. Independently compare permitted samples to the user's TN offer. Exercise stale data, conflicting markets, inactives, outages, unsupported lines, disappearing legs, request/credit limits and corrections. Verify retention and refresh latency empirically. For SGPs, require supported leg combinations and the actual combined book price; individual prices cannot establish it.

## Current market-coverage matrix

| Category | Free inputs now | bet365 TN offer | Model/decision support in Release 1B |
|---|---|---|---|
| Full-game moneyline, spread, total | ESPN published observations, usually DraftKings; provider price time unknown | UNVERIFIED | Real research destinations and explicit blockers; no definitive value recommendation |
| Alternate lines, team totals, periods | No catalog ingested by this worker | UNAVAILABLE | Unavailable; never inferred from main lines |
| QB passing/attempts/completions/INT/rushing | No provider market catalog or current role model integrated | UNAVAILABLE | Unavailable |
| Rushing, receiving, receptions, usage | Canonical research primitives exist; no validated prop/catalog integration | UNAVAILABLE | Unavailable |
| TD/first TD/multiple TD | No exact market catalog integrated | UNAVAILABLE | Unavailable |
| SGP combinations/combined odds | No exact permitted combination feed | UNAVAILABLE | Joint-model and combined-price gates closed |

The observed catalog is derived from actual database quotes, not this aspirational matrix. A market not captured is unknown, not proof bet365 does not offer it.

## Free source integration and limits

The worker reuses ESPN's public scoreboard for canonical fixtures, published benchmark quotes and governed final scores. It requires no key and never contacts bet365. [ESPN scoreboard](https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard) has no established SLA or sportsbook-time guarantee here; capture budgets are bounded and failures are recorded. Existing nflverse point-in-time feature builders and NWS/injury domain services are retained. Team/player feature inputs without valid ingested vintages stay unavailable; missing injuries do not imply healthy players. NWS data without verified venue/roof applicability must not adjust a model. More ingestion is not automatically more predictive accuracy.
