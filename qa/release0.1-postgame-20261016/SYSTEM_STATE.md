# Release 0.1 initial state

Initial checkout: 607dcb6e6a0e5f19331285c896d512ca54d43f87; local branch codex/release0-1-postgame-validation created for the correction. Existing untracked integration/tonight QA directories retained. No master, installed application or external deployment changes.

Authorized database: apps/api/data/research_1b4_final_20261014.db. Initial census: 345 predictions, 115 feature snapshots, 21 model versions, zero model evaluations. SQLite integrity ok and no foreign-key violations. Evidence: initial-state.json.

Recoverable pre-change SQLite backup: prospective-before.db, SHA256 05a96418f1fffd33b213c4f228b340d7691676558e3796f610fe8d9ce948022b. workspace-marker.json retains workspace identity metadata. Restore only to a separate recovery location first; never overwrite an active database or silently reuse a copied file identity. Original database and retained study were never migrated.

Architecture: run_research.py provides bounded manual cycles with an exclusive worker lock; espn_live_capture.py resolves fixture identities and records final observations through ingest_result; nflverse exports corroborate the schedule and score. prospective.py publishes actual-time forecast/model/feature graphs and evaluates observed uncontested finals. FastAPI serves the research API and Vite proxies localhost:5173 to loopback port 8016. No recurring worker was installed.

Confirmed gaps: eligibility lacked complete forecast/model/feature attestation checks; evaluations lacked a database-enforced logical forecast/result pair; UI lacked explicit evaluation lifecycle and signed errors. Correction adds immutable forecast_freezes, a nullable historical-compatible unique evaluation identity, fail-closed eligibility, signed errors and lifecycle display. New forecasts are sealed atomically; no historical predictions are regenerated.

The old baseline reconciled exactly. Only its three explicitly attested forecasts are adopted. Other historical records remain stored and blocked from evaluation without equivalent evidence. See FORECAST_INTEGRITY.md.

Final operational validation: additive schema is fb0100000001; final census is 345 predictions, 115 features, 21 models, 3 evaluations and 3 freezes. The API remains read-only on loopback 8016. SQLite briefing reads now serialize before connection acquisition and queue asynchronously outside shared sync workers. This partially mitigates contention but does not satisfy browser responsiveness acceptance. Release status FAIL; see TEST_RESULTS.md and RELEASE_0_1_HANDOFF.md. All original-data preservation checks were rerun successfully at the end.
