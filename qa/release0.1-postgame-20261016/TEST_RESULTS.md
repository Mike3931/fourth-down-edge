# Test results - Release 0.1

Release disposition: FAIL. Postgame arithmetic and preservation pass, but the full browser acceptance gate remains red. Do not merge, deploy, install, or describe this as a completed release.

| Check | Actual result | Evidence |
|---|---|---|
| Full offline backend run | 1602 passed, 2 failed, 8 skipped, 52 deselected | backend-full.log |
| Corrected migration/workspace failures plus integrity regressions | 36 passed | final-regression.log |
| Prospective, workspace and official-source guards | 43 passed | final-focused.log |
| War Room, integrity and corrected regression group | 39 passed | corrected-regressions.log |
| Latest async-route/integrity regression group | 32 passed (quiet output) | async-regression.log |
| Sync and async briefing worker guard tests | 2 passed | gate-tests.log |
| Frontend unit checks | 239 passed (177 calculations, 62 API client) | frontend.log |
| TypeScript and production build | PASS | build.log and browser build output |
| Focused prospective browser lifecycle | 3 passed | browser-tests.log |
| Final full browser run, 6 workers | 31 passed, 3 failed | browser-async-gate.log |
| Final real-engine browser run | 7 passed, 2 skipped | live-async-gate.log |
| Mypy | PASS, 90 source files | mypy-async.log |
| Ruff: src, tests, new attestation script | PASS | lint-scoped.log |
| Broader Ruff including all old scripts | FAIL, existing import order in scripts/espn_live_capture.py:392 | lint-async.log |
| Actual DB integrity, historical preservation, duplicates | PASS | preservation.json, preservation-final.log |
| Actual three-model arithmetic and repeat execution | PASS, first 3 / repeat 0 | execution.json |
| PostgreSQL and remote CI on these changes | NOT RUN | No local PostgreSQL runtime; changes have not been pushed |

The two full-backend failures were stale migration-head and positional INSERT test assumptions after the additive migration. Both were corrected and passed targeted reruns. There has not been a fresh all-green full backend invocation after those corrections; do not present the initial full run as green. Eight skips require unavailable historical schedule artifacts; 52 deselections include network and PostgreSQL cases.

Final browser failures:
1. Game Lab mobile refresh/accessibility test: Refresh analysis remained disabled while the briefing request was outstanding.
2. Slate live-screen test: heading did not appear within its existing five-second assertion deadline.
3. Candidates disclaimer test: neither the real-data disclaimer nor unavailable state appeared within five seconds.

Earlier attempts (browser-all.log, browser-all-recheck.log, browser-serial.log, browser-final.log and earlier live-engine logs) remain retained. Initial runs showed connection-pool exhaustion and concurrent SQLite graph materialization. Restarting or redirecting stdout alone did not fix this. The SQLite guard now waits before a connection, with an async gate that waits outside the shared sync worker pool. Dedicated tests pass, but this is only a partial mitigation: final War Room engine test took 58.1 seconds. The responsiveness defect remains unresolved. No test deadlines were relaxed.

The final live-engine skips are captured model comparison and current public-capture navigation requirements unavailable in this cohort. They do not invalidate the actual SEA-DEN manual readback, but they are not passed tests. game-lab-final-ui.txt and game-lab-final.jpg show the real final score, three evaluations, correct signed errors, lifecycle state, and NO BET. Endpoint data came from the populated authorized workspace through a read-only API connection, not demo fixtures.

Reproduction (repository root, API already serving authorized workspace read-only on 8016):

    $env:FDE_RESEARCH_API='http://127.0.0.1:8016'
    $env:FDE_E2E_PORT='4190'
    npx playwright test
    $env:FDE_E2E_PORT='5190'
    npx playwright test --config=playwright.live.config.ts

Choose unused test ports; do not disturb the user-facing dev server on 5173. Do not run test migrations against the original populated database. Preserve failed evidence when rerunning.
