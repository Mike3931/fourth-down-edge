# Release 1A corrective pass

Scope: PR #2 on top of PR #1. No master merge, deployment, installed application modification or paper-ledger work is authorized in this pass.

## Completeness

The slate now selects upcoming games first (nearest kickoff first), then history (newest kickoff first). Canonical game ID breaks kickoff ties. The newest schedule observation is selected by observation time and row ID. `offset`, `total_count` and `has_more` expose pagination; bounds reject invalid limits. Historical rows remain available through pagination and game-specific lookup. Game Lab requests its canonical game directly so a deep link does not depend on the first global page.

Candidates accept a canonical-game filter before pagination, with separate browser cache keys per game. The response identifies its scope and reports `has_more`. Game Lab only reports absence when that scope matches and completeness is explicitly confirmed; partial or older responses receive a warning. Existing time horizon, mode, health gate and recorded-evaluation requirements remain enforced.

Challenge findings: newest assessments are resolved separately per study cohort. A newer PASS or DATA_INCOMPLETE assessment supersedes an older active assessment in the read view. This does not change any ledger entry, settle conflicts, or combine cohorts. Deterministic time/row-ID ordering remains a display rule, not an adjudication of conflicting evaluations.

## Price provenance and governance

ESPN reads published close/summary fields. The collector's `observed_at` is the first application capture instant for an unchanged price, not when the sportsbook established it. Repeated unchanged captures neither append artificial movement nor refresh that timestamp. No provider timestamp is fabricated. LIVE continues to identify real public-source origin; it does not certify freshness, in-play status or executable availability.

The read API exposes application capture time, any separately stored provider timestamp, an unknown sportsbook establishment time, source classification and `currently_available_offer=false`. The Game Lab labels capture times and states that sportsbook timing and availability are unverified. Older records retain their original values, IDs, hashes and provenance.

New consensus construction excludes ESPN observations without verified provider price timing even when captured recently and even in the one-book burn-in cohort. A separate rejection counter explains this. `consensus-v3` separates this calculation from immutable v2 snapshots. This is a stricter evidence gate, not a model promotion or a revision of frozen studies. Existing recorded research assessments remain visible with their original policy/model/cohort and unapproved status; they are not certified as current offers.

## Regression coverage and acceptance

Regression fixtures contain 405 historical games plus an upcoming game, and 205 competing games that exclude the target candidate from the global 200-row page. Additional coverage verifies pagination, genuine scoped absence, separate cohorts, later PASS, read-only counts, ESPN timestamp honesty, repeat capture integrity, consensus refusal and browser handling of incomplete scope.

Before CI: Ruff and mypy passed, focused backend/governance tests passed (119), production build and 239 frontend unit tests passed, schema/RLS/immutability checks passed, client secret audit passed, and the browser suite passed (25). Final-source CI and fresh real-public-source acceptance must be read back before merge readiness is asserted. Updated PR evidence records the exact tested commit and workflow links.

The first complete CI challenge detected nine failures in an older lightweight exclusion fixture lacking provider metadata. Its quote contract was corrected without weakening the production rule; an additional regression verifies the new exclusion counter partitions correctly. All PostgreSQL gates passed in that run. The entire suite is rerun on the corrected source rather than counting the first run as acceptance. Game Lab's absence message explicitly names the endpoint's pregame window so it cannot imply absence across all history and future horizons.

Expected exclusions to verify from final CI: network-marked tests are intentionally excluded from deterministic CI; PostgreSQL markers run as separate mandatory gates. Artifact-dependent skips must be enumerated from pytest's short summary. The actual public-capture browser journey is opt-in and skipped by default in CI; it is run locally against an isolated migrated database. Deployment is skipped by PR policy and explicitly prohibited here. Production dependency audit currently reports two moderate router findings; the existing Python audit exceptions remain documented in security.md.

## Preservation

All validation uses disposable databases. The original populated database is read-only and must match its prior SHA256 `133ff5b1a59229280ec19e84c59c314f200b377623b3bfa26de0fa583e2e5d0a` after acceptance. The three conflicting evaluations remain unresolved and preserved. No migration against that database is attempted and no owner decision is inferred.
