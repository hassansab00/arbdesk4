-- Single-operator mode: remove the Paper Trades sign-in while keeping every
-- paper table and command behind a narrow server-side API using service_role.
-- The browser's anon key receives no new table or RPC privileges.
begin;

alter table public.paper_accounts add column access_mode text not null default 'member'
  check (access_mode in ('member','single_desk'));
alter table public.paper_accounts alter column owner_id drop not null;
alter table public.paper_accounts add constraint paper_account_owner_mode
  check ((access_mode='member' and owner_id is not null) or
         (access_mode='single_desk' and owner_id is null));
create unique index one_single_paper_desk on public.paper_accounts(access_mode)
  where access_mode='single_desk';

create function public.create_single_paper_account(p_name text,p_starting_cash numeric) returns uuid
language plpgsql security invoker set search_path='' as $$
declare id uuid;
begin
  select account_id into id from public.paper_accounts where access_mode='single_desk';
  if found then return id; end if;
  insert into public.paper_accounts(owner_id,access_mode,name,starting_cash,cash)
    values(null,'single_desk',p_name,p_starting_cash,p_starting_cash) returning account_id into id;
  insert into public.paper_activity(account_id,event_type,payload,cash_delta)
    values(id,'account_opened',jsonb_build_object('starting_cash',p_starting_cash,'access_mode','single_desk'),p_starting_cash);
  return id;
exception when unique_violation then
  select account_id into id from public.paper_accounts where access_mode='single_desk';
  return id;
end $$;

create function public.submit_single_paper_order(p_account uuid,p_command uuid,p_band uuid,p_side text,
  p_shares numeric,p_limit numeric,p_cash_ceiling numeric,p_reason text default '') returns uuid
language plpgsql security invoker set search_path='' as $$
declare a public.paper_accounts; b public.bands; m public.markets; id uuid; recent integer;
begin
  select * into a from public.paper_accounts where account_id=p_account for update;
  if not found or a.access_mode<>'single_desk' or a.owner_id is not null then raise exception 'Single paper desk unavailable'; end if;
  select order_id into id from public.paper_orders where account_id=p_account and command_key=p_command;
  if found then return id; end if;
  select count(*) into recent from public.paper_orders where account_id=p_account and requested_at>now()-interval '1 minute';
  if recent>=30 then raise exception 'Paper order rate limit reached'; end if;
  select * into b from public.bands where band_id=p_band;
  if not found or p_side not in ('YES','NO') then raise exception 'Unknown band or side'; end if;
  select * into m from public.markets where market_id=b.market_id;
  if m.closed or m.resolution_date < (now() at time zone 'UTC')::date-1 then raise exception 'Market is closed or historical'; end if;
  if p_cash_ceiling>a.cash-a.reserved_cash or p_cash_ceiling<p_shares*p_limit then
    raise exception 'Insufficient available cash or fee-inclusive ceiling'; end if;
  insert into public.paper_orders(account_id,command_key,band_id,token_id,side,action,origin,shares,
    limit_price,cash_ceiling,reason,policy_version,expires_at,context)
  values(p_account,p_command,p_band,case when p_side='YES' then b.token_yes else b.token_no end,
    p_side,'BUY','manual',p_shares,p_limit,p_cash_ceiling,p_reason,a.policy_version,now()+interval '5 minutes',
    jsonb_build_object('market',to_jsonb(m),'band',to_jsonb(b),'actor','single_desk')) returning order_id into id;
  update public.paper_accounts set reserved_cash=reserved_cash+p_cash_ceiling where account_id=p_account;
  insert into public.paper_activity(account_id,order_id,event_type,payload)
    values(p_account,id,'order_submitted',jsonb_build_object('origin','manual','reason',p_reason));
  return id;
end $$;

create function public.cancel_single_paper_order(p_order uuid) returns boolean
language plpgsql security invoker set search_path='' as $$
declare o public.paper_orders; a public.paper_accounts;
begin
  select * into o from public.paper_orders where order_id=p_order;
  if not found then raise exception 'Unknown order'; end if;
  select * into a from public.paper_accounts where account_id=o.account_id for update;
  if not found or a.access_mode<>'single_desk' or a.owner_id is not null then raise exception 'Single paper desk unavailable'; end if;
  select * into o from public.paper_orders where order_id=p_order for update;
  if o.status<>'queued' then return false; end if;
  update public.paper_orders set status='canceled' where order_id=p_order;
  update public.paper_accounts set reserved_cash=reserved_cash-o.cash_ceiling where account_id=o.account_id;
  insert into public.paper_activity(account_id,order_id,event_type,payload)
    values(o.account_id,p_order,'order_canceled','{}');
  return true;
end $$;

create function public.set_single_paper_policy(p_account uuid,p_mode text,p_paused boolean,p_policy jsonb) returns boolean
language plpgsql security invoker set search_path='' as $$
begin
  if not exists(select 1 from public.paper_accounts where account_id=p_account and access_mode='single_desk' and owner_id is null)
    then raise exception 'Single paper desk unavailable'; end if;
  if p_mode is null or p_paused is null or p_mode not in ('manual','assisted','automatic') or jsonb_typeof(p_policy->'strategies') is distinct from 'array'
    or jsonb_typeof(p_policy->'cities') is distinct from 'array'
    or not coalesce((p_policy->>'max_plan_usd')::numeric between 1 and 1000000,false)
    or not coalesce((p_policy->>'max_exposure_usd')::numeric between 1 and 1000000,false)
    or not coalesce((p_policy->>'min_edge')::numeric between 0 and 1,false)
    then raise exception 'Valid strategy/city scope, dollar limits and minimum edge are required'; end if;
  update public.paper_accounts set mode=p_mode,entries_paused=p_paused,
    policy=(policy-array['strategies','cities','max_plan_usd','max_exposure_usd','min_edge']) ||
      jsonb_build_object('strategies',p_policy->'strategies','cities',p_policy->'cities',
        'max_plan_usd',p_policy->'max_plan_usd','max_exposure_usd',p_policy->'max_exposure_usd','min_edge',p_policy->'min_edge'),
    policy_version=policy_version+1 where account_id=p_account;
  insert into public.paper_activity(account_id,event_type,payload)
    values(p_account,'policy_changed',jsonb_build_object('mode',p_mode,'entries_paused',p_paused,'policy',p_policy,'actor','single_desk'));
  return true;
end $$;

create function public.approve_single_paper_plan(p_plan uuid,p_approve boolean default true) returns uuid
language plpgsql security invoker set search_path='' as $$
declare plan public.paper_trade_plans;
begin
  select * into plan from public.paper_trade_plans where plan_id=p_plan;
  if not found or not exists(select 1 from public.paper_accounts where account_id=plan.account_id
      and access_mode='single_desk' and owner_id is null) then raise exception 'Single paper desk unavailable'; end if;
  if p_approve is null then raise exception 'Approval decision required'; end if;
  if not p_approve then
    perform 1 from public.paper_accounts where account_id=plan.account_id for update;
    select * into plan from public.paper_trade_plans where plan_id=p_plan for update;
    if plan.status='rejected' then return p_plan; end if;
    if plan.status<>'pending_approval' then raise exception 'Plan no longer awaiting approval'; end if;
    update public.paper_trade_plans set status='rejected' where plan_id=p_plan;
    insert into public.paper_activity(account_id,event_type,payload)
      values(plan.account_id,'plan_rejected',jsonb_build_object('plan_id',p_plan,'actor','single_desk'));
    return p_plan;
  end if;
  return arbdesk_private.queue_plan(p_plan,'assisted');
end $$;

create function public.submit_single_paper_exit(p_account uuid,p_command uuid,p_band uuid,p_side text,
  p_shares numeric,p_limit numeric) returns uuid
language plpgsql security invoker set search_path='' as $$
declare a public.paper_accounts; pos public.paper_positions; b public.bands; id uuid; pending numeric;
begin
  select * into a from public.paper_accounts where account_id=p_account for update;
  if not found or a.access_mode<>'single_desk' or a.owner_id is not null then raise exception 'Single paper desk unavailable'; end if;
  select order_id into id from public.paper_orders where account_id=p_account and command_key=p_command;
  if found then return id; end if;
  select * into pos from public.paper_positions where account_id=p_account and band_id=p_band and side=p_side;
  if not found then raise exception 'No position'; end if;
  select coalesce(sum(shares),0) into pending from public.paper_orders where account_id=p_account and band_id=p_band
    and side=p_side and action='SELL' and status in ('queued','working');
  if p_shares>pos.shares-pending then raise exception 'Shares already sold or reserved for exit'; end if;
  select * into b from public.bands where band_id=p_band;
  insert into public.paper_orders(account_id,command_key,band_id,token_id,side,action,origin,shares,limit_price,
    cash_ceiling,policy_version,expires_at,reason)
  values(p_account,p_command,p_band,case when p_side='YES' then b.token_yes else b.token_no end,p_side,'SELL','manual',
    p_shares,p_limit,0,a.policy_version,now()+interval '5 minutes','manual position reduction') returning order_id into id;
  insert into public.paper_activity(account_id,order_id,event_type,payload)
    values(p_account,id,'exit_submitted',jsonb_build_object('shares',p_shares,'minimum_price',p_limit));
  return id;
end $$;

create function public.set_single_paper_exit_policy(p_account uuid,p_enabled boolean,
  p_take_profit numeric,p_stop_loss numeric) returns boolean
language plpgsql security invoker set search_path='' as $$
begin
  if not exists(select 1 from public.paper_accounts where account_id=p_account
      and access_mode='single_desk' and owner_id is null)
    then raise exception 'Single paper desk unavailable'; end if;
  if p_enabled is null or not coalesce(p_take_profit>0 and p_take_profit<=10
      and p_stop_loss>0 and p_stop_loss<1,false) then
    raise exception 'Take-profit must be 0–10 and stop-loss 0–1, both strictly positive'; end if;
  update public.paper_accounts set policy=policy||jsonb_build_object(
    'auto_exit_enabled',p_enabled,'take_profit_fraction',p_take_profit,
    'stop_loss_fraction',p_stop_loss),policy_version=policy_version+1
    where account_id=p_account;
  insert into public.paper_activity(account_id,event_type,payload)
    values(p_account,'exit_policy_changed',jsonb_build_object('enabled',p_enabled,
      'take_profit_fraction',p_take_profit,'stop_loss_fraction',p_stop_loss,'actor','single_desk'));
  return true;
end $$;

do $$ declare signature text; begin
  foreach signature in array array[
    'create_single_paper_account(text,numeric)',
    'submit_single_paper_order(uuid,uuid,uuid,text,numeric,numeric,numeric,text)',
    'cancel_single_paper_order(uuid)',
    'set_single_paper_policy(uuid,text,boolean,jsonb)',
    'approve_single_paper_plan(uuid,boolean)',
    'submit_single_paper_exit(uuid,uuid,uuid,text,numeric,numeric)',
    'set_single_paper_exit_policy(uuid,boolean,numeric,numeric)'
  ] loop
    execute format('revoke all on function public.%s from public,anon,authenticated',signature);
    execute format('grant execute on function public.%s to service_role',signature);
  end loop;
end $$;

commit;
