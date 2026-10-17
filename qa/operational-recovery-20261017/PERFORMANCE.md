# Measured performance

Before measurements used the populated authorized DB read-only through an isolated baseline API on 8017. Its source was reconstructed from HEAD plus preexisting-work.patch and the pre-existing forecast-integrity module. After measurements used the same database and current API on 8016. No provider calls or source reduction were used. endpoint-times.json retains the initial paired comparison; endpoint-final.json retains the final measurements. Timing includes client construction, HTTP and JSON response transfer; browser timings separately include visible UI readiness. No timeouts were increased.

| Same endpoint | Before seconds (median of 3 warm reads) | After seconds (median of 3 warm reads) |
|---|---:|---:|
| `/v1/research/briefing` | 2.682 | 0.881 |
| `/v1/forward/slate` | 0.849 | 0.386 |
| `/v1/forward/candidates` | 0.696 | 0.393 |

Six mixed concurrent briefing/slate/candidates requests: maximum before 8.413s; final after 1.665s. The prior October 16 browser evidence included a 58.1s War Room test; that is a historical failing-run measurement, not the controlled baseline above.

Final selected HOU-JAX preparation median: 0.604s. The old API ignored a canonical-game query and generated the global briefing. The after API selects only the target within the unchanged current seven-day window. Past games still show their frozen forecasts and disclose unavailable current preparation; no historical inputs are rewritten.

browser-journey-times.json proves two full journeys with every ordinary step below 2000ms. First browser entry is recorded separately. manual-journey.json, war-room-ui.txt, war-room.jpg and game-lab.jpg are from direct browser use of localhost:5173. There were two locator-selection failures in the inspection tooling; those attempts are excluded from the timing set, and the journeys were completed using the visible accessibility links.

Profiling identified ORM materialization, JSON decoding and repeated historical scans. A fresh optimized direct briefing measured 1.183s, then 0.417-0.532s on repeated reads. early-optimized-profile.* are an intermediate profile, not the original baseline. profile-run.log preserves the initial 2.676s profiling measurement; the controlled HTTP baseline and exact preserved-code equivalence check provide reproducible before/after evidence.

The fact cache stores immutable payload data only, with four bounded revisions. It keys on database identity plus main/WAL/journal stat revision and selected source IDs. Writes, replacement, other databases and uncommitted/explicit transactions refuse reuse. Tests cover rollback-journal and WAL commits, pending Core writes/rollback, source scoping and immutable returned values. Cutoff/source selection, identity reviews, freshness, missing data and decisions are recomputed per read. It holds no database session/connection and caches no final eligibility decision.
