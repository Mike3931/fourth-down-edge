# Release 1B canonical identity corrective pass — 2026-10-13

Target: PR #3, source integration only. Original database, installed app, master, frozen studies and the three unresolved historical evaluations are not modified.

## Resolution rules

Both ESPN and nflverse forward schedule ingestion use the same resolver. A durable binding is keyed by data mode, provider and provider game ID; it retains the canonical identity permanently across date/week changes. New regular-season ESPN proposals use nflverse's season/week/away/home convention. Pre/postseason proposals use a stable ESPN event key, never kickoff date. These proposals are not accepted over an existing identity.

An unbound provider can join one existing identity only with corroborating season/type, canonical participants, and exact kickoff (plus same week for regular season). Multiple observations are grouped by canonical identity, not counted as multiple candidate games. A unique legacy provider-key history can establish a binding while retaining its original canonical ID. ESPN-first legacy IDs are retained when nflverse arrives; nflverse's established source ID becomes an alias. No historical canonical ID or child reference is rewritten.

A bound provider's schedule changes append revisions under the same ID. An unchanged source repoll does not overwrite another provider's newer revision. New source aliases append their own provider observation, preserving provenance. Unbound disagreements about kickoff/week/home-away, conflicting participant/season histories, competing legacy IDs and ambiguous matches are refused and recorded in a repeat-safe identity review table. These cases need review; they are not silently merged. `/v1/research/identity-reviews` exposes evidence, and the briefing identifies unresolved identity flags. The worker refuses to claim complete capture when identity observations are refused.

A database primary key protects each provider binding. A shared fixture claim also prevents concurrent first arrivals from publishing different identities. On an insert race, the losing transaction rolls back rather than forcing an identity; retry can resolve against the committed evidence. PostgreSQL contention is tested in the mandatory identity gate. Deliberately conservative matching can withhold an unbound provider that first appears after a reschedule; without stable-key corroboration it remains a review case.

Migration `e92a61bf4037` creates empty crosswalk/review/workspace tables only. No backfill, deduplication, alias resolution, canonical renumbering, evaluation resolution or child-record update occurs. Previously duplicated identities remain distinct and flagged. A populated newer original database is not downgraded or migrated.

## Worker protection

`init_research_workspace.py` creates only a nonexistent file using exclusive creation, migrates that explicit new target in a separate process, and records a database UUID plus filesystem device/file identity. It cannot adopt a populated file. The attestation is local, ignored by Git, and not a provider credential.

The worker requires the matching UUID and filesystem identity; a renamed/copied file plus copied sidecar cannot pass. Known protected databases and hard-linked/symlinked aliases are rejected by file identity. Unregistered profiles and any attested database containing historical forward evaluations, backtests or model evaluations are refused. Additional protected paths can be supplied through `FDE_PROTECTED_DATABASES`. A serial process lock prevents overlapping workers; authorization is rechecked each cycle. This protects against accidental profile selection/copying, not a hostile local owner deliberately forging database contents and attestations.

Acceptance exposed cached configuration initialized by imports before CLI environment assignment. The worker now explicitly selects its authorized database engine and all artifact directories; cached settings cannot redirect writes to a different profile. An adversarial regression verifies the other profile remains byte-identical. Failed initialization is not automatically cleaned, reset or adopted.

## Regression and acceptance

Tests cover both provider arrival orders; date changes and misleading new proposed IDs; multiple observations and repeated ingestion; ambiguity, kickoff and participant disagreements; preserved odds/forecast/final linkage; PostgreSQL first-arrival contention and retry; copied/renamed/linked/unregistered databases; attestation mismatch; historical research rows; and cached-profile redirection. Existing season-type assertions now check stable event/week IDs instead of dates.

All required CI and actual-engine acceptance must pass on the final corrective head. The complete results, CI links, immutable original database hash and final SHA are recorded in the PR description and the local corrective acceptance report. Prior foundation acceptance does not substitute for this corrective run. Existing optional schedule-artifact/public-capture skips and dependency exceptions remain explicit; no analytical accuracy or deployment readiness is inferred from passing tests.
