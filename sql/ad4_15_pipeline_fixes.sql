-- ===========================================================================
-- ad4_15_pipeline_fixes.sql - unblock the failing GitHub Actions.
--
-- Four scheduled Actions had been failing for days. Three were bugs in the
-- Python (fixed in scripts/); this file is the one that needs a database
-- change.
--
--   Signal Engine:  insert or update on table "signals" violates foreign key
--                   constraint "signals_strategy_id_fkey"
--                   Key (strategy_id)=(system) is not present in "strategies".
--
-- scripts/signals.py raises system-wide alerts - a job failed, a job went
-- stale, an anomaly fired - that are deliberately NOT tied to a trading
-- strategy, and stamps them strategy_id = 'system'. There is no such row in
-- strategies, so the foreign key rejects the whole batch and the run dies
-- before any signal is written, including the real trading ones.
--
-- The fix is to make 'system' a real row rather than to weaken the key. It
-- IS the source of those signals, so naming it is honest; and a row works
-- whether or not signals.strategy_id happens to be nullable, which a NULL
-- would not.
--
-- It is seeded disabled, with side NONE and a zero capital cap, so nothing
-- can ever trade it - and origin = 'system' marks it as machinery rather
-- than one of Hassan's six strategies.
--
-- Run order: any time after sql/ad4_strategies_seed.sql. Re-runnable.
-- ===========================================================================

insert into strategies (
  strategy_id, name, side, origin, conflict_class, enabled,
  universe, regime_filter, capital_cap_pct, max_concurrent, config, extra
)
values (
  'system',
  'System alerts (not a trading strategy)',
  'NONE',
  'system',
  'none',
  false,
  '[]'::jsonb,
  '[]'::jsonb,
  0,
  0,
  '{}'::jsonb,
  '{"note": "Owner of the system-wide signals scripts/signals.py raises - job_failed, job_stale, anomaly. Not tradeable: enabled=false, capital_cap_pct=0, max_concurrent=0. Exists so signals.strategy_id has something to point at.", "origin": "ad4_15_pipeline_fixes.sql"}'::jsonb
)
on conflict (strategy_id) do update
  set name            = excluded.name,
      side            = excluded.side,
      origin          = excluded.origin,
      enabled         = false,          -- never let this one be turned on
      capital_cap_pct = 0,
      max_concurrent  = 0,
      extra           = excluded.extra;


-- --------------------------------------------------------------------------
-- Confirm it landed, and that it cannot trade.
-- --------------------------------------------------------------------------
do $ad4$
declare
  v_total int;
  v_trade int;
  v_on    int;
begin
  select count(*) into v_total from strategies;
  select count(*) into v_trade from strategies where coalesce(origin, '') <> 'system';
  select count(*) into v_on    from strategies where enabled = true;

  raise notice 'strategies: % total (% tradeable, % system), % enabled',
               v_total, v_trade, v_total - v_trade, v_on;

  if not exists (select 1 from strategies where strategy_id = 'system') then
    raise warning 'the system strategy did not land - the Signal Engine will keep failing';
  elsif v_on > 0 then
    raise warning '% strategy(ies) are ENABLED. Deliberate?', v_on;
  else
    raise notice 'OK - the Signal Engine can now write its system alerts';
  end if;
end
$ad4$;
