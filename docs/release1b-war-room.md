# Release 1B — evidence-first War Room foundation

Branch starts at integrated `fa1d96a0ebcc0f9fce0439023a1f20fe0d7ba5c2`. No deployment, installation, protected database writes or paper-ledger release.

## Working vertical slice

The real front door is `/war-room`. An authenticated `/v1/research/briefing` read prepares a complete seven-day local slate without the global historical/candidate row limits. It excludes future observations and fixture/demo prices, retains original cohort/model/policy identifiers, and distinguishes missing evidence from zero edge. It never generates evaluations or modifies research on GET.

The home screen shows today's captured game count, games with published market observations, a source-backed briefing, NO BET, separate unavailable single/SGP states, game links into real Game Lab, missing-input explanations, a price-target refusal and recorded material changes. No synthetic recommendations or probabilities populate empty states. API and schema failures hide cached findings rather than silently presenting stale findings as current. Full diagnostics are not the home screen.

Real Game Lab shares the validated briefing query and shows the selected game's prepared forecast/source timestamps and expandable missing-input reasons. A failed preparation request hides its cached evidence; a game outside the briefing window is explicitly unavailable. Refresh analysis refreshes that evidence alongside prices and candidates. This connects the summary to the evidence view without substituting demo inputs or implying a complete analytical model.

`scripts/run_research.py` is a bounded serial free-source worker: capture → governed persistence → point-in-time feature-availability manifest → recorded briefing. Every attempt creates a distinct existing Job record, keeping failures visible and completed manifests available for comparisons. Maximum 96 cycles and minimum five-minute spacing limit polling. It does not activate official evaluations, train/promote models, schedule itself in Windows or send notifications. Run history is operational metadata; it is not a new official forward cohort.

An ESPN source correction appends a new schedule observation for changed kickoff/venue/fixture state, not only week changes. Identical recapture does not freshen quotes or schedules. Unknown source-update timestamps remain unknown; original rows are preserved.

## Local isolated acceptance

From `apps/api`, initialize a NEW isolated SQLite workspace. The initializer refuses every existing file, migrates only the new file in a fresh process, and records a database UUID plus filesystem-identity attestation. Do not point this branch at the original newer populated database. Run:

```
uv run python scripts/init_research_workspace.py --database-file data/research_1b_identity_acceptance.db
uv run python scripts/run_research.py --database-file data/research_1b_identity_acceptance.db
```

For bounded periodic collection, supply `--cycles 8 --interval 900`. The worker requires matching workspace UUID and file identity, rejects copies/replacements and protected-file aliases, and refuses databases with historical research/evaluation rows. A filename is never sufficient authorization. Previously created unattested scratch databases cannot be adopted; create a new workspace instead. No installation or background service is implicit. Serve the same isolated database with the existing loopback FastAPI/Vite setup and open `/war-room`. UI refresh rereads evidence; the worker performs source ingestion independently. A failed capture remains the latest attempt even when an older successful manifest exists.

An exclusive sibling lock prevents overlapping worker instances. Normal exit removes the owned lock. After a hard process kill, confirm that no worker remains before manually removing its lock; recovery does not silently override it. Each cycle queries a bounded UTC window; the initial range plus daily fallback can make up to 12 public requests, with existing request timeouts. NWS collection adds at most 12 calls for six verified outdoor US venues, using ten-second request timeouts and stopping further weather calls on the first failure. Forecasts outside the kickoff period or with future issuance are refused. Dome, retractable/unknown roof and international venues remain explicitly unattempted; there is no weather-based probability adjustment. Scheduled Windows services, event-triggered injury collection and notifications are not enabled by this source change.

## Deliberately incomplete analytical capabilities

Release 1B prepares identities, observation provenance and availability manifests. It does not fabricate EPA/player usage when point-in-time inputs are absent. The existing trained feature builders, availability services and model registry are reusable; automatic current-season team/player/injury ingestion is not yet integrated by this worker. That work requires defensible source mappings and independent acceptance. NWS is integrated as bounded forecast observation collection, with source outages and unsupported venues retained as explicit gaps. No injury-driven projection, price-based WATCH target, validated probability, exact TN price, executable SGP or performance improvement is claimed.

The current quote schema has no established state-specific offer/combinability proof, so this version's verified-price gate is explicitly closed. Even a row named `bet365` cannot enable it. Historical candidates remain research evidence, separate by cohort, and cannot become a verified current recommendation. A completed source capture does not mean analytical completeness. Provider findings and market coverage: `release1b-provider-feasibility.md`.

Foundation freshness diagnostics use source issuance, not capture time: availability requires verified official/licensed observations issued within 48 hours; weather requires a known issuance within six hours. Unknown, stale or future issuance remains a missing-input reason. These diagnostics do not amend frozen policy gates, imply a complete injury report, confirm any player is active, or authorize a forecast/model adjustment. Equal-time research assessments are retained together for review.

## Product and next milestones

Reusable: canonical captures, immutable observations, feature builders, model registry, forward policy/cohort gates, read-only real Game Lab, health and CI. Missing: validated current-season team/player/availability inputs, qualified market-specific models, authorized TN price feed and exact combined SGP pricing. Demo portfolio is still separate and not a real paper ledger.

Continue 1B with verified source ingestion/identity and feature-vintage acceptance before 1C probability/value qualification. Gate 1D on joint-model validation plus exact combinability/price evidence; keep an unpriced SGP a research capability only. UI refinements cannot substitute for those gates. Do not implement the complete vision in one unvalidated release.
