# Result verification

VERIFIED through the two existing approved public research providers: Denver 17, Seattle 14; Denver wins; home-minus-away margin +3; combined total 31. This is corroborated public-provider final evidence, not a licensed league feed or sportsbook settlement.

Identity: 2026_06_SEA_DEN, SEA away, DEN home, ESPN event 401872995, scheduled kickoff 2026-10-16T00:15:00Z (October 15, 7:15 p.m. Central). ESPN explicitly returns STATUS_FINAL and completed=true; nflverse agrees on fixture, scores and event ID. No conflicting result was observed.

Current verification retrieval: 2026-10-16T10:26:05.269060+00:00. The exact final-whistle timestamp is unavailable and is not inferred from kickoff. Database result_observed_at is the actual ingestion observation time. Source publication/update timestamp remains unknown when the provider does not supply one.

Sources: https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard?dates=20261015 and https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv . Full source URLs, response hashes, HTTP statuses and per-response retrieval timestamps: live-result-verification.json and live-response-*.bin. Earlier independent read: provider-verification.json and response-*.bin. A 400 range response used the existing adapter's daily-date fallback. Zero NFL.com requests; no new provider, bypass or subscription.

The governed ingest_result path appended one final observation. No schedule or old result row was rewritten. execution.json records results_recorded=1 and identity/result refusals=0. Result observation IDs and actual observation times are retained in evaluated-api.json.
