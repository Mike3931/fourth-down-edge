# Temporal-integrity audit

Every temporal visibility comparison in the analytical engine, classified.
Produced under the model-integrity gate of 2026-08-03.

The distinction that runs through all of it:

* An **observation vintage** — an odds snapshot, injury report, weather
  forecast, schedule observation — stamped exactly at the cutoff *was*
  available at the cutoff. Inclusive (`<=`) is correct.
* A **completed-game result** must be strictly in the past (`<`). At
  `now == kickoff`, an inclusive test admits the game's own outcome into
  the state used to predict it.

Conflating the two is what produced the defect this gate exists to close.

## Classification

| # | Location | Comparison | Class | Boundary test |
| --- | --- | --- | --- | --- |
| 1 | `pit/clock.py` `ReplayClock.can_see` | `observed_at <= now` | **Intentionally inclusive** — observation vintages | `test_sequential_replay_leak.py::test_a_result_at_exactly_now_is_not_visible` (asserts inclusive here, strict elsewhere) |
| 2 | `pit/clock.py` `ReplayClock.can_see_result` | `observed_at < now` | **Intentionally strict** — results (added by this gate) | same test, plus `test_a_result_strictly_before_now_is_visible`, `test_a_naive_timestamp_is_never_visible` |
| 3 | `pit/clock.py` `advance_to` | `instant < self.now` → raise | **Intentionally strict** — the clock may never rewind | `test_leakage.py` |
| 4 | `backtest/walkforward.py` result visibility | `can_see_result` (strict) | **Defect corrected** — was inclusive `can_see` | `TestSimultaneousKickoffs`, `TestNoGameInformsItsOwnPrediction` |
| 5 | `backtest/walkforward.py` invariant check | `result_observed_at <= kickoff` → raise | **Intentionally strict** — malformed record | `TestTheInvariantIsEnforced` (3 tests) |
| 6 | `backtest/walkforward.py` ordering | `(kickoff, id)` / `(result_observed_at, id)` | **Defect corrected** — was input-order dependent | `test_reversing_the_order_changes_nothing` |
| 7 | `canonical/load_games.py` ingest guard | `result_observed_at <= kickoff` → raise | **Intentionally strict** — added by this gate | `test_ingest_rejects_bad_result_time` |
| 8 | `features/builder.py:243` | `result_observed_at <= as_of_at` → raise | **Intentionally strict** — a snapshot may not see its own game's result | `test_leakage.py` |
| 9 | `features/builder.py` snapshot cutoff | `assert_no_lookahead(as_of, kickoff)` | **Intentionally strict** — except `CLOSING_CAPTURE`, which is evaluation-only and documented | `test_leakage.py` |
| 10 | `features/history.py:149,153` team rows | `r.observed_at <= as_of_at` | **Intentionally inclusive** — prior-game observations; also excludes the subject game by id | `test_leakage.py` |
| 11 | `pit/guards.py:55` `assert_no_lookahead` | `ts <= as_of_at` | **Intentionally inclusive** — asserts data is at or before the cutoff | `test_leakage.py` |
| 12 | `forward/consensus.py:262` | `ConsensusSnapshot.observed_at <= as_of_at` | **Intentionally inclusive** — market vintage | `test_forward_capture.py::test_future_quotes_never_enter_earlier_consensus` |
| 13 | `forward/consensus.py:293` closing selection | `observed_at <= kickoff_utc` | **Intentionally inclusive** — the closing capture is defined *at* kickoff and is evaluation-only | `test_closing_selected_by_rule_not_by_outcome`, `test_closing_capture_excluded_from_prediction_inputs` |
| 14 | `forward/injuries.py:189` | `observed_at <= as_of_at` | **Intentionally inclusive** — injury vintage | `test_forward_capture.py` |
| 15 | `forward/weather.py:231,303` | `observed_at <= as_of_at` | **Intentionally inclusive** — weather / roof vintage | `test_forward_capture.py` |
| 16 | `forward/schedule.py:308` | `observed_at <= as_of_at` | **Intentionally inclusive** — schedule vintage | `test_forward_capture.py` |
| 17 | `forward/handlers.py:619` closing window | `(kickoff - window) <= now <= (kickoff + 10m)` | **Intentionally inclusive** — an operational scheduling window, not a data-visibility rule | `test_record_chain.py` |
| 18 | `forward/health.py:415` upcoming filter | `kickoff - 6d <= now < kickoff` | **Not applicable** — a display/health filter over future games; strict upper bound already excludes started games | `test_record_chain.py` |
| 19 | `db/models.py:145` (comment) | documents `observed_at <= as_of_at` | **Not applicable** — comment | — |

## Items 12–16: why inclusive is right there

These select the newest vintage at or before a cutoff. A quote recorded at
exactly the cutoff instant was genuinely observable then, and excluding it
would discard real information and make the engine pessimistic about what
it knew. None of them can admit a *result*: they read observation tables
that contain no game outcome.

## Item 13 deserves its own note

`observed_at <= kickoff_utc` selects the closing consensus, which is
deliberately defined *at* kickoff. That is inclusive on purpose and is why
`CLOSING_CAPTURE_EVALUATION_ONLY` exists as a separate horizon: the
closing capture is a benchmark, never a prediction input. Two tests pin
that separation.

## What changed under this gate

Items 4, 6 and 7. Item 4 (strict result visibility) and item 7 (ingest
guard) produced **no change** to any historical result. Item 6
(deterministic ordering) **did** — see the certification report.

## Residual note

`ReplayClock.can_see` (item 1) now has no production caller; the replay
uses `can_see_result`. It is retained as the general observation-vintage
primitive and is directly tested. If it acquires a caller, that caller
must be reading a vintage and not a result.
