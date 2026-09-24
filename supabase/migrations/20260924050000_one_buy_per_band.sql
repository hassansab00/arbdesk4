-- ===========================================================================
-- ONE BUY PER BAND AND SIDE (plan v2 P5.0 item 1)
--
-- Measured live on 24 Sep: of the 53 (desk, band, side) keys that ever got a
-- BUY, 14 got more than one - 35 orders, up to 5 on one key, every one of
-- them automatic. A signal that fires again on the next cycle publishes a new
-- plan, and nothing on the order path asked whether the desk already held,
-- or was already buying, that band.
--
-- queue_plan now refuses a leg when the desk holds shares on (band, side) or
-- has a live order (queued or working) there. The account row is locked FOR
-- UPDATE at the top, so two plans for one desk cannot both pass the check. An
-- automatic plan refused here is marked blocked with this message by
-- publish_paper_plan, which already catches queue_plan's refusals; an
-- assisted approval gets the message back.
--
-- The function is otherwise identical to 20260923100000 (the live body's md5
-- matched that file's on 24 Sep).
-- ===========================================================================

create or replace function arbdesk_private.queue_plan(p_plan uuid,p_origin text) returns uuid
language plpgsql set search_path='' as $$
declare plan public.paper_trade_plans; a public.paper_accounts; leg jsonb; b public.bands;
  total numeric; exposure numeric; id uuid;
begin
  select * into plan from public.paper_trade_plans where plan_id=p_plan;
  select * into a from public.paper_accounts where account_id=plan.account_id for update;
  select * into plan from public.paper_trade_plans where plan_id=p_plan for update;
  if a.status='retired' then raise exception 'Desk retired'; end if;
  if plan.status='queued' then return p_plan; end if;
  if plan.plan_id is null or p_origin not in ('automatic','assisted') then raise exception 'Unknown plan or origin'; end if;
  if plan.status<>'pending_approval' or now()>plan.expires_at then raise exception 'Plan unavailable or expired'; end if;
  if a.policy_version<>plan.policy_version then raise exception 'Policy changed; a new plan is required'; end if;
  if p_origin='automatic' and (a.mode<>'automatic' or a.entries_paused) then raise exception 'Automatic entries paused'; end if;
  if not (a.policy->'strategies' ? plan.strategy_id) then raise exception 'Strategy outside policy'; end if;
  if not coalesce((plan.evidence->>'net_edge_per_share')::numeric >= (a.policy->>'min_edge')::numeric,false)
    then raise exception 'Insufficient verified net edge'; end if;
  if jsonb_array_length(plan.legs)=0 then raise exception 'No executable legs'; end if;
  select sum((x->>'cash_ceiling')::numeric) into total from jsonb_array_elements(plan.legs) x;
  select coalesce(sum(cost_basis),0) into exposure from public.paper_positions where account_id=a.account_id;
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
