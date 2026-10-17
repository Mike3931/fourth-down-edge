# Final acceptance

| Gate | Result | Evidence |
|---|---|---|
| Full mandatory backend selection on final source | 1610 passed, 8 data-dependent skipped | backend-final.log / backend-final.xml |
| Initially skipped schedule cases, supplied retained raw artifact | All 8 executed; 10 passed in file | retained-schedule.log / retained-schedule.xml |
| PostgreSQL identity, chain and real multi-connection concurrency | 52 passed, zero skipped | postgres-tests.log / postgres.xml |
| Fresh PostgreSQL migration / downgrade to prior / re-upgrade | PASS | pg-migrations.log |
| Direct/scheduler semantic parity | 1 passed, zero skipped | parity.log / parity.xml |
| Full browser suite | 36 passed | browser-complete.log |
| Populated real-engine and two-second repeated journey | 9 passed, 1 optional historical-study case skipped | live-accepted.log / browser-journey-times.json |
| Frontend units | 239 passed | frontend.log |
| TypeScript + production build | PASS | build.log, browser build output |
| Frontend SQL/RLS migration checks | PASS | web-db-migrations.log |
| Bundle secret audit | PASS | bundle-audit-final.log |
| Production dependency audit high/critical gate | PASS; 2 existing moderate advisories | dependency-audit.log |
| Ruff and Mypy | PASS, 92 source files typed | lint-accepted.log / mypy-accepted.log |
| Complete preserved-vs-optimized readback equality | PASS | equivalence.log |
| Actual DB integrity, hashes, immutability and no duplicates | PASS | preservation-final.json / idempotency.json |

Reproduction follows .github/workflows/deploy.yml: full backend selects `not network and not pg_concurrency and not pg_chain and not pg_identity`; the three PostgreSQL marker groups run separately and cannot fall back to SQLite. A temporary official EDB PostgreSQL 16.15 runtime served disposable fde_test/fde_races databases on 127.0.0.1:55432. It did not use the original research DB or install a service. Vendor archive SHA256: 43bb45f173a6f08cf1d29a97a6d8deb119e8e8093a24c00d2d1001a0ccaa8281. Source: https://www.postgresql.org/download/windows/ and https://www.enterprisedb.com/download-postgresql-binaries .

The first recovery browser run was 33 passed / 1 Candidates timeout. Its evidence is retained in browser-after.log. The final SQL Candidates correction passed all tests. The pre-fix focused reproduction passed today with Game Lab taking 10.8s; earlier failures remain documented in the prior handoff. The first full backend process was superseded while building its expensive walk-forward fixture so a clean run could cover every final source change and all new tests; backend-full.log is not reported as a passing gate. A simultaneous-build bundle-audit attempt failed; serial rerun passed and is the accepted result. No tests were weakened, fixture counts reduced, or assertion deadlines increased.

The optional live historical-study case requires a separately wired populated historical study. That study was preserved, not copied or promoted into this prospective workspace. The opt-in REAL_CAPTURE and OPERATIONAL cases were explicitly enabled and passed using actual populated data. The old prospective-only switch assumes zero settled forecasts and was not used for this postgame workspace.

Backend data-dependent skips (not counted as passed):
- tests.test_international_slate.TestNineInternationalGames.test_exactly_nine_international_games: no ingested nflverse schedule artifact available
- tests.test_international_slate.TestNineInternationalGames.test_exactly_eight_international_stadiums: no ingested nflverse schedule artifact available
- tests.test_international_slate.TestNineInternationalGames.test_tottenham_hosts_two: no ingested nflverse schedule artifact available
- tests.test_international_slate.TestNineInternationalGames.test_exact_slate_membership_and_venues: no ingested nflverse schedule artifact available
- tests.test_international_slate.TestNineInternationalGames.test_designated_home_game_abroad_is_international: no ingested nflverse schedule artifact available
- tests.test_international_slate.TestNineInternationalGames.test_no_international_game_keeps_domestic_venue: no ingested nflverse schedule artifact available
- tests.test_international_slate.TestNineInternationalGames.test_international_games_carry_venue_timezone_not_us: no ingested nflverse schedule artifact available
- tests.test_international_slate.TestNineInternationalGames.test_source_location_flag_alone_undercounts: no ingested nflverse schedule artifact available

These eight initially skipped cases then ran successfully using FDE_RAW_DIR=C:/Users/mjcun/NFL/apps/api/data/raw and only tests/test_international_slate.py. The artifact is retained nflverse/games/20260801T125403341745Z-32e68976/games.csv; tests read it and ingest into their own in-memory database. No original database or raw payload was modified. All ten tests in that file passed. This is additional real-fixture execution, not a claim that the earlier full run had zero skips.
