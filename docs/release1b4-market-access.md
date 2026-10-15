# Release 1B.4 — Tennessee bet365 access decision

Reviewed 2026-10-14 against public primary sources. **No Tennessee NFL market quote or exact bet365 SGP quote has been accepted. NO BET remains enforced.** This is a feasibility result, not evidence that the consumer markets do not exist. No accounts, credentials, paid trials, purchases, vendor messages, sportsbook sessions or restricted endpoints were used. The previous [Release 1B.3 assessment](release1b3-market-access.md) remains preserved.

## Evidence levels and market scope

“Verified” means an actual response observed in this investigation. “Documented” means the vendor describes a capability; it does not establish the intersection of NFL, bet365, Tennessee and the specific contract. “Trial required” means a permitted entitlement and sample are needed, not that a free trial has been authorized. “Unsupported” below is limited to the reviewed published product scope.

| Provider | Actually verified | Documented NFL singles, props, alternates and TD coverage | Tennessee / exact SGP | Access decision |
|---|---|---|---|---|
| OpticOdds | Public documentation only; no quote response | General bet365 listing; discovery APIs support book/league/market filters and game/player markets. Exact TN moneyline/spread/total, alternate thresholds, passing/rushing/receiving/receptions and TD inventory remains untested. [API guide](https://developer.opticodds.com/docs/odds-api-getting-started-guide) | No explicit TN sample. Published supported-SGP book list **omits bet365**. A sportsbook-keyed pricer exists, but AI consensus and Copilot configuration prices are independent products, not proof of bet365 offers. [SGP guide](https://developer.opticodds.com/docs/same-game-parlay-sgp-pricer-bet-builder) | License key and written market/jurisdiction entitlement required; [pricing](https://opticodds.com/pricing) requires contact. No accepted no-cost trial. Do not build the target connector before TN/rights confirmation. |
| SportsGameOdds | Public documentation only; no quote response | General NFL/bet365 and ML/spread/total, alternates, passing/rushing/receiving/defense/kicking props, anytime/first/last/period TD markets advertised. Exact book/state intersection untested. [Football coverage](https://sportsgameodds.com/sports/football-odds-api) | TN unconfirmed. No exact bet365 SGP quote/eligibility API established from reviewed documentation. A generic parlay builder is insufficient. | Free Amateur's listed books exclude bet365. Pro explicitly lists bet365: $299/month, advertised sub-minute updates and 300 requests/minute; trial advertised, but key/rights/payment conditions untested. [Pricing](https://sportsgameodds.com/pricing) |
| Odds-API.io | Credential-free bookmaker catalog HTTP 200; only international Bet365 and bet365 NJ entries matched | [NFL page](https://odds-api.io/sports/nfl) advertises main/alternate lines, player yardage/TD categories and SGP; no accepted per-book NFL inventory. [Bet365 guide](https://odds-api.io/blog/bet365-api) explicitly distinguishes international from NJ | Neither catalog entry identifies TN. NFL SGP advertising does not establish an exact TN combined-quote endpoint, eligible leg contract or returned offer | Key required for markets. [Pricing](https://odds-api.io/pricing) says new free keys are paused; existing free keys have 100/hour and 500/day limits. Paid plans and a money-back guarantee are not authorized free access. Displayed currencies vary; confirm a written quote before any purchase decision. |
| The Odds API | Public bookmaker catalog documentation | Published bet365 product is paid `bet365_au`, limited to AFL/NRL ML/spread/total. **Unsupported for the requested TN NFL target under current published scope.** [Bookmaker catalog](https://the-odds-api.com/sports-odds-data/bookmaker-apis.html) | No TN NFL bet365 or exact SGP capability established | Other-book NFL data cannot substitute for the requested bet365 offer. No activation warranted for this target. |

## Actual public catalog refresh

`GET https://api.odds-api.io/v3/bookmakers`, captured **2026-10-14T23:46:48.8679156Z**, returned HTTP **200**, **430** entries. SHA-256 of the UTF-8 response text: `9cf614dada0d2402b648c1f1d2d766ee05de692f3ecdba2ef0d7440425495c80`.

The complete selected bet365 metadata, with no credentials or prices:

```json
[
  {"name":"Bet365","active":true,"createdAt":"2025-10-11T11:38:05.549931Z"},
  {"name":"bet365 NJ","active":true,"createdAt":"2025-10-14T06:32:00.244395Z"}
]
```

Release 1B.3 recorded 429 catalog entries; this refresh recorded 430. The relevant two entries remain the same. `createdAt` describes catalog metadata, not price establishment or quote freshness. Only the selected fields and response digest are retained here; the complete response is not an archived market dataset. **Verified quote count: 0 singles, 0 SGP; verified Tennessee market coverage: 0.** The catalog proves no offer, entitlement, jurisdiction equivalence or account availability.

## Rights, timestamp and settlement blockers

- SportsGameOdds [terms](https://sportsgameodds.com/terms), Version 3 dated June 23, 2026, restrict derivatives and competing models, require consent for published benchmarks, and require deletion of service data/backups within 30 days after termination with narrow exceptions. This conflicts with the app's indefinite raw-evidence retention unless a written license exception permits it. Trial charges can start automatically; payment information may be required even for free access. Marketing support for modeling does not resolve these terms.
- OpticOdds [public terms](https://opticodds.com/terms-of-service) restrict commercial/revenue-generating use. Obtain the applicable API agreement for private modeling, immutable retention and derived displays; do not infer contractual permission from enterprise advertising. Odds-API.io [terms](https://odds-api.io/terms) also need an applicable upstream/jurisdiction and retention-rights confirmation.
- OpticOdds' [odds response](https://developer.opticodds.com/reference/get_fixtures-odds) provides per-odd `timestamp`; its [history endpoint](https://developer.opticodds.com/reference/get_fixtures-odds-historical) is separately entitled. A documented timestamp field does not establish sportsbook-origin semantics or accepted TN historical depth. Keep sportsbook establishment time, vendor observation/update time and application receipt time separate; missing origin time remains unknown. No captured historical quote may be labeled a currently executable offer.
- Official [bet365 NJ SGP help](https://help.nj.bet365.com/s/en-us/sports/bet-builder) describes combined prices that account for dependence, limits on legs/players, and conditional void/recalculation behavior. This is **NJ documentation**, useful evidence of the contract complexity, not authoritative TN rules or a TN API. Retrieve the applicable Tennessee rules/version before writing a settlement contract; never copy NJ behavior into TN automatically.

## Exact external decision and acceptance gate

**Recommended owner decision: authorize a no-cost feasibility inquiry, or supply an already entitled evaluation key, only after written TN and usage-rights answers. Do not purchase a plan yet.** No inquiry has been sent. OpticOdds and SportsGameOdds are candidates for this limited inquiry; neither is accepted for SGP. Odds-API.io needs TN clarification before a trial has value.

The vendor must provide:

1. Explicit bet365 **Tennessee** book/jurisdiction IDs and upstream authority; no international/NJ substitution. Identify actual NFL moneyline, spread, total, alternate, passing/rushing/receiving/receptions and TD contracts and excluded categories.
2. Permission for permanent immutable raw/source retention, private research displays, statistical model training/evaluation, redacted acceptance evidence and continued audit use after subscription ends. Resolve deletion obligations before collecting data.
3. A no-billing trial scope and credentials supplied through secure configuration, plus eventual price, quotas, update guarantees and history depth. Do not put keys in code, URLs, screenshots or logs.
4. Representative current two-sided singles samples with exact lines, book event/market/selection IDs, jurisdiction, periods, OT/DNP/void rules, suspension state, book/vendor/capture times and known timestamp semantics.
5. For SGP: requested and accepted leg sets, rejection reasons, actual unboosted **bet365 TN** combined price, quote ID, timestamp/expiry, line-change behavior, account/availability limits, and settlement/repricing rules after inactive/void legs. A proprietary fair-price output cannot satisfy this.

After authorization, test only in an isolated research workspace: complete pagination, canonical game/player mapping, retries, revisions, stale/suspended quotes, changed lines and rejected combinations. Compare permitted samples with a contemporaneous owner-visible TN offer. Preserve raw evidence only as licensed. **Any failed identity, rights, market freshness, model or joint-pricing gate retains NO BET.** This investigation does not authorize outreach, activation or implementation of an unverified adapter.
