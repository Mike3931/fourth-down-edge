# Fourth Down Edge — Phase 2 analytical engine reports

> RESEARCH MODE — MODEL NOT APPROVED FOR REAL-MONEY DECISIONS

Generated 2026-08-01T13:40:45.703702+00:00 · commit `51144321127e` · lock `955525cd6ac3`

## 1. Data coverage by season

| Season | Games | With results | Team-game stats | Closing odds | Roster rows |
| --- | ---: | ---: | ---: | ---: | ---: |
| 2018 | 267 | 267 | 534 | 1602 | 3141 |
| 2019 | 267 | 267 | 534 | 1602 | 3113 |
| 2020 | 269 | 269 | 538 | 1614 | 3067 |
| 2021 | 285 | 285 | 570 | 1710 | 2960 |
| 2022 | 284 | 284 | 568 | 1704 | 3133 |
| 2023 | 285 | 285 | 570 | 1710 | 3089 |
| 2024 | 285 | 285 | 570 | 1710 | 3215 |
| 2025 | 285 | 285 | 570 | 1710 | 3133 |

## 2. Feature missingness

18 features are declared structurally unavailable:

- `market_consensus_spread` — no licensed historical multi-book odds; nflverse closing lines are evaluation-only
- `market_consensus_total` — no licensed historical multi-book odds; nflverse closing lines are evaluation-only
- `market_no_vig_win_prob` — requires pre-close consensus quotes (adapter contract exists, no feed)
- `market_line_movement` — requires opening + intraweek line history (no source)
- `market_spread_dispersion` — requires multi-book quotes (no source)
- `market_price_dispersion` — requires multi-book quotes (no source)
- `market_quote_age` — requires timestamped quotes (manual-entry or feed only, forward capture)
- `market_eligible_books` — requires multi-book quotes (no source)
- `player_active_probability` — no reliable point-in-time historical injury feed; forward capture only
- `player_expected_snap_share` — participation backfill pending; would be retrospective otherwise
- `player_restriction_probability` — no point-in-time injury designations historically
- `player_replacement_quality` — requires per-player valuation model (later phase)
- `position_group_continuity` — requires weekly depth charts with observation instants
- `weather_temp_forecast` — NWS serves forward forecasts only; historical pregame forecasts unavailable
- `weather_wind_forecast` — NWS serves forward forecasts only
- `weather_gust_forecast` — NWS serves forward forecasts only
- `weather_precip_prob` — NWS serves forward forecasts only
- `referee_crew_effect` — insufficient per-crew sample for unshrunk effects; excluded from v1 by policy

## 3. Leakage tests

- future observation rejected by assert_no_lookahead
- timezone-naive timestamps rejected (ambiguous comparison)
- records with unknown provenance treated as future, never as safely-past
- replay clock refuses to rewind
- a game's own stats can never enter its own features
- future-week results excluded at every horizon
- snapshot build aborts if a result is already observable at the cutoff
- CLOSING_CAPTURE excluded from prediction horizons (evaluation only)
- earlier horizons see no more data than later horizons
- train/validation/test season sets disjoint and ordered
- isotonic calibration refused below minimum sample size

Status: **all passing**

## 4–7. Model performance (test periods, evaluated once)

### test:2024:PREGAME

| Model | n | Log loss | Brier | CRPS margin | Margin MAE | Total MAE | Cal. slope |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| market-residual-v1 | 285 | 0.5935 | 0.2028 | 7.125 | 9.72 | 9.79 | 1.49 |
| market-benchmark-v1 | 285 | 0.5938 | 0.2031 | 7.131 | 9.72 | 9.75 | 1.52 |
| glm-ridge-v1 | 285 | 0.6202 | 0.2149 | 7.331 | 10.02 | 9.96 | 1.30 |
| team-ratings-v1 | 285 | 0.6215 | 0.2159 | 7.392 | 10.16 | 10.05 | 1.36 |
| naive-rolling-v1 | 285 | 0.6575 | 0.2319 | 7.635 | 10.50 | 10.33 | 0.84 |
| naive-homefield-v1 | 285 | 0.6887 | 0.2478 | 8.096 | 11.17 | 10.12 | -20.55 |

### test:2025:PREGAME

| Model | n | Log loss | Brier | CRPS margin | Margin MAE | Total MAE | Cal. slope |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| market-benchmark-v1 | 285 | 0.6083 | 0.2113 | 6.908 | 9.67 | 10.40 | 1.17 |
| market-residual-v1 | 285 | 0.6149 | 0.2138 | 7.037 | 9.82 | 10.50 | 1.02 |
| naive-rolling-v1 | 285 | 0.6130 | 0.2123 | 7.244 | 10.03 | 10.84 | 1.45 |
| team-ratings-v1 | 285 | 0.6335 | 0.2219 | 7.249 | 10.16 | 10.60 | 0.96 |
| glm-ridge-v1 | 285 | 0.6514 | 0.2295 | 7.480 | 10.41 | 10.53 | 0.84 |
| naive-homefield-v1 | 285 | 0.6909 | 0.2489 | 7.971 | 11.00 | 11.00 | 12.12 |

## 9. Model versus market

### test:2024:PREGAME

| Model | Δ log loss | Δ CRPS margin | Δ margin MAE | Beats market (CRPS)? |
| --- | ---: | ---: | ---: | :--: |
| market-residual-v1 | -0.0003 | -0.0058 | 0.0025 | yes |
| glm-ridge-v1 | 0.0264 | 0.1995 | 0.3087 | no |
| team-ratings-v1 | 0.0277 | 0.2609 | 0.4434 | no |
| naive-rolling-v1 | 0.0637 | 0.5038 | 0.7832 | no |
| naive-homefield-v1 | 0.0949 | 0.9644 | 1.4529 | no |

### test:2025:PREGAME

| Model | Δ log loss | Δ CRPS margin | Δ margin MAE | Beats market (CRPS)? |
| --- | ---: | ---: | ---: | :--: |
| market-residual-v1 | 0.0066 | 0.1290 | 0.1462 | no |
| naive-rolling-v1 | 0.0047 | 0.3360 | 0.3601 | no |
| team-ratings-v1 | 0.0252 | 0.3409 | 0.4904 | no |
| glm-ridge-v1 | 0.0431 | 0.5721 | 0.7407 | no |
| naive-homefield-v1 | 0.0826 | 1.0630 | 1.3325 | no |

## 8. Calibration

Candidates (none/platt/beta, isotonic only with n>=800) are fitted on prior out-of-fold validation predictions and selected by log loss there. The identity (no-calibration) comparison is always reported. Calibration is never fitted on the test period, and is applied only to the target it was fitted for (spread cover).

- `cal_beta_val2023` — method **beta**, target spread_cover_prob, fitted on val OOF season 2023, n=271
- `cal_beta_val2024` — method **beta**, target spread_cover_prob, fitted on val OOF season 2024, n=281

## 10–12. Simulated betting (research candidates only)

### bt_4cfbc3643125 (test season 2024)

Statuses: {'PASS': 1083, 'WATCH': 53, 'RESEARCH_CANDIDATE': 4}

Max drawdown: 2.0 units · CLV: unavailable — see limitations

| Market | Bets | Wins | Losses | Pushes | P/L units |
| --- | ---: | ---: | ---: | ---: | ---: |
| SPREAD | 4 | 2 | 2 | 0 | -0.095 |

### bt_0df40f7df767 (test season 2025)

Statuses: {'PASS': 867, 'WATCH': 169, 'RESEARCH_CANDIDATE': 104}

Max drawdown: 14.1305 units · CLV: unavailable — see limitations

| Market | Bets | Wins | Losses | Pushes | P/L units |
| --- | ---: | ---: | ---: | ---: | ---: |
| SPREAD | 52 | 28 | 24 | 0 | 2.019 |
| TOTAL | 52 | 26 | 26 | 0 | -2.13 |

## 13. Closing-line value

**unavailable** — Only closing prices exist historically, and simulated fills are at-or-worse-than close, so CLV would be a tautology (<= 0 by construction). Reporting it would be misleading; it becomes measurable once pre-close quotes are captured forward.

## 15–16. Limitations

### Data

- No licensed historical multi-book odds. nflverse carries CLOSING lines only, used as the evaluation benchmark and residualization baseline — never as features for earlier horizons.
- No line-movement, cross-book dispersion, or quote-age history exists in this phase.
- No point-in-time historical injury/participation/depth-chart feed; those features are declared unavailable rather than backfilled from retrospective knowledge.
- Weather forecasts are forward-only from NWS; historical pregame forecasts cannot be reconstructed without lookahead, so weather features are unavailable for replay.
- Result-availability instants are approximated as kickoff + 4h30m; the source records no publication timestamp.
- Horizon cutoffs are fixed offsets from kickoff, identical for every game, because no intra-week observation timeline exists in the source.

### Models

- All artifacts are research_only. No model is approved for real-money decisions.
- Margin and total are modeled as independent Normals; real NFL margins concentrate on key numbers (3, 7) more than a Normal does.
- The market-residual model is fitted against CLOSING lines, so its 'edge' is measured versus a price a bettor could not have obtained earlier in the week.
- Backtest fills are at-or-worse-than close by construction; closing-line value is therefore structurally unavailable and is reported as such, never fabricated.
- Per-QB efficiency uses team dropback aggregates as a proxy; no per-passer model yet.
- Sample sizes are small (roughly 285 games per test season): reported ROI confidence intervals are wide and consistent with zero edge.
