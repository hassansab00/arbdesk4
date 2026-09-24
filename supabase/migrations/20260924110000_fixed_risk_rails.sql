-- THE FIXED RAILS (plan v2, step P5.9, part 1).
--
-- DECIDED 24 Sep: Hassan delegated the P5.9 decision to Claude ("do what's
-- the most optimal option"); the rails are the plan's defaults. They are
-- fixed - never learned, never loosened by the engine:
--
--   daily_loss_frac   0.05   no new entry once today's realised loss reaches
--                            5% of the account's equity at the start of the day
--   city_day_frac     0.03   at most 3% of equity on one city-day (held +
--                            reserved + this plan)
--   cluster_day_frac  0.08   at most 8% per correlated cluster-day. The clusters
--                            are part 2 (fitted weekly from forecast errors);
--                            until then a city is its own cluster, so the 3%
--                            city-day rail is the one that binds
--   max_price         0.97   no YES or NO buy above 97c
--   close_buffer_min  15     no entry within 15 min of the market's close: the
--                            end of its local day (markets carry no scheduled
--                            close; the outcome is decided when the day ends)
--   trading_halt             the kill switch: settings 'trading_halt'.halted
--
-- Enforced in queue_plan, which every automatic and approved entry passes
-- through, so a rail holds whoever proposed the order, the solver included.
-- Exits are not entries and are not railed: a desk can always get out.
--
-- queue_plan is the live body (md5 737994a7... on 24 Sep, = 20260924090000)
-- with the rail checks added and marked P5.9.

insert into public.settings (key, value) values ('risk_rails', jsonb_build_object(
  'daily_loss_frac', 0.05, 'city_day_frac', 0.03, 'cluster_day_frac', 0.08,
  'max_price', 0.97, 'close_buffer_min', 15,
  'decided_by', 'Claude, on Hassan''s delegation (24 Sep): the plan''s defaults',
  'note', 'plan v2 P5.9 fixed rails; never learned'))
on conflict (key) do nothing;
insert into public.settings (key, value) values ('trading_halt', jsonb_build_object('halted', false, 'reason', null))
on conflict (key) do nothing;

-- The kill switch, with its reason on record.
create or replace function public.set_trading_halt(p_halted boolean, p_reason text)
returns jsonb language plpgsql security definer set search_path = '' as $$
begin
  if p_halted and coalesce(btrim(p_reason), '') = '' then
    raise exception 'halting the desk needs a reason';
  end if;
  update public.settings set value = jsonb_build_object('halted', p_halted, 'reason', nullif(btrim(coalesce(p_reason,'')), ''),
                                                         'set_at', now())
   where key = 'trading_halt';
  return jsonb_build_object('halted', p_halted);
end $$;
revoke all on function public.set_trading_halt(boolean, text) from public, anon, authenticated;
grant execute on function public.set_trading_halt(boolean, text) to service_role;

create or replace function arbdesk_private.queue_plan(p_plan uuid,p_origin text) returns uuid
language plpgsql set search_path='' as $$
declare plan public.paper_trade_plans; a public.paper_accounts; leg jsonb; b public.bands;
  total numeric; exposure numeric; id uuid;
  rails jsonb; halt jsonb; equity numeric; pnl_today numeric; mk public.markets; tz text;
  v_close timestamptz; on_market numeric; leg_usd numeric;
begin
  select * into plan from public.paper_trade_plans where plan_id=p_plan;
  select * into a from public.paper_accounts where account_id=plan.account_id for update;
  select * into plan from public.paper_trade_plans where plan_id=p_plan for update;
  if a.status='retired' then raise exception 'Desk retired'; end if;
  -- P5.1: a suspended desk takes no new entries.
  if a.status='suspended' then raise exception 'Desk suspended'; end if;
  if plan.status='queued' then return p_plan; end if;
  if plan.plan_id is null or p_origin not in ('automatic','assisted') then raise exception 'Unknown plan or origin'; end if;
  if plan.status<>'pending_approval' or now()>plan.expires_at then raise exception 'Plan unavailable or expired'; end if;
  if a.policy_version<>plan.policy_version then raise exception 'Policy changed; a new plan is required'; end if;
  if p_origin='automatic' and (a.mode<>'automatic' or a.entries_paused) then raise exception 'Automatic entries paused'; end if;
  -- P5.1: a shadow ledger is one strategy's evidence, whatever its policy says.
  if a.kind='shadow' and plan.strategy_id is distinct from a.strategy_id then
    raise exception 'Shadow ledger of % trades only that strategy', a.strategy_id; end if;
  if not (a.policy->'strategies' ? plan.strategy_id) then raise exception 'Strategy outside policy'; end if;
  if not coalesce((plan.evidence->>'net_edge_per_share')::numeric >= (a.policy->>'min_edge')::numeric,false)
    then raise exception 'Insufficient verified net edge'; end if;
  if jsonb_array_length(plan.legs)=0 then raise exception 'No executable legs'; end if;
  -- P5.9 THE FIXED RAILS (settings 'risk_rails'; never learned). Every
  -- automatic or approved entry passes here, whoever proposed it.
  select value into halt from public.settings where key='trading_halt';
  if coalesce((halt->>'halted')::boolean,false) then
    raise exception 'Rail: trading halted (%)', coalesce(halt->>'reason','no reason given'); end if;
  select value into rails from public.settings where key='risk_rails';
  rails := coalesce(rails,'{}'::jsonb);
  select sum((x->>'cash_ceiling')::numeric) into total from jsonb_array_elements(plan.legs) x;
  select coalesce(sum(cost_basis),0) into exposure from public.paper_positions where account_id=a.account_id;
  -- Equity at cost: free and reserved cash plus what the open positions cost.
  equity := a.cash + exposure;
  select coalesce(sum(net_pnl),0) into pnl_today from public.paper_trades
   where account_id=a.account_id and closed_at >= date_trunc('day', now() at time zone 'utc') at time zone 'utc';
  if pnl_today < 0 and -pnl_today >= coalesce((rails->>'daily_loss_frac')::numeric,0.05) * (equity - pnl_today) then
    raise exception 'Rail: daily loss limit reached (% today)', round(pnl_today,2); end if;
  if total is null or total<=0 or total>(a.policy->>'max_plan_usd')::numeric or total>a.cash-a.reserved_cash
    or exposure+a.reserved_cash+total>(a.policy->>'max_exposure_usd')::numeric then raise exception 'Account or policy cash limit'; end if;
  for leg in select * from jsonb_array_elements(plan.legs) loop
    select * into b from public.bands where band_id=(leg->>'band_id')::uuid;
    if not found then raise exception 'Unknown band'; end if;
    if not coalesce((leg->>'cash_ceiling')::numeric>0 and
      (leg->>'cash_ceiling')::numeric>=(leg->>'shares')::numeric*(leg->>'limit_price')::numeric,false)
      then raise exception 'Invalid leg cash reservation'; end if;
    if not exists(select 1 from public.markets m where m.market_id=b.market_id and not m.closed
      and (a.policy->'cities' ? m.city_key or a.policy->'cities' ? 'ALL')) then raise exception 'Market outside policy or closed'; end if;
    if (leg->>'limit_price')::numeric > coalesce((rails->>'max_price')::numeric,0.97) then
      raise exception 'Rail: % at % is above the % price bound', leg->>'side', leg->>'limit_price',
        coalesce((rails->>'max_price')::numeric,0.97); end if;
    select * into mk from public.markets where market_id=b.market_id;
    select timezone into tz from public.cities where city_key=mk.city_key;
    -- The market has no scheduled close; its outcome is decided when the
    -- city's local day ends, so that is the close the rail measures from.
    v_close := ((mk.resolution_date + 1)::timestamp) at time zone coalesce(tz,'UTC');
    if now() > v_close - make_interval(mins => coalesce((rails->>'close_buffer_min')::int,15)) then
      raise exception 'Rail: within % min of the local close', coalesce((rails->>'close_buffer_min')::int,15); end if;
    -- Per city-day: what the account already holds or has reserved on this
    -- market, plus every leg of this plan on it. Clusters (P5.9 part 2) are
    -- not fitted yet, so a city is its own cluster and this cap is the tighter.
    select coalesce(sum(p.cost_basis),0) into on_market from public.paper_positions p
      join public.bands bb on bb.band_id=p.band_id where p.account_id=a.account_id and bb.market_id=mk.market_id;
    select on_market + coalesce(sum(o.cash_ceiling),0) into on_market from public.paper_orders o
      join public.bands bb on bb.band_id=o.band_id
     where o.account_id=a.account_id and bb.market_id=mk.market_id and o.status in ('queued','working') and o.action='BUY';
    select coalesce(sum((x->>'cash_ceiling')::numeric),0) into leg_usd from jsonb_array_elements(plan.legs) x
      join public.bands bb on bb.band_id=(x->>'band_id')::uuid where bb.market_id=mk.market_id;
    if on_market + leg_usd > coalesce((rails->>'city_day_frac')::numeric,0.03) * equity then
      raise exception 'Rail: % on % % would exceed % of the account', round(on_market + leg_usd,2), mk.city_key,
        mk.resolution_date, coalesce((rails->>'city_day_frac')::numeric,0.03); end if;
    if exists(select 1 from public.paper_positions p where p.account_id=a.account_id and p.band_id=b.band_id
                and p.side=leg->>'side' and p.shares>0)
       or exists(select 1 from public.paper_orders o where o.account_id=a.account_id and o.band_id=b.band_id
                and o.side=leg->>'side' and o.status in ('queued','working'))
      then raise exception 'Already holding or ordering % on this band', leg->>'side'; end if;
    insert into public.paper_orders(account_id,plan_id,command_key,band_id,token_id,side,action,origin,
      signal_id,strategy_id,shares,limit_price,cash_ceiling,policy_version,expires_at,context)
    values(a.account_id,p_plan,gen_random_uuid(),b.band_id,case when leg->>'side'='YES' then b.token_yes else b.token_no end,
      leg->>'side','BUY',p_origin,plan.signal_id,plan.strategy_id,(leg->>'shares')::numeric,(leg->>'limit_price')::numeric,
      (leg->>'cash_ceiling')::numeric,a.policy_version,plan.expires_at,plan.evidence);
  end loop;
  update public.paper_accounts set reserved_cash=reserved_cash+total where account_id=a.account_id;
  update public.paper_trade_plans set status='queued' where plan_id=p_plan;
  insert into public.paper_activity(account_id,event_type,payload)
    values(a.account_id,'plan_authorized',jsonb_build_object('plan_id',p_plan,'origin',p_origin,'reserved',total));
  return p_plan;
end $$;
revoke all on function arbdesk_private.queue_plan(uuid,text) from public;
grant execute on function arbdesk_private.queue_plan(uuid,text) to service_role;
