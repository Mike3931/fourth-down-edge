-- Close an anonymous-read hole on global recommendations.
--
-- 0002 created:
--
--   create policy recommendations_select_own on recommendations
--     for select using (user_id is null or auth.uid() = user_id);
--
-- `recommendations.user_id` is nullable, so a recommendation not owned by
-- any particular user (one produced by the engine for the whole slate)
-- has user_id IS NULL. That branch of the predicate does not reference
-- auth at all, so it is TRUE for an anonymous session too: anyone holding
-- the public anon key could read every global recommendation.
--
-- That is the product's core output — status, edge, EV, stake breakdown,
-- target price, invalidation price. Every shared research table it is
-- derived from (games, predictions, odds_snapshots, model_versions, ...)
-- already requires auth.role() = 'authenticated'. The conclusions drawn
-- from that data were more exposed than the data itself.
--
-- No rows are affected: recommendations are currently computed live and
-- never persisted (see docs/limitations.md), so the table is empty. This
-- closes the hole before the analytical engine starts writing to it.

drop policy if exists recommendations_select_own on recommendations;

create policy recommendations_select_authenticated on recommendations
  for select using (
    auth.role() = 'authenticated'
    and (user_id is null or auth.uid() = user_id)
  );

-- INSERT is unchanged and was never exposed: `with check (auth.uid() =
-- user_id)` evaluates to NULL rather than TRUE for an anonymous session,
-- so the write was already denied. Only the read needed fixing.
