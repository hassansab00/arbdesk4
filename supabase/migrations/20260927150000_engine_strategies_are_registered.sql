-- Plan v2 P5.12 part 3a: the strategies that decide through the one engine
-- (P8.1, Hassan 27 Sep) are registered, so the hourly tick can record their
-- decisions (decisions.strategy_id references strategies).
--
-- enabled = false: each enters the 'research' state (strategy_gets_a_state),
-- so neither signal_engine nor paper_plans runs it, and nothing it decides can
-- reach a ledger. Part 3b wires its orders and moves it to shadow through
-- set_strategy_state, with its reason.
--
-- Inserting a strategy also opens its shadow ledger (strategy_opens_its_ledger,
-- settings shadow_ledger.notional_usd) - that is the platform's rule, and a
-- ledger nothing trades holds its notional and nothing else.
--
-- s2_combination_arb is the engine's S2 view too, but that id is the old
-- signal path's live shadow strategy; the tick does not decide for it until
-- the old path is retired, so its record is never written by two engines.
--
-- Idempotent: on conflict do nothing.

insert into public.strategies (strategy_id, name, side, enabled, extra)
values
  ('s10_winner', 'S10 max-temp winner: the most probable bucket', 'YES', false,
   '{"origin":"engine","family":"s10","plan":"P8.1"}'::jsonb),
  ('s10_growth', 'S10 max-temp winner: the best-growth tradeable bucket', 'YES', false,
   '{"origin":"engine","family":"s10","plan":"P8.1"}'::jsonb),
  ('s10_lock', 'S10 max-temp winner: the ladder under a no-loss lock', 'YES', false,
   '{"origin":"engine","family":"s10","plan":"P8.1"}'::jsonb),
  ('s11_ladder', 'S11 ladder optimiser: the growth-optimal YES set', 'YES', false,
   '{"origin":"engine","family":"s11","plan":"P8.1"}'::jsonb),
  ('s11_lock', 'S11 ladder optimiser under a no-loss lock', 'YES', false,
   '{"origin":"engine","family":"s11","plan":"P8.1"}'::jsonb),
  ('s12_no', 'S12 overpriced bucket: NO', 'NO', false,
   '{"origin":"engine","family":"s12","plan":"P8.1"}'::jsonb)
on conflict (strategy_id) do nothing;

-- S10 decides SWITCH (sell the held bucket, buy the new target: P7.5), and the
-- decision log records what each strategy decided, so SWITCH joins the actions
-- (the plan's P5.12 part 3). Re-runnable: dropped if present, then added.
alter table public.decisions drop constraint if exists decisions_action_check;
alter table public.decisions add constraint decisions_action_check
  check (action = any (array['BUY', 'SELL', 'SWITCH', 'HOLD', 'WAIT', 'NONE']));
