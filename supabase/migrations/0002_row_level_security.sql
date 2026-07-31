-- Row Level Security: users own their bankrolls, wagers, settings, manual
-- prices, recommendations, exposure snapshots, and audit entries. Shared
-- reference/research data is readable by any authenticated user and writable
-- only by the service role (ingestion pipelines).

-- Helper predicate used below: (auth.uid() = user_id)

-- User-owned tables ---------------------------------------------------------

alter table users enable row level security;
create policy users_select_own on users for select using (auth.uid() = id);
create policy users_update_own on users for update using (auth.uid() = id);
create policy users_insert_self on users for insert with check (auth.uid() = id);

alter table user_settings enable row level security;
create policy user_settings_all_own on user_settings
  for all using (auth.uid() = user_id) with check (auth.uid() = user_id);

alter table manual_book_prices enable row level security;
create policy manual_prices_select_own on manual_book_prices for select using (auth.uid() = user_id);
create policy manual_prices_insert_own on manual_book_prices
  for insert with check (auth.uid() = user_id and confirmed_visible = true);
-- No UPDATE or DELETE policies: manual prices are immutable and append-only.

alter table bankroll_accounts enable row level security;
create policy bankroll_accounts_select_own on bankroll_accounts for select using (auth.uid() = user_id);
create policy bankroll_accounts_insert_own on bankroll_accounts for insert with check (auth.uid() = user_id);
create policy bankroll_accounts_update_own on bankroll_accounts for update using (auth.uid() = user_id);

alter table bankroll_transactions enable row level security;
create policy bankroll_tx_select_own on bankroll_transactions for select using (auth.uid() = user_id);
create policy bankroll_tx_insert_own on bankroll_transactions for insert with check (auth.uid() = user_id);
-- No UPDATE/DELETE: transactions are append-only.

alter table bets enable row level security;
create policy bets_select_own on bets for select using (auth.uid() = user_id);
create policy bets_insert_own on bets for insert with check (auth.uid() = user_id);
create policy bets_update_own on bets for update using (auth.uid() = user_id);
-- Updates are additionally constrained by the append-only trigger in 0003:
-- only PENDING -> settled transitions and correction paths are allowed.

alter table bet_events enable row level security;
create policy bet_events_select_own on bet_events for select
  using (exists (select 1 from bets b where b.id = bet_id and b.user_id = auth.uid()));
create policy bet_events_insert_own on bet_events for insert
  with check (exists (select 1 from bets b where b.id = bet_id and b.user_id = auth.uid()));

alter table settlements enable row level security;
create policy settlements_select_own on settlements for select
  using (exists (select 1 from bets b where b.id = bet_id and b.user_id = auth.uid()));
create policy settlements_insert_own on settlements for insert
  with check (exists (select 1 from bets b where b.id = bet_id and b.user_id = auth.uid()));

alter table exposure_snapshots enable row level security;
create policy exposure_select_own on exposure_snapshots for select using (auth.uid() = user_id);
create policy exposure_insert_own on exposure_snapshots for insert with check (auth.uid() = user_id);

alter table recommendations enable row level security;
create policy recommendations_select_own on recommendations
  for select using (user_id is null or auth.uid() = user_id);
create policy recommendations_insert_own on recommendations
  for insert with check (auth.uid() = user_id);

alter table audit_log enable row level security;
create policy audit_select_own on audit_log for select using (auth.uid() = user_id);
create policy audit_insert_own on audit_log for insert with check (auth.uid() = user_id);
-- No UPDATE/DELETE: audit log is append-only for all non-service roles.

-- Shared research data: authenticated read, service-role write --------------

do $$
declare t text;
begin
  foreach t in array array[
    'providers','ingestion_runs','raw_objects','source_records','entity_mappings',
    'teams','players','stadiums','officials','games','game_officials',
    'roster_snapshots','depth_chart_snapshots','participation','injury_reports',
    'player_availability_snapshots','weather_snapshots','odds_snapshots',
    'feature_sets','game_feature_snapshots','model_versions','calibration_models',
    'predictions','prediction_components','model_evaluations','data_quality_events'
  ]
  loop
    execute format('alter table %I enable row level security', t);
    execute format(
      'create policy %I on %I for select using (auth.role() = ''authenticated'')',
      t || '_read_authenticated', t
    );
    -- No insert/update/delete policies for client roles: only the service
    -- role (which bypasses RLS) may write shared research data.
  end loop;
end $$;
