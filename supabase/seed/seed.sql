-- Demonstration seed data for local Supabase development.
-- Everything here is DEMONSTRATION DATA — NOT FOR REAL-MONEY DECISIONS.
--
-- The browser demo (Local Demo Mode) generates its full dataset
-- deterministically in @fde/api-client. This SQL seed provides the minimal
-- shared reference rows a fresh Supabase project needs for the same slate:
-- providers, model versions, and feature-set registration. Full game/odds
-- seeding is performed by the future ingestion service; see docs/architecture.

insert into providers (id, name, kind, is_critical) values
  ('demo-schedule', 'Demo schedule feed', 'schedule', true),
  ('demo-roster', 'Demo roster feed', 'roster', true),
  ('demo-injury', 'Demo injury feed', 'injury', true),
  ('demo-weather', 'Demo weather feed', 'weather', true),
  ('demo-odds', 'Demo consensus odds (mock)', 'odds', true),
  ('demo-officials', 'Demo officials feed', 'officials', false),
  ('manual-entry', 'Manual bet365 price entry (user-typed)', 'odds', false)
on conflict (id) do nothing;

insert into feature_sets (id, version, description) values
  ('fs-2026.09.1', '2026.09.1', 'Demo feature set: ratings, efficiency, availability, weather, market priors')
on conflict (id) do nothing;

insert into model_versions
  (id, name, version, status, target, algorithm, training_period, validation_period,
   feature_set_version, calibration_version, artifact_hash, git_commit, approved_at,
   known_limitations, drift_status, is_placeholder, performance_summary)
values
  ('mv_ensemble', 'fde-ensemble', '0.3.0-demo', 'APPROVED_DEMO',
   'Win prob / margin / total distributions', 'Weighted component ensemble (deterministic)',
   '2019-2024 demo backtest window', '2025 demo holdout', 'fs-2026.09.1', 'cal-0.6.0',
   'demo-hash-ensemble', 'demo-git-commit-placeholder', '2026-09-01T12:00:00Z',
   '["Demo-approved for demonstration workflows only","Thresholds are not historically validated","Not suitable for real-money decisions"]',
   'STABLE', false, 'Demo backtest metrics only — not validated for live use'),
  ('mv_market', 'market-baseline', '1.2.0', 'APPROVED_DEMO', 'Market-implied probabilities',
   'No-vig consensus prior', '-', '-', 'fs-2026.09.1', 'cal-0.6.0', 'demo-hash-market',
   'demo-git-commit-placeholder', '2026-09-01T12:00:00Z', '[]', 'STABLE', false, 'Baseline'),
  ('mv_bayes', 'bayesian-hierarchical', '0.1.0', 'PLACEHOLDER', 'Margin distribution',
   'Bayesian hierarchical (planned: PyMC)', '-', '-', 'fs-2026.09.1', 'cal-0.6.0',
   'demo-hash-bayes', 'demo-git-commit-placeholder', null,
   '["Placeholder — illustrative output only","Not trained on real data"]', 'UNKNOWN', true, 'No validated performance'),
  ('mv_gbm', 'gradient-boosting', '0.1.0', 'PLACEHOLDER', 'Win probability',
   'Gradient boosting (planned: LightGBM)', '-', '-', 'fs-2026.09.1', 'cal-0.6.0',
   'demo-hash-gbm', 'demo-git-commit-placeholder', null,
   '["Placeholder — illustrative output only"]', 'UNKNOWN', true, 'No validated performance'),
  ('mv_mc', 'monte-carlo-simulator', '0.1.0', 'PLACEHOLDER', 'Score distributions',
   'Drive-level Monte Carlo (planned: Python service)', '-', '-', 'fs-2026.09.1', 'cal-0.6.0',
   'demo-hash-mc', 'demo-git-commit-placeholder', null,
   '["Placeholder — illustrative output only"]', 'UNKNOWN', true, 'No validated performance')
on conflict (id) do nothing;
