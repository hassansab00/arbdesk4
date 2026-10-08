-- ===========================================================================
-- THE MODEL-ONLY TWINS TRADE ON PAPER (Hassan, 8 Oct: "run the shadow, and
-- the w = 1 thing ... the paper trade section has been empty").
--
-- Every engine strategy prices from p = p_market + w (p_model - p_market),
-- w learned per view and checkpoint class and 0 until the model has earned
-- it (market_anchor, Hassan 27 Sep). At w = 0 every view is the market's own
-- price, so no ask has an edge: from 27 Sep 16:36Z to 8 Oct 06:36Z the six
-- engine strategies decided 16,182 times and bought once (decisions, live and
-- data/archive/decisions). The 04:54Z learning run on 8 Oct kept all nine
-- weights at 0: 11-14 settled days per scope, 40 needed.
--
-- Each of the six gets a twin that decides by exactly its rules on the
-- model's own ladder - the anchor at w = 1, recorded on every decision as
-- {"w": 1.0, "version": "model-only:w1"} (scripts/strategies/engine_views.py,
-- MODEL_ONLY). The twins trade their own shadow ledgers only: $1,000 of paper
-- each, opened by strategy_opens_its_ledger when the row goes in. Nothing
-- reaches the portfolio account (Rule 6). The Candidate gate, the risk rails
-- and the engine's sizing apply to them as to their bases.
--
-- 1. The six rows, registered switched off (state research, ledger opened).
-- 2. Their ledgers take no automatic exits, as their bases' do not
--    (20260927210000): the engine exits only by its own rules.
-- 3. Into shadow - switched on - with the reason on the record. Only from the
--    state registration gave them, so a re-run never switches back on a twin
--    someone has since switched off.
--
-- Run it after the code that knows the twins is on main: signal_engine skips
-- engine_shadow.STRATEGIES, and an enabled id outside it would be read as an
-- old signal-path strategy. Re-runnable.
-- ===========================================================================

insert into public.strategies (strategy_id, name, side, enabled, extra)
values
  ('s10_winner_model', 'S10 max-temp winner, model only (w = 1): the most probable bucket', 'YES', false,
   '{"origin":"engine","family":"s10","base":"s10_winner","anchor":"model-only:w1"}'::jsonb),
  ('s10_growth_model', 'S10 max-temp winner, model only (w = 1): the best-growth tradeable bucket', 'YES', false,
   '{"origin":"engine","family":"s10","base":"s10_growth","anchor":"model-only:w1"}'::jsonb),
  ('s10_lock_model', 'S10 max-temp winner, model only (w = 1): the ladder under a no-loss lock', 'YES', false,
   '{"origin":"engine","family":"s10","base":"s10_lock","anchor":"model-only:w1"}'::jsonb),
  ('s11_ladder_model', 'S11 ladder optimiser, model only (w = 1): the growth-optimal YES set', 'YES', false,
   '{"origin":"engine","family":"s11","base":"s11_ladder","anchor":"model-only:w1"}'::jsonb),
  ('s11_lock_model', 'S11 ladder optimiser, model only (w = 1), under a no-loss lock', 'YES', false,
   '{"origin":"engine","family":"s11","base":"s11_lock","anchor":"model-only:w1"}'::jsonb),
  ('s12_no_model', 'S12 overpriced bucket, model only (w = 1): NO', 'NO', false,
   '{"origin":"engine","family":"s12","base":"s12_no","anchor":"model-only:w1"}'::jsonb)
on conflict (strategy_id) do nothing;

with changed as (
  update public.paper_accounts
     set policy = policy || '{"auto_exit_enabled": false}'::jsonb, policy_version = policy_version + 1
   where kind = 'shadow'
     and strategy_id in ('s10_winner_model', 's10_growth_model', 's10_lock_model',
                         's11_ladder_model', 's11_lock_model', 's12_no_model')
     and coalesce((policy->>'auto_exit_enabled')::boolean, false)
  returning account_id)
insert into public.paper_activity(account_id, event_type, payload)
select account_id, 'policy_changed',
       jsonb_build_object('auto_exit_enabled', false,
                          'why', 'model-only twin (8 Oct): the engine exits only by its own rules, as its base does')
  from changed;

do $$
declare r record;
begin
  for r in select strategy_id from public.strategy_state
            where strategy_id in ('s10_winner_model', 's10_growth_model', 's10_lock_model',
                                  's11_ladder_model', 's11_lock_model', 's12_no_model')
              and state = 'research' and reason = 'registered'
            order by strategy_id loop
    perform public.set_strategy_state(r.strategy_id, 'shadow',
      'Hassan, 8 Oct: run the model-only (w = 1) shadow - its own paper ledger, no capital (Rule 6)');
  end loop;
end $$;
