# Fourth Down Edge — Product gap, beyond passing engineering tests

**Decision:** Release 0 / Release 1A are infrastructure and research-UX gates,
**not** a completed NFL betting research product. Shipping more technical
screens without a coherent real-data decision journey does not satisfy the
user's stated objective.

## What the repository actually supports

- A sophisticated append-only research engine, a model registry, market
  observations, a forward-test record, and several correctly disclosed
  technical safeguards.
- A **separate** browser demo with fictional players and seeded numbers.
- A real-game detail page in this handoff that is read-only and depends on
  the same backend. It cannot place a bet, value an independently supplied
  bet365 line, or give an approved betting recommendation.
- No verified production odds subscription, populated 2026 season live
  capture, real-fixture paper-bet ledger, or approved predictive strategy.

## What a useful everyday experience needs

| User question | Required product response | Honest fallback |
|---|---|---|
| What games matter today/this week? | Real fixtures with schedule source and kickoff, **market data timestamp** | Schedule available; market feed missing/stale |
| What is the case for/against a market? | Supported features, injuries/availability, market movement, model distribution and the major contrary factor | Input missing or model not approved; no fabricated probabilities |
| Is the bookmaker price I entered acceptable? | Manual market/side/line/price, equivalent break-even, model uncertainty, timestamp, sensitivity analysis | DATA INCOMPLETE; user may save a paper observation, not a BET decision |
| What did we record and learn? | Immutable paper entry linked to game + vintage + manual quote; settlement provenance, CLV, calibration, results | No settlement until reliable final score and price evidence |
| What about a same-game parlay (SGP)? | **Separate future scope** requiring *joint*, correlated player/game distributions and licensed prop prices | No parlay edge calculation or leg-multiplication shortcut |

## Product-quality acceptance criteria for the next major release

1. From an up-to-date NFL matchup, reach real Game Lab in one click. The user
   can see when every underlying observation was obtained.
2. Show at most one primary action per screen. Technical diagnostics remain
   available but are not the daily home page. Use plain-language explanations
   as well as inspectable math and provider provenance.
3. A user-entered bet365 spread, total or moneyline price is stored **only**
   as a manually observed quote and a voluntarily chosen *paper* entry.
   Neither sportsbook automation nor currently available price is implied.
4. Replay the same point-in-time inputs and reproduce the same research
   output across refreshes. A missing or conflicting record must **not** turn
   into a green recommendation or silent fallback.
5. Each candidate's score/edge is tied to an immutable model version and
   measured out-of-sample calibration. Passing calculations tests alone
   cannot authorize real-money use.
6. Desktop and mobile E2E run on an actual backend and real-data-shaped
   fixtures, without test suite cross-contamination. The result of missing
   permissions, API key, or market feed is explicitly actionable.
7. The actual deployment must contain a hosted Python service and secure
   secret configuration. GitHub Pages only hosts the old static frontend.

## Scope decision before coding SGP features

The existing app explicitly says it does **not** recommend parlays. A
consumer-facing NFL betting assistant with SGPs would therefore require a
separate specification and data/model validation, **not** simply enabling
parlays in the existing UI. Independence-based multiplication of individual
leg probabilities understates or overstates correlated outcomes. Player
prop source availability, injuries, depth charts, participation, game state,
line consistency, bookmaker prices, joint calibration and limits must be
addressed before any numerical SGP edge is displayed. Mark the capability
**NOT IMPLEMENTED** until all these gates are passed.
