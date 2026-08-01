/**
 * Validates supabase/migrations and supabase/seed against a real Postgres
 * engine (pglite — an embeddable WASM build of Postgres), without requiring
 * Docker or a running Supabase project.
 *
 * This is not a substitute for `supabase db push` against a real project —
 * it stubs the `auth` schema (auth.users, auth.uid(), auth.role()) that
 * Supabase's GoTrue normally provides, and the `anon`/`authenticated`
 * database roles Supabase pre-creates, just enough for our RLS policies to
 * parse and actually be enforced. What it DOES catch, with a real Postgres
 * parser and planner: DDL syntax errors, constraint/type errors, trigger and
 * policy syntax errors; it exercises the append-only guard triggers end to
 * end; and — critically, since RLS does not apply to table owners by
 * default — it runs queries as a genuinely non-owner `authenticated` role to
 * confirm the policies actually isolate one user's data from another's, not
 * just that policies exist.
 *
 * Run: node supabase/test/validate-migrations.mjs
 */
import { PGlite } from '@electric-sql/pglite';
import { pgcrypto } from '@electric-sql/pglite/contrib/pgcrypto';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const migrationsDir = join(here, '..', 'migrations');
const seedPath = join(here, '..', 'seed', 'seed.sql');

const AUTH_STUB = `
create schema if not exists auth;
create table auth.users (
  id uuid primary key default gen_random_uuid(),
  email text
);
create or replace function auth.uid() returns uuid language sql stable as $$
  select nullif(current_setting('request.jwt.claim.sub', true), '')::uuid
$$;
create or replace function auth.role() returns text language sql stable as $$
  select coalesce(current_setting('request.jwt.claim.role', true), 'anon')
$$;
`;

const MIGRATIONS = ['0001_initial_schema.sql', '0002_row_level_security.sql', '0003_append_only_guards.sql'];

const db = new PGlite({ extensions: { pgcrypto } });
let allOk = true;

async function run(label, sql) {
  try {
    await db.exec(sql);
    console.log(`OK   ${label}`);
  } catch (err) {
    console.log(`FAIL ${label}`);
    console.log(`     ${err.message}`);
    allOk = false;
  }
}

await run('auth schema stub', AUTH_STUB);
for (const file of MIGRATIONS) {
  await run(file, readFileSync(join(migrationsDir, file), 'utf8'));
}
await run('seed.sql', readFileSync(seedPath, 'utf8'));

if (allOk) {
  const counts = await db.query(`
    select
      (select count(*)::int from information_schema.tables where table_schema='public') as tables,
      (select count(*)::int from information_schema.triggers where trigger_schema='public') as triggers,
      (select count(*)::int from pg_policies where schemaname='public') as policies,
      (select count(*)::int from pg_tables t join pg_class c on c.relname = t.tablename
         where t.schemaname='public' and c.relrowsecurity = true) as rls_enabled
  `);
  const c = counts.rows[0];
  console.log('---');
  console.log(`public tables: ${c.tables} | triggers: ${c.triggers} | RLS policies: ${c.policies} | RLS-enabled tables: ${c.rls_enabled}`);

  // Exercise the append-only guards end to end, not just "it parsed".
  await db.exec(`
    insert into auth.users (id, email) values ('11111111-1111-1111-1111-111111111111', 'demo@example.com');
    insert into users (id, email) values ('11111111-1111-1111-1111-111111111111', 'demo@example.com');
    insert into feature_sets (id, version) values ('fs1','v1');
    insert into teams (id,name,abbreviation,conference,division) values
      ('t1','Team One','TM1','AFC','EAST'), ('t2','Team Two','TM2','AFC','EAST');
    insert into stadiums (id,name,city,surface,roof,timezone) values
      ('s1','Stadium One','City One','GRASS','OUTDOOR','UTC');
    insert into games (id,season,week,kickoff_utc,away_team_id,home_team_id,stadium_id,roof_status) values
      ('g1',2026,1,'2026-09-13T17:00:00Z','t1','t2','s1','OUTDOOR');
    insert into model_versions (id,name,version,status,target,algorithm) values
      ('mv1','test','1.0','APPROVED_DEMO','margin','test');
    insert into predictions (id,game_id,model_version_id,vintage,as_of_at,home_win_probability,
      away_win_probability,expected_home_score,expected_away_score,expected_margin,expected_total,
      margin_interval_80,total_interval_80,margin_std,total_std,data_completeness_score)
    values ('p1','g1','mv1','OPENING', now() - interval '1 day', 0.55,0.45,24,21,3,45,
      '[-10,16]','[35,55]',13.5,10,0.9);
    insert into bankroll_accounts (id, user_id, mode, starting_balance, current_balance) values
      ('22222222-2222-2222-2222-222222222222','11111111-1111-1111-1111-111111111111','PAPER',10000,10000);
    insert into bets (id,user_id,bankroll_account_id,game_id,market,selection,american,stake,mode,placed_at,result)
    values ('33333333-3333-3333-3333-333333333333','11111111-1111-1111-1111-111111111111',
      '22222222-2222-2222-2222-222222222222','g1','SPREAD','HOME',-110,50,'PAPER',now(),'PENDING');
    update bets set result = 'WIN', settled_at = now()
      where id = '33333333-3333-3333-3333-333333333333';
  `);

  try {
    await db.exec(`update predictions set expected_margin = 99 where id = 'p1';`);
    console.log('FAIL immutability guard: UPDATE on predictions succeeded (must be blocked)');
    allOk = false;
  } catch (err) {
    console.log(`OK   immutability guard blocked UPDATE on predictions (${err.message.split('\n')[0]})`);
  }

  try {
    await db.exec(`update bets set result = 'LOSS' where id = '33333333-3333-3333-3333-333333333333';`);
    console.log('FAIL settlement guard: re-settling without a CORRECTED event succeeded (must be blocked)');
    allOk = false;
  } catch (err) {
    console.log(`OK   settlement guard blocked direct re-settlement (${err.message.split('\n')[0]})`);
  }

  // --- RLS enforcement: policies must actually isolate users, not just exist ---
  const USER_B = '44444444-4444-4444-4444-444444444444';
  await db.exec(`
    create role anon nologin;
    create role authenticated nologin;
    grant usage on schema public to anon, authenticated;
    grant select, insert, update, delete on all tables in schema public to anon, authenticated;
    insert into auth.users (id, email) values ('${USER_B}', 'userb@example.com');
    insert into users (id, email) values ('${USER_B}', 'userb@example.com');
    insert into manual_book_prices (id, user_id, game_id, sportsbook, market, selection, american, price_observed_at, entered_at, confirmed_visible)
      values (gen_random_uuid(), '11111111-1111-1111-1111-111111111111', 'g1', 'bet365 (manual entry)', 'SPREAD', 'HOME', -110, now(), now(), true);
  `);

  async function checkRls(label, expectOk, fn) {
    try {
      const ok = await fn();
      if (ok === expectOk) console.log(`OK   ${label}`);
      else { console.log(`FAIL ${label}: got ${ok}, expected ${expectOk}`); allOk = false; }
    } catch (err) {
      if (!expectOk) console.log(`OK   ${label} (${err.message.split('\n')[0]})`);
      else { console.log(`FAIL ${label}: threw ${err.message.split('\n')[0]}`); allOk = false; }
    }
  }

  await db.exec(`
    set role authenticated;
    select set_config('request.jwt.claim.sub', '${USER_B}', false);
    select set_config('request.jwt.claim.role', 'authenticated', false);
  `);
  await checkRls('RLS: a different authenticated user sees ZERO of another user\'s manual_book_prices rows', true, async () => {
    const r = await db.query(`select count(*)::int as n from manual_book_prices`);
    return r.rows[0].n === 0;
  });
  await checkRls('RLS: a different authenticated user sees ZERO of another user\'s bankroll_accounts rows', true, async () => {
    const r = await db.query(`select count(*)::int as n from bankroll_accounts`);
    return r.rows[0].n === 0;
  });
  await checkRls('RLS: a user cannot INSERT a manual price claiming to be a different user', false, async () => {
    await db.query(
      `insert into manual_book_prices (id, user_id, game_id, sportsbook, market, selection, american, price_observed_at, entered_at, confirmed_visible) values (gen_random_uuid(),'11111111-1111-1111-1111-111111111111','g1','bet365 (manual entry)','SPREAD','AWAY',-120, now(), now(), true)`,
    );
    return true;
  });
  await checkRls('RLS: shared reference data (teams) remains readable across users', true, async () => {
    const r = await db.query(`select count(*)::int as n from teams`);
    return r.rows[0].n === 2;
  });

  await db.exec(`
    set role anon;
    select set_config('request.jwt.claim.sub', '', false);
    select set_config('request.jwt.claim.role', 'anon', false);
  `);
  await checkRls('RLS: an unauthenticated session sees ZERO user-owned manual_book_prices rows', true, async () => {
    const r = await db.query(`select count(*)::int as n from manual_book_prices`);
    return r.rows[0].n === 0;
  });

  await db.exec(`reset role;`);
}

console.log('---');
console.log(allOk ? 'ALL CHECKS PASSED' : 'SOME CHECKS FAILED');
await db.close();
process.exitCode = allOk ? 0 : 1;
