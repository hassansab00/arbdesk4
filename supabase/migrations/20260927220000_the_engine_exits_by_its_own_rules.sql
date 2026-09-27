-- ===========================================================================
-- THE ENGINE EXITS BY ITS OWN RULES (plan v2 P5.12 part 3b, step 3)
--
-- S10's own rules sell: a held bucket the day's maximum has already ruled
-- out is sold at the bid (SELL), and a held bucket is swapped for a better
-- one when the growth gained beats the exit cost and h_switch (SWITCH). The
-- engine records those decisions; now they are carried out on the strategy's
-- shadow ledger, as its BUYs are (20260927210000).
--
-- submit_engine_exit(account, decision, band, side, shares, limit)
--     A SELL order for what the ledger holds, named for its decision: a SELL
--     or SWITCH decided in the last 15 minutes by the ledger's own strategy,
--     on a shadow ledger, for a switched-on strategy; never more than the
--     position less what is already on its way out. Origin 'automatic', the
--     decision in the order's context, 5 minutes to fill (the tick fills it
--     at once). One order per decision, band and side.
-- publish_engine_plan
--     Also takes a SWITCH decision: its buy half is a plan like any BUY's.
-- record_paper_trade
--     A SELL fill closes its trades as 'engine_exit' when the order names an
--     engine decision; every other SELL closes as 'auto_exit', as before.
-- ===========================================================================

create or replace function public.submit_engine_exit(p_account uuid, p_decision bigint, p_band uuid, p_side text,
                                                     p_shares numeric, p_limit numeric)
returns uuid
language plpgsql
set search_path = ''
as $$
declare a public.paper_accounts; d public.decisions; pos public.paper_positions; b public.bands;
  id uuid; k uuid; pending numeric;
begin
  select * into a from public.paper_accounts where account_id = p_account for update;
  if not found then raise exception 'Unknown account'; end if;
  if a.kind is distinct from 'shadow' then raise exception 'Engine orders go to a shadow ledger only'; end if;
  k := md5('arbdesk:engine-exit:' || p_account::text || ':' || p_decision::text || ':' || p_band::text || ':'
           || coalesce(p_side, ''))::uuid;
  select order_id into id from public.paper_orders where account_id = p_account and command_key = k;
  if found then return id; end if;
  select * into d from public.decisions where decision_id = p_decision;
  if not found or d.action not in ('SELL', 'SWITCH') then
    raise exception 'An engine SELL or SWITCH decision is required'; end if;
  if d.strategy_id is distinct from a.strategy_id then
    raise exception 'A decision of % on the ledger of %', d.strategy_id, a.strategy_id; end if;
  if d.decided_at < now() - interval '15 minutes' or d.decided_at > now() then
    raise exception 'Decision stale or future'; end if;
  if not exists (select 1 from public.strategies s where s.strategy_id = d.strategy_id and s.enabled) then
    raise exception 'Strategy % is not switched on', d.strategy_id; end if;
  select * into pos from public.paper_positions where account_id = p_account and band_id = p_band and side = p_side;
  if not found then raise exception 'No position'; end if;
  select coalesce(sum(shares), 0) into pending from public.paper_orders
   where account_id = p_account and band_id = p_band and side = p_side and action = 'SELL'
     and status in ('queued', 'working');
  if p_shares is null or p_shares <= 0 or p_shares > pos.shares - pending then
    raise exception 'Shares already sold or reserved for exit'; end if;
  select * into b from public.bands where band_id = p_band;
  insert into public.paper_orders(account_id, command_key, band_id, token_id, side, action, origin, strategy_id,
                                  shares, limit_price, cash_ceiling, policy_version, expires_at, reason, context)
  values (p_account, k, p_band, case when p_side = 'YES' then b.token_yes else b.token_no end, p_side, 'SELL',
          'automatic', d.strategy_id, p_shares, p_limit, 0, a.policy_version, now() + interval '5 minutes',
          'engine decision ' || p_decision,
          jsonb_build_object('source', 'engine', 'decision_id', p_decision, 'action', d.action))
  returning order_id into id;
  insert into public.paper_activity(account_id, order_id, event_type, payload)
    values (p_account, id, 'exit_submitted',
            jsonb_build_object('shares', p_shares, 'minimum_price', p_limit, 'decision_id', p_decision,
                               'action', d.action));
  return id;
end $$;

revoke all on function public.submit_engine_exit(uuid, bigint, uuid, text, numeric, numeric) from public, anon, authenticated;
grant execute on function public.submit_engine_exit(uuid, bigint, uuid, text, numeric, numeric) to service_role;

create or replace function public.publish_engine_plan(p_account uuid, p_decision bigint, p_legs jsonb, p_evidence jsonb)
returns uuid
language plpgsql
set search_path = ''
as $$
declare a public.paper_accounts; d public.decisions; id uuid; k uuid;
begin
  select * into a from public.paper_accounts where account_id = p_account for update;
  if not found then raise exception 'Unknown account'; end if;
  if a.kind is distinct from 'shadow' then raise exception 'Engine orders go to a shadow ledger only'; end if;
  k := md5('arbdesk:engine:' || p_account::text || ':' || p_decision::text)::uuid;
  select plan_id into id from public.paper_trade_plans where account_id = p_account and command_key = k;
  if found then return id; end if;
  select * into d from public.decisions where decision_id = p_decision;
  -- A SWITCH's buy half is a plan too (step 3).
  if not found or d.action not in ('BUY', 'SWITCH') then raise exception 'An engine BUY decision is required'; end if;
  if d.strategy_id is distinct from a.strategy_id then
    raise exception 'A decision of % on the ledger of %', d.strategy_id, a.strategy_id; end if;
  if d.decided_at < now() - interval '15 minutes' or d.decided_at > now() then
    raise exception 'Decision stale or future'; end if;
  if not exists (select 1 from public.strategies s where s.strategy_id = d.strategy_id and s.enabled) then
    raise exception 'Strategy % is not switched on', d.strategy_id; end if;
  insert into public.paper_trade_plans(account_id, signal_id, command_key, expires_at, status, reason, strategy_id,
                                       legs, evidence, policy_version)
  values (p_account, null, k, now() + interval '5 minutes', 'pending_approval', 'engine decision ' || p_decision,
          d.strategy_id, p_legs, coalesce(p_evidence, '{}'::jsonb) || jsonb_build_object('decision_id', p_decision),
          a.policy_version)
  returning plan_id into id;
  if a.mode = 'automatic' and not a.entries_paused then
    begin perform arbdesk_private.queue_plan(id, 'automatic');
    exception when others then
      update public.paper_trade_plans set status = 'blocked', reason = sqlerrm where plan_id = id;
    end;
  end if;
  return id;
end $$;

revoke all on function public.publish_engine_plan(uuid, bigint, jsonb, jsonb) from public, anon, authenticated;
grant execute on function public.publish_engine_plan(uuid, bigint, jsonb, jsonb) to service_role;

create or replace function arbdesk_private.record_paper_trade()
 returns trigger
 language plpgsql
 security definer
 set search_path to ''
as $function$
declare
  q numeric; cost numeric; fee numeric;
  m record;
  automatic boolean;
  legs integer;
  snap jsonb;
  new_trade uuid;
begin
  if new.status not in ('filled','partial') or coalesce(old.status,'') = new.status then
    return null;
  end if;
  q    := (new.result->>'shares')::numeric;
  cost := (new.result->>'notional')::numeric;
  fee  := coalesce((new.result->>'fee')::numeric, 0);
  if q is null or q <= 0 then
    return null;
  end if;

  if new.action = 'SELL' then
    -- An exit the engine decided (P5.12 part 3b) says so; every other SELL
    -- closes as before.
    perform arbdesk_private.close_paper_trades(
      new.account_id, new.band_id, new.side, q, cost, fee,
      case when new.context ? 'decision_id' then 'engine_exit' else 'auto_exit' end, now());
    return null;
  end if;

  if exists (select 1 from public.paper_trades where order_id = new.order_id) then
    return null;
  end if;

  select mk.city_key, mk.resolution_date into m
    from public.bands b join public.markets mk on mk.market_id = b.market_id
   where b.band_id = new.band_id;

  select a.mode = 'automatic' into automatic
    from public.paper_accounts a where a.account_id = new.account_id;

  select jsonb_array_length(p.legs) into legs
    from public.paper_trade_plans p where p.plan_id = new.plan_id;

  snap := arbdesk_private.decision_snapshot(new.signal_id, new.band_id);

  insert into public.paper_trades(
    account_id, order_id, signal_id, strategy_id, band_id, city_key,
    resolution_date, side, action, opened_at, shares, avg_fill_price,
    quoted_price, slippage_paid, fee_paid, partial_fill, requested_shares,
    legs_requested, approved_by_user,
    fill_quality, forecast_version, calibration_version, cost_version,
    regime_label)
  values (
    new.account_id, new.order_id, new.signal_id, new.strategy_id, new.band_id,
    m.city_key, m.resolution_date, new.side, 'BUY', now(), q, cost / q,
    new.limit_price, cost - new.limit_price * q, fee, q < new.shares, new.shares,
    legs, not coalesce(automatic, false),
    case when new.shares > 0 then q / new.shares end,
    nullif(snap->>'forecast_version', '')::uuid,
    nullif(snap->>'calibration_version', '')::uuid,
    nullif(snap->>'cost_version', '')::uuid,
    nullif(snap->>'regime_label', ''))
  returning trade_id into new_trade;

  insert into public.ledger(
    trade_id, signal_id, event_type, stage, strategy_id, band_id, regime_label,
    forecast_version, calibration_version, cost_version, payload, detail)
  values (
    new_trade, new.signal_id, 'fill', 'fill', new.strategy_id, new.band_id,
    nullif(snap->>'regime_label', ''),
    snap->>'forecast_version', snap->>'calibration_version', snap->>'cost_version',
    jsonb_build_object(
      'order_id', new.order_id, 'side', new.side,
      'requested_shares', new.shares, 'filled_shares', q,
      'limit_price', new.limit_price, 'avg_fill_price', cost / q,
      'notional', cost, 'fee', fee,
      'fill_quality', case when new.shares > 0 then q / new.shares end),
    snap);
  return null;
end $function$;
