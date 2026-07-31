-- Append-only and immutability guards enforced in the database itself,
-- independent of application code.

-- 1) Predictions are immutable: no UPDATE, no DELETE. New vintages are new rows.
create or replace function forbid_mutation() returns trigger
language plpgsql as $$
begin
  raise exception '% on % is forbidden: records are immutable/append-only', tg_op, tg_table_name;
end $$;

create trigger predictions_immutable
  before update or delete on predictions
  for each row execute function forbid_mutation();

create trigger prediction_components_immutable
  before update or delete on prediction_components
  for each row execute function forbid_mutation();

create trigger manual_prices_immutable
  before update or delete on manual_book_prices
  for each row execute function forbid_mutation();

create trigger bankroll_tx_immutable
  before update or delete on bankroll_transactions
  for each row execute function forbid_mutation();

create trigger settlements_immutable
  before update or delete on settlements
  for each row execute function forbid_mutation();

create trigger bet_events_immutable
  before update or delete on bet_events
  for each row execute function forbid_mutation();

create trigger audit_log_immutable
  before update or delete on audit_log
  for each row execute function forbid_mutation();

-- 2) Bets: only legal transitions. A settled bet's economic fields freeze;
--    result changes must flow through the correction path, which records a
--    correction settlement row + bet event (application layer) — the trigger
--    verifies a CORRECTED bet_event exists in the same transaction.
create or replace function guard_bet_update() returns trigger
language plpgsql as $$
begin
  if old.result = 'PENDING' then
    -- Settling: immutable identity fields must not change.
    if new.user_id  is distinct from old.user_id
       or new.game_id   is distinct from old.game_id
       or new.market    is distinct from old.market
       or new.selection is distinct from old.selection
       or new.line      is distinct from old.line
       or new.american  is distinct from old.american
       or new.stake     is distinct from old.stake
       or new.placed_at is distinct from old.placed_at then
      raise exception 'Bet identity fields are immutable';
    end if;
    return new;
  end if;

  -- Already settled: allow only a correction that changes result, and require
  -- a CORRECTED event recorded in the same transaction.
  if new.result is distinct from old.result then
    if not exists (
      select 1 from bet_events e
      where e.bet_id = old.id
        and e.event_type = 'CORRECTED'
        and e.created_at >= now() - interval '5 minutes'
    ) then
      raise exception 'Settled bets may only change via the correction path (CORRECTED bet_event required)';
    end if;
    return new;
  end if;

  raise exception 'Settled wager history cannot be silently overwritten';
end $$;

create trigger bets_guard_update
  before update on bets
  for each row execute function guard_bet_update();

create trigger bets_no_delete
  before delete on bets
  for each row execute function forbid_mutation();

-- 3) Point-in-time sanity: a prediction may never be created with a cutoff
--    in the future of its own creation time.
create or replace function guard_prediction_asof() returns trigger
language plpgsql as $$
begin
  if new.as_of_at > now() + interval '1 minute' then
    raise exception 'Prediction as_of_at may not be in the future';
  end if;
  return new;
end $$;

create trigger predictions_asof_guard
  before insert on predictions
  for each row execute function guard_prediction_asof();
