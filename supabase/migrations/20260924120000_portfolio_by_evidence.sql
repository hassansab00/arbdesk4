-- THE PORTFOLIO ACCOUNT, ACTIVATED BY EVIDENCE (plan v2, step P5.10).
--
-- DECIDED 24 Sep: Hassan delegated the P5.10 decision to Claude ("do what's
-- the most optimal option"). The decision, recorded in settings
-- 'portfolio_gate':
--
--   bankroll            $10,000 paper (the plan's suggestion)
--   promotion gate      a strategy's OWN shadow ledger shows at least 30
--                       settled dates, at least 60 settled decisions, a
--                       positive lower 80% bound on log-growth per dollar, and
--                       no single city-day making more than 25% of the gain
--   activation          when the first strategy is promoted, never by date
--   allocation          Thompson sampling on each strategy's posterior,
--                       at most 40% of the bankroll per strategy, floor 0
--
-- scripts/meta_allocator.py runs the gate and the allocation nightly and
-- calls the two functions below. The promotion itself goes through
-- promote_strategy_to_portfolio() (P5.2), which records the approver.
--
-- Idempotent: the setting is inserted once; the functions are replaced.

insert into public.settings (key, value) values ('portfolio_gate', jsonb_build_object(
  'bankroll_usd', 10000,
  'min_settled_dates', 30, 'min_decisions', 60,
  'lower_bound_level', 0.80, 'max_city_day_share', 0.25,
  'cap_per_strategy', 0.40,
  'decided_by', 'Claude, on Hassan''s delegation (24 Sep): the plan''s suggested gate and bankroll',
  'note', 'plan v2 P5.10'))
on conflict (key) do nothing;

-- Activation: once, from the suspended account P5.1 opened, before it has
-- done anything. The placeholder cash becomes the bankroll through one
-- activity row, so the desk's books still balance.
create or replace function public.activate_portfolio_account(p_bankroll numeric, p_approved_by text, p_reason text)
returns jsonb language plpgsql security definer set search_path = '' as $$
declare a public.paper_accounts; v_other bigint;
begin
  if p_bankroll is null or p_bankroll <= 0 or p_bankroll >= 1000000000 then
    raise exception 'a bankroll must be above 0 (got %)', p_bankroll;
  end if;
  if coalesce(btrim(p_approved_by), '') = '' or coalesce(btrim(p_reason), '') = '' then
    raise exception 'activating the portfolio names who approved it and why';
  end if;
  select * into a from public.paper_accounts where kind = 'portfolio' and status <> 'retired' for update;
  if not found then
    raise exception 'there is no portfolio account';
  end if;
  if a.status = 'active' then
    return jsonb_build_object('account_id', a.account_id, 'status', 'active', 'already', true);
  end if;
  select count(*) into v_other from public.paper_activity
   where account_id = a.account_id and event_type <> 'account_opened';
  if v_other > 0 or exists (select 1 from public.paper_orders where account_id = a.account_id)
     or exists (select 1 from public.paper_positions where account_id = a.account_id) then
    raise exception 'the portfolio account has history; its bankroll cannot be reset';
  end if;
  update public.paper_accounts
     set bankroll_usd = p_bankroll, starting_cash = p_bankroll, cash = p_bankroll, reserved_cash = 0,
         status = 'active', mode = 'automatic', entries_paused = false,
         policy = jsonb_build_object('cities', jsonb_build_array('ALL'), 'min_edge', 0,
                                     'strategies', '[]'::jsonb, 'allocation', '{}'::jsonb,
                                     'max_plan_usd', p_bankroll, 'max_exposure_usd', p_bankroll,
                                     'auto_exit_enabled', true, 'stop_loss_fraction', 0.15,
                                     'take_profit_fraction', 0.25),
         policy_version = a.policy_version + 1
   where account_id = a.account_id;
  insert into public.paper_activity (account_id, event_type, payload, cash_delta)
  values (a.account_id, 'portfolio_activated',
          jsonb_build_object('bankroll', p_bankroll, 'approved_by', btrim(p_approved_by),
                             'reason', btrim(p_reason), 'placeholder_cash', a.cash),
          p_bankroll - a.cash);
  return jsonb_build_object('account_id', a.account_id, 'status', 'active', 'bankroll', p_bankroll,
                            'already', false);
end $$;
revoke all on function public.activate_portfolio_account(numeric, text, text) from public, anon, authenticated;
grant execute on function public.activate_portfolio_account(numeric, text, text) to service_role;

-- The nightly allocation: which strategies the portfolio trades, and what
-- share of it each may use. Only strategies in the portfolio state, each at
-- most the cap, and never more than the whole bankroll.
create or replace function public.set_portfolio_allocation(p_weights jsonb, p_version text)
returns jsonb language plpgsql security definer set search_path = '' as $$
declare a public.paper_accounts; k text; v numeric; total numeric := 0; cap numeric; strategies jsonb := '[]'::jsonb;
begin
  select * into a from public.paper_accounts where kind = 'portfolio' and status = 'active' for update;
  if not found then
    raise exception 'the portfolio account is not active';
  end if;
  select coalesce((value->>'cap_per_strategy')::numeric, 0.40) into cap
    from public.settings where key = 'portfolio_gate';
  cap := coalesce(cap, 0.40);
  for k, v in select key, value::numeric from jsonb_each_text(coalesce(p_weights, '{}'::jsonb)) loop
    if v < 0 or v > cap + 1e-9 then
      raise exception 'weight % for % is outside [0, %]', v, k, cap;
    end if;
    if not exists (select 1 from public.strategy_state where strategy_id = k and state = 'portfolio') then
      raise exception '% is not in the portfolio state', k;
    end if;
    total := total + v;
    if v > 0 then strategies := strategies || to_jsonb(k); end if;
  end loop;
  if total > 1 + 1e-9 then
    raise exception 'weights sum to %, more than the whole bankroll', total;
  end if;
  update public.paper_accounts
     set policy = policy || jsonb_build_object('strategies', strategies, 'allocation', coalesce(p_weights, '{}'::jsonb),
                                               'allocation_version', p_version),
         policy_version = policy_version + 1
   where account_id = a.account_id
     and (policy->'allocation' is distinct from coalesce(p_weights, '{}'::jsonb)
          or policy->'strategies' is distinct from strategies);
  return jsonb_build_object('account_id', a.account_id, 'strategies', strategies, 'total', total,
                            'version', p_version);
end $$;
revoke all on function public.set_portfolio_allocation(jsonb, text) from public, anon, authenticated;
grant execute on function public.set_portfolio_allocation(jsonb, text) to service_role;
