# Release 1B.3 availability and usage evidence

Review date: 2026-10-14. Research only. This investigation does not establish a licensed automated official feed or attest complete current-week availability. No sportsbook interface was accessed.

## Public source findings

| Need | Evidence checked | Usable now / limitation |
|---|---|---|
| Official practice and injury reports | [NFL injury page](https://www.nfl.com/injuries/) | Public current-week reporting exists. Publication provenance, automated access rights and complete team coverage remain unverified. |
| Official inactive lists | [NFL operations injury reporting policy](https://operations.nfl.com/media/2683/2017-nfl-injury-report-policy.pdf) | Historical policy distinguishes injury updates from the club's submitted inactive list. This 2017 document is contextual, not proof of current feed access or 2026 operating deadlines. Obtain the actual game-specific official list and issuance timestamp before confirming inactive status. |
| Snap participation | [nflverse update schedule](https://nflreadr.nflverse.com/articles/nflverse_data_schedule.html) | PFR-derived counts have four scheduled daily updates; source publication can lag. Existing collector retains captured vintages and mapped player/game evidence. Snaps do not measure routes. |
| Current-season routes / on-field participation | [nflverse participation documentation](https://nflreadr.nflverse.com/reference/load_participation.html) | FTN participation from 2023 onward arrives after postseason completion. It cannot supply live 2026 participation. Historical attribution is FTN Data via nflverse under CC BY-SA 4.0. No current route feed was verified. |
| Expected starters / personnel changes | [nflverse depth-chart schedule](https://nflreadr.nflverse.com/articles/nflverse_data_schedule.html) | From 2025, snapshots are timestamped rather than assigned weeks. A future adapter must preserve both source snapshot and first capture, map provider IDs explicitly, and label depth-chart rank as an expectation, not a confirmed starter. No new collector is enabled in this release. |
| QB and skill-position usage | [nflverse player/team stat schedule](https://nflreadr.nflverse.com/articles/nflverse_data_schedule.html) | Existing exports update with play-by-play and later stat corrections. The new descriptive shares use independently captured player and team totals for exactly the same canonical game, team and week. |

The NFL public page is evidence that official reporting exists, not permission to run a restricted scraper. No new automated official ingestion or claim of complete inactive coverage is made. Existing structured official observations still require a reference and publication timestamp for INACTIVE; OUT remains expected inactive. Secondary injury rows and absent reports cannot establish healthy/active status. Complete report coverage would require a separate team/game/report attestation, not counting observed players.

## Working extension

`forward/usage_evidence.py` computes observed passing-attempt, carry and target shares with explicit numerator, denominator, canonical player/game/team/week and both source/row IDs. It is invoked only after the existing intelligence reader selects eligible captured vintages and completed-game records. It reads or writes no database records.

Missing, zero, fractional, negative and nonfinite denominators are refused. A numerator above its team total is refused. Duplicate matching team rows are ambiguous and withheld rather than selected by arrival order. A missing player value is not zero. The player list is never summed to manufacture a team denominator.

These are one-game descriptions, not forecasts. Routes, expected starter and projected opportunity stay unknown. A team's pass attempts are not routes; targets are not route participation. Existing snap share is a separate observed measure. A player's previous team production is not transferred into the current team's denominator. Vintages remain subject to existing cutoff/freshness reporting; historical observations must not be advertised as fresh opportunity estimates.

Regression coverage: exact same-game computation and lineage; wrong team/game/week/player denominators; ambiguous duplicates; invalid/missing/zero/inconsistent totals; zero versus missing player counts; missing canonical player ID. Source acquisition and model eligibility remain separate controls.

## Next evidence needed

1. An authorized official report input with issuance time, game/team scope, provider player identifiers, explicit completeness and archived revisions. Validate practice, designation and inactive-list documents separately.
2. A mapped, timestamped depth-chart collector; corroborate personnel changes without assuming first-listed equals starting.
3. A current route provider with documented rights and historical vintages, or defer route-dependent models. Evaluate targets/carries/attempts and snaps as separately named alternatives.
4. Prospective role/availability validation before these descriptions become player-prop features. Receiving and rushing experiments need opportunity distributions; touchdowns require sparse-event treatment; SGPs require dependency modeling, not independent multiplication.
