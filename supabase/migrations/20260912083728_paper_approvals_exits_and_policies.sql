begin;
create table public.paper_trade_plans (
  plan_id uuid primary key default gen_random_uuid(),
  account_id uuid not null references public.paper_accounts(account_id),
  signal_id bigint references public.signals(signal_id),
  command_key uuid not null,
  created_at timestamptz not null default now(),
  expires_at timestamptz not null,
  status text not null check(status in ('pending_approval','queued','blocked','rejected','expired')),
  reason text not null,
  strategy_id text not null,
  legs jsonb not null check(jsonb_typeof(legs)='array'),
  evidence jsonb not null,
  policy_version bigint not null,
  unique(account_id,command_key)
);
create index paper_trade_plans_signal on public.paper_trade_plans(signal_id);
alter table public.paper_trade_plans enable row level security;
revoke all on public.paper_trade_plans from anon,authenticated,service_role;
grant select on public.paper_trade_plans to authenticated;
grant all on public.paper_trade_plans to service_role;
create policy owner_read on public.paper_trade_plans for select to authenticated using(exists(
  select 1 from public.paper_accounts a where a.account_id=paper_trade_plans.account_id and a.owner_id=(select auth.uid())));
alter table public.paper_orders add column plan_id uuid references public.paper_trade_plans(plan_id);
create index paper_orders_plan on public.paper_orders(plan_id);

create function arbdesk_private.set_paper_policy(p_account uuid,p_mode text,p_paused boolean,p_policy jsonb) returns boolean
language plpgsql security definer set search_path='' as $$
begin
  if auth.uid() is null or not exists(select 1 from public.paper_accounts where account_id=p_account and owner_id=auth.uid()) then
    raise exception 'Account access denied'; end if;
  if p_mode is null or p_paused is null or p_mode not in ('manual','assisted','automatic') or jsonb_typeof(p_policy->'strategies') is distinct from 'array'
    or jsonb_typeof(p_policy->'cities') is distinct from 'array'
    or not coalesce((p_policy->>'max_plan_usd')::numeric between 1 and 1000000,false)
    or not coalesce((p_policy->>'max_exposure_usd')::numeric between 1 and 1000000,false)
    or not coalesce((p_policy->>'min_edge')::numeric between 0 and 1,false)
    then raise exception 'Valid strategy/city scope, dollar limits and minimum edge are required'; end if;
  update public.paper_accounts set mode=p_mode,entries_paused=p_paused,
    policy=(policy - array['strategies','cities','max_plan_usd','max_exposure_usd','min_edge']) ||
      jsonb_build_object('strategies',p_policy->'strategies','cities',p_policy->'cities',
        'max_plan_usd',p_policy->'max_plan_usd','max_exposure_usd',p_policy->'max_exposure_usd','min_edge',p_policy->'min_edge'),
    policy_version=policy_version+1 where account_id=p_account;
  insert into public.paper_activity(account_id,event_type,payload)
    values(p_account,'policy_changed',jsonb_build_object('mode',p_mode,'entries_paused',p_paused,'policy',p_policy));
  return true;
end $$;
revoke all on function arbdesk_private.set_paper_policy(uuid,text,boolean,jsonb) from public,anon;
grant usage on schema arbdesk_private to authenticated;
grant execute on function arbdesk_private.set_paper_policy(uuid,text,boolean,jsonb) to authenticated;
create function public.set_paper_policy(p_account uuid,p_mode text,p_paused boolean,p_policy jsonb) returns boolean
language sql security invoker set search_path='' as $$ select arbdesk_private.set_paper_policy(p_account,p_mode,p_paused,p_policy) $$;
revoke all on function public.set_paper_policy(uuid,text,boolean,jsonb) from public,anon;
grant execute on function public.set_paper_policy(uuid,text,boolean,jsonb) to authenticated;

create function arbdesk_private.queue_plan(p_plan uuid,p_origin text) returns uuid
language plpgsql set search_path='' as $$
declare plan public.paper_trade_plans; a public.paper_accounts; leg jsonb; b public.bands;
  total numeric; exposure numeric; id uuid;
begin
  select * into plan from public.paper_trade_plans where plan_id=p_plan;
  select * into a from public.paper_accounts where account_id=plan.account_id for update;
  select * into plan from public.paper_trade_plans where plan_id=p_plan for update;
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
grant usage on schema arbdesk_private to service_role;
grant execute on function arbdesk_private.queue_plan(uuid,text) to service_role;

create function public.publish_paper_plan(p_account uuid,p_command uuid,p_signal bigint,p_legs jsonb,p_evidence jsonb,p_block_reason text default null) returns uuid
language plpgsql security invoker set search_path='' as $$
declare a public.paper_accounts; s public.signals; id uuid;
begin
  select * into a from public.paper_accounts where account_id=p_account for update;
  if not found then raise exception 'Unknown account'; end if;
  select plan_id into id from public.paper_trade_plans where account_id=p_account and command_key=p_command;
  if found then return id; end if;
  select * into s from public.signals where signal_id=p_signal;
  if not found or s.action<>'ENTER' or s.strategy_id='system' then raise exception 'Trade entry signal required'; end if;
  if s.fired_at<now()-interval '15 minutes' or s.fired_at>now() then raise exception 'Signal stale or future'; end if;
  insert into public.paper_trade_plans(account_id,signal_id,command_key,expires_at,status,reason,strategy_id,legs,evidence,policy_version)
    values(p_account,p_signal,p_command,now()+interval '5 minutes',case when p_block_reason is null then 'pending_approval' else 'blocked' end,
      coalesce(p_block_reason,s.reason,'strategy decision'),s.strategy_id,p_legs,p_evidence,a.policy_version) returning plan_id into id;
  if p_block_reason is null and a.mode='automatic' and not a.entries_paused then
    begin perform arbdesk_private.queue_plan(id,'automatic');
    exception when others then
      update public.paper_trade_plans set status='blocked',reason=sqlerrm where plan_id=id;
    end;
  end if;
  return id;
end $$;
revoke all on function public.publish_paper_plan(uuid,uuid,bigint,jsonb,jsonb,text) from public,anon,authenticated;
grant execute on function public.publish_paper_plan(uuid,uuid,bigint,jsonb,jsonb,text) to service_role;

create function arbdesk_private.approve_paper_plan(p_plan uuid,p_approve boolean default true) returns uuid
language plpgsql security definer set search_path='' as $$
declare plan public.paper_trade_plans;
begin
  select * into plan from public.paper_trade_plans where plan_id=p_plan;
  if not found or auth.uid() is null or not exists(select 1 from public.paper_accounts where account_id=plan.account_id and owner_id=auth.uid()) then
    raise exception 'Account access denied'; end if;
  if p_approve is null then raise exception 'Approval decision required'; end if;
  if not p_approve then
    perform 1 from public.paper_accounts where account_id=plan.account_id for update;
    select * into plan from public.paper_trade_plans where plan_id=p_plan for update;
    if plan.status='rejected' then return p_plan; end if;
    if plan.status<>'pending_approval' then raise exception 'Plan no longer awaiting approval'; end if;
    update public.paper_trade_plans set status='rejected' where plan_id=p_plan and status='pending_approval';
    insert into public.paper_activity(account_id,event_type,payload)
      values(plan.account_id,'plan_rejected',jsonb_build_object('plan_id',p_plan,'actor',auth.uid()));
    return p_plan;
  end if;
  return arbdesk_private.queue_plan(p_plan,'assisted');
end $$;
revoke all on function arbdesk_private.approve_paper_plan(uuid,boolean) from public,anon;
grant usage on schema arbdesk_private to authenticated;
grant execute on function arbdesk_private.approve_paper_plan(uuid,boolean) to authenticated;
create function public.approve_paper_plan(p_plan uuid,p_approve boolean default true) returns uuid
language sql security invoker set search_path='' as $$ select arbdesk_private.approve_paper_plan(p_plan,p_approve) $$;
revoke all on function public.approve_paper_plan(uuid,boolean) from public,anon;
grant execute on function public.approve_paper_plan(uuid,boolean) to authenticated;

create function arbdesk_private.submit_paper_exit(p_account uuid,p_command uuid,p_band uuid,p_side text,p_shares numeric,p_limit numeric) returns uuid
language plpgsql security definer set search_path='' as $$
declare a public.paper_accounts; pos public.paper_positions; b public.bands; id uuid; pending numeric;
begin
  select * into a from public.paper_accounts where account_id=p_account for update;
  if not found or auth.uid() is null or a.owner_id<>auth.uid() then raise exception 'Account access denied'; end if;
  select order_id into id from public.paper_orders where account_id=p_account and command_key=p_command;
  if found then return id; end if;
  select * into pos from public.paper_positions where account_id=p_account and band_id=p_band and side=p_side;
  if not found then raise exception 'No position'; end if;
  select coalesce(sum(shares),0) into pending from public.paper_orders where account_id=p_account and band_id=p_band
    and side=p_side and action='SELL' and status in ('queued','working');
  if p_shares>pos.shares-pending then raise exception 'Shares already sold or reserved for exit'; end if;
  select * into b from public.bands where band_id=p_band;
  insert into public.paper_orders(account_id,command_key,band_id,token_id,side,action,origin,shares,limit_price,cash_ceiling,policy_version,expires_at,reason)
    values(p_account,p_command,p_band,case when p_side='YES' then b.token_yes else b.token_no end,p_side,'SELL','manual',
      p_shares,p_limit,0,a.policy_version,now()+interval '5 minutes','manual position reduction') returning order_id into id;
  insert into public.paper_activity(account_id,order_id,event_type,payload)
    values(p_account,id,'exit_submitted',jsonb_build_object('shares',p_shares,'minimum_price',p_limit));
  return id;
end $$;
revoke all on function arbdesk_private.submit_paper_exit(uuid,uuid,uuid,text,numeric,numeric) from public,anon;
grant usage on schema arbdesk_private to authenticated;
grant execute on function arbdesk_private.submit_paper_exit(uuid,uuid,uuid,text,numeric,numeric) to authenticated;
create function public.submit_paper_exit(p_account uuid,p_command uuid,p_band uuid,p_side text,p_shares numeric,p_limit numeric) returns uuid
language sql security invoker set search_path='' as $$ select arbdesk_private.submit_paper_exit(p_account,p_command,p_band,p_side,p_shares,p_limit) $$;
revoke all on function public.submit_paper_exit(uuid,uuid,uuid,text,numeric,numeric) from public,anon;
grant execute on function public.submit_paper_exit(uuid,uuid,uuid,text,numeric,numeric) to authenticated;
commit;
