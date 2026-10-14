# Release 1B.2 — automated NFL intelligence

Baseline: integrated `5848b156c010793a8ae2427da635257bdbc6cef6`. Dedicated branch `codex/release1b2-player-research`. Source review only; no installation, deployment, master changes, original database migration or automatic model promotion.

## Working research pipeline

The bounded research worker captures public ESPN fixtures/quotes/finals, the complete available 2026 nflverse regular-season schedule, player reference IDs, rosters, weekly team/player production and EPA totals, completed-game offensive snap shares and secondary injury rows. Applicable NWS forecasts remain separately sourced. It prepares immutable per-game evidence manifests and connects them to the War Room and real Game Lab. NO BET remains explicit: data preparation does not produce probabilities or qualify a current TN offer.

Live acceptance challenged the initial implementation: schedule capture alone did not mark prior nflverse games final, so the completed-game gate correctly withheld player production. Intelligence normalization v2 imports published final scores through the existing governed result service at the actual capture time, refuses conflicting/invalid finals without correction authority, and withholds affected research pending review. Earlier cutoffs cannot see these newly captured results. Preliminary v1 scratch evidence remains preserved; final acceptance must use v2.

Final normalization v3 additionally refuses ambiguous same-week multi-team roster claims with detailed review evidence. Reads can reuse a completed same-day manifest for at most 15 minutes, after checking all relevant source/identity/observation watermarks and source expiration. Failed/running attempts, future or changed evidence and expired vintages cannot become a current cached preparation. Final acceptance uses v3; the v1/v2 isolated prototype evidence remains preserved.

Final accepted normalization is v4: each HTTP source is first captured when its response is received, never at the earlier cycle/request start. A delayed-response regression explicitly excludes the bytes from a cutoff during the download. This avoids backdating later datasets in a long research cycle. Earlier v1/v2/v3 prototypes remain isolated evidence, not release acceptance.

Every public dataset is retained through the existing immutable raw artifact and ingestion-manifest infrastructure. New research sources/facts preserve publication time, first capture, season/week, canonical team/player/game references, original row fields, content hashes, coverage and identity-context hashes. The publication time in a downloaded artifact is its release time, not an individual injury report's issue time. Absent, future or stale publication is surfaced. No mutable export is retroactively assigned to an earlier assessment date.

GSIS remains the canonical player key. The nflverse player catalog supplies explicit GSIS/ESPN/PFR IDs, never name matches. Payload-wide and existing-binding collisions, birth-date disagreements, unmapped players, malformed weeks/teams, conflicting rows and invalid numeric/snap values are withheld with review evidence. Existing canonical player reference values are not rewritten. Player position contradictions are withheld in game preparation. Trades change the timestamped roster vintage rather than rewriting a historical assignment. Names are presentation only.

Identical payloads with identical mapping context reuse their original vintage; a recovered/changed identity context creates a new normalization vintage at recovery time, retaining original rejected evidence. Source polls record successful/failed attempts without freshening first-seen timestamps. Per-game manifests include source/fact IDs, prepared values, cutoff, environment/market evidence and missing-input reasons; completed runs reference the prepared evidence. PostgreSQL and SQLite migration triggers prohibit updates/deletes of sources, facts, player bindings/review flags and game manifests, including raw SQL mutation.

Legacy `load_rosters_parquet` now filters the requested season and only inserts missing reference players/seasonal entries. It no longer overwrites reference state or relies on a primary-key-less `merge` that duplicates rows on repeat loading. Legacy rows with unknown observation times remain unknown; they are not backfilled into point-in-time features. Current revisions belong to the new timestamped observations. Frozen studies and their canonical projections are preserved.

New research uses shared canonical team/game identity, immutable raw manifests, PIT guards, existing injury observations and the existing model/feature governance boundary. Team EPA values are published nflverse production totals; offensive snap shares are published snap-count fractions. No competing prediction builder or invented efficiency adjustment is introduced. Existing trained feature/model contracts remain unpromoted: new descriptive inputs do not silently flow into frozen model evaluations.

## Availability correctness and limitations

The injury/availability service now versions new derivations as `availability-v1`. OUT means EXPECTED_INACTIVE, never confirmed inactive. Explicit INACTIVE requires a sourced, time-stamped OFFICIAL_VERIFIED observation. Later-entered and future-issued observations are excluded from earlier cutoffs. Existing stored observations/assessments, including availability-v0 records, are unchanged; no automatic reassessment runs.

Game preparation links verified recorded observations only to resolved players on the applicable team's usable roster and game identity, requires admissible issue time, and withholds contradictory equal-time reports. Secondary nflverse injury rows are linked by season/week/team and GSIS to one canonical game; they retain secondary/unverified status because report-level timestamps and authoritative completeness are not established. A successful download cannot confirm healthy status, a complete report or an inactive list. The live acceptance report identifies actual available weeks and counts; current-week official coverage can remain unavailable.

No restricted NFL/team endpoint, bet365 website or authenticated account is scraped. Automatic official-report and inactive-list ingestion remains an explicit gap until permission-compatible access, durable source IDs, issue timestamps and completeness are independently accepted. Existing authenticated structured injury intake remains available; its content is not automatically proof of an official complete report.

## Safe bounded automation

Initialize a **new nonexistent** isolated database from `apps/api`:

```
uv run python scripts/init_research_workspace.py --database-file data/research_1b2.db
uv run python scripts/run_research.py --database-file data/research_1b2.db --season 2026
```

For bounded foreground refresh, add `--cycles 32 --interval 900`. Maximum 96 cycles, minimum five-minute cycle spacing, existing exclusive process lock and workspace UUID/filesystem identity checks remain. January–July default to the preceding NFL season; `--season` explicitly selects one. There is no OS background installation or scheduler activation.

Persisted source polling: schedule 15 minutes; player catalog 24 hours; roster/team/player/snap/injury exports six hours. Failed sources retry after the smaller of their interval and 30 minutes. Each cycle makes at most seven research-source requests with 25-second timeouts, in addition to existing bounded ESPN/NWS calls. Provider failures are isolated with nested transactions and retained poll evidence; successful independent sources survive. A cycle with research-source failures is recorded failed, not completed/current. Restarting the foreground worker uses persisted due times. Existing hard-kill lock recovery still requires confirming the prior process stopped before manually removing its lock.

GET requests remain read-only. The worker prepares data before the app is opened; UI refresh rereads prepared/known evidence and never impersonates a provider refresh. A failed API refresh hides cached findings; a recorded provider failure labels retained evidence historical. Future-captured facts/bindings and later corrections cannot enter an earlier cutoff. Upcoming games only use prior linked completed-game stats, matched current roster players and same-game snap observations. Routes and comprehensive participation remain unavailable.

## User journey

War Room → real matchup → Game Lab shows the last three available completed team games, recent mapped player usage/production and available matched offensive snap share. It identifies the represented week, mapped roster count, incomplete availability, source publication/capture and withheld-row counts. Production lists are bounded for readability and disclose displayed versus total counts; they are not recommended prop selections. Source and missing-input details are expandable. Run comparisons identify changes in research evidence rather than claiming nothing changed after failures.

## Provider feasibility — documentary, not feed acceptance

- Public nflverse data is distributed under its repository's CC-BY-4.0 license; source links/attribution are retained. Its documented participation feed for 2023+ arrives after the postseason; it is not a live-season substitute. Public snap counts have separate coverage and PFR-ID mapping requirements.
- SportsGameOdds advertises bet365 on Pro ($299/month at review), with props and alternate lines. General catalog advertising does not establish Tennessee NFL market coverage, exact TD contracts, permitted usage/retention, suspension timestamps, sportsbook offer availability or exact combined SGP pricing.
- OpticOdds documents bet365 for general odds; its explicit SGP endpoint sportsbook list omits bet365. Its AI/consensus parlay product is not an exact bet365 combined quote.
- No reviewed public contract establishes an accepted free automatic TN bet365 singles/props/alternate/TD/SGP connector. Those categories remain UNVERIFIED; no provider activation, purchase, account creation or sales outreach occurred. Actual combined SGP pricing and combinability require separately authorized feed/sample acceptance; multiplying singles cannot substitute.

Primary references: [nflverse data/CC-BY attribution](https://github.com/nflverse/nflverse-data), [update/participation schedule](https://nflreadr.nflverse.com/articles/nflverse_data_schedule.html), [player-ID scope](https://nflreadr.nflverse.com/reference/load_players.html), [SportsGameOdds pricing](https://sportsgameodds.com/pricing), [OpticOdds general odds](https://developer.opticodds.com/docs/odds-api-getting-started-guide), [explicit SGP coverage](https://developer.opticodds.com/reference/post_parlay-odds).

## Acceptance gates and next dependencies

Require all mandatory existing CI gates, new PostgreSQL player-binding contention and immutable-trigger checks, PIT/identity/roster/correction/refusal regressions, frontend/build, browser and actual-engine checks. Local real-source acceptance must report actual counts/represented weeks, repeat ingestion, retained lineage and desktop/mobile evidence. These results and final source SHA belong in the PR and final acceptance report, not inferred from code existence.

Remaining dependencies: accepted official availability/inactive coverage; complete applicable current-week data and routes; licensed/authorized exact TN singles/prop/alternate/TD quote semantics; exact SGP leg combinability/combined quote; qualified market-specific and joint models with out-of-sample/prospective calibration and approval. NO BET remains the correct outcome while these gates are closed. The original populated database has a different/newer migration lineage and must not be migrated/downgraded by this source branch. The three historical evaluation conflicts remain unresolved.
