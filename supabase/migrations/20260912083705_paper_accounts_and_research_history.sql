-- Additive foundation: private research history and paper-account commands.
-- Existing screens and raw/source tables remain intact.
begin;
create schema if not exists arbdesk_private;
revoke all on schema arbdesk_private from public, anon, authenticated;

create table public.desk_members (
  user_id uuid primary key references auth.users(id),
  created_at timestamptz not null default now()
);
alter table public.desk_members enable row level security;
revoke all on public.desk_members from anon,authenticated,service_role;
grant select on public.desk_members to authenticated;
grant all on public.desk_members to service_role;
create policy self_read on public.desk_members for select to authenticated
  using (user_id=(select auth.uid()));

create table public.research_captures (
  capture_id uuid primary key default gen_random_uuid(),
  command_key text not null unique,
  captured_at timestamptz not null default clock_timestamp(),
  engine_version text not null,
  provenance text not null check(provenance in ('forward_capture','historical_import','source_revision')),
  source_relation text not null,
  source_key text not null,
  payload jsonb not null,
  payload_hash text not null,
  unique(source_relation,source_key,payload_hash)
);
create index research_captures_source_time on public.research_captures(source_relation,captured_at desc);
alter table public.research_captures enable row level security;
revoke all on public.research_captures from anon,authenticated,service_role;
grant select on public.research_captures to authenticated;
grant select,insert on public.research_captures to service_role;
create policy member_read on public.research_captures for select to authenticated
  using (exists(select 1 from public.desk_members where user_id=(select auth.uid())));

create function arbdesk_private.immutable_record() returns trigger
language plpgsql set search_path='' as $$
begin raise exception 'Append-only record; write a linked correction instead'; end $$;
create trigger research_immutable before update or delete on public.research_captures
  for each row execute function arbdesk_private.immutable_record();
create trigger research_no_truncate before truncate on public.research_captures
  for each statement execute function arbdesk_private.immutable_record();

create function arbdesk_private.archive_research_row() returns trigger
language plpgsql security definer set search_path='' as $$
declare payload jsonb; identity_key text; version text;
begin
  payload:=to_jsonb(new);
  identity_key:=coalesce(payload->>'prob_id',payload->>'signal_id',payload->>'version_id',
    payload->>'band_id',payload->>'city_key','row') || ':' || coalesce(payload->>'for_date','');
  version:=coalesce(payload->>'forecast_version',payload->>'calibration_version',
    payload->>'version_id','legacy-producer-unversioned');
  insert into public.research_captures(command_key,engine_version,provenance,source_relation,source_key,payload,payload_hash)
  values(gen_random_uuid()::text,version,'source_revision',tg_table_name,identity_key,payload,md5(payload::text))
  on conflict(source_relation,source_key,payload_hash) do nothing;
  return new;
end $$;
revoke all on function arbdesk_private.archive_research_row() from public;
do $$ declare t text; begin
  foreach t in array array['band_probabilities','signals','fact_forecast_outcome','fact_band_outcome',
    'fact_signal_outcome','model_versions'] loop
    if to_regclass('public.'||t) is not null then
      execute format('create trigger preserve_research_output after insert or update on public.%I for each row execute function arbdesk_private.archive_research_row()',t);
    end if;
  end loop;
end $$;

create function public.capture_research_state(p_command text,p_engine_version text) returns jsonb
language plpgsql security invoker set search_path='' as $$
declare t text; n integer; total integer:=0; stamp timestamptz:=clock_timestamp();
begin
  if nullif(p_command,'') is null or nullif(p_engine_version,'') is null then
    raise exception 'command and deployed engine version required';
  end if;
  foreach t in array array['v_synthesis_findings','v_learning_state','v_forecast_convergence'] loop
    if to_regclass('public.'||t) is null then raise exception 'Missing research source %',t; end if;
    execute format('insert into public.research_captures(command_key,captured_at,engine_version,provenance,source_relation,source_key,payload,payload_hash)
      select $1||'':''||$2||'':''||md5(to_jsonb(v)::text), $3,$4,''forward_capture'',$2,
      md5(to_jsonb(v)::text),to_jsonb(v),md5(to_jsonb(v)::text) from public.%I v
      on conflict do nothing',t) using p_command,t,stamp,p_engine_version;
    get diagnostics n=row_count; total:=total+n;
  end loop;
  return jsonb_build_object('rows',total,'command',p_command,'captured_at',stamp);
end $$;
revoke all on function public.capture_research_state(text,text) from public,anon,authenticated;
grant execute on function public.capture_research_state(text,text) to service_role;
do $$ declare t text; begin
  foreach t in array array['v_synthesis_findings','v_learning_state','v_forecast_convergence'] loop
    if to_regclass('public.'||t) is not null then execute format('grant select on public.%I to service_role',t); end if;
  end loop;
end $$;

create table public.paper_accounts (
  account_id uuid primary key default gen_random_uuid(),
  owner_id uuid not null references public.desk_members(user_id),
  name text not null check(length(name) between 1 and 100),
  starting_cash numeric not null check(starting_cash>0 and starting_cash<1000000000),
  cash numeric not null check(cash>=0),
  reserved_cash numeric not null default 0 check(reserved_cash>=0 and reserved_cash<=cash),
  mode text not null default 'manual' check(mode in ('manual','assisted','automatic')),
  entries_paused boolean not null default true,
  policy jsonb not null default '{}'::jsonb,
  policy_version bigint not null default 1,
  created_at timestamptz not null default now()
);
create index paper_accounts_owner on public.paper_accounts(owner_id);
alter table public.paper_accounts enable row level security;
revoke all on public.paper_accounts from anon,authenticated,service_role;
grant select on public.paper_accounts to authenticated;
grant all on public.paper_accounts to service_role;
create policy owner_read on public.paper_accounts for select to authenticated using(owner_id=(select auth.uid()));

create table public.paper_orders (
  order_id uuid primary key default gen_random_uuid(),
  account_id uuid not null references public.paper_accounts(account_id),
  command_key uuid not null,
  band_id uuid not null references public.bands(band_id),
  token_id text not null,
  side text not null check(side in ('YES','NO')),
  action text not null check(action in ('BUY','SELL')),
  origin text not null check(origin in ('manual','assisted','automatic')),
  signal_id bigint references public.signals(signal_id),
  strategy_id text,
  shares numeric not null check(shares>0 and shares<1000000000),
  limit_price numeric not null check(limit_price>0 and limit_price<1),
  cash_ceiling numeric not null check(cash_ceiling>=0 and cash_ceiling<1000000000),
  share_step numeric not null default 0.01 check(share_step>0),
  max_book_age_seconds integer not null default 120 check(max_book_age_seconds between 1 and 900),
  status text not null default 'queued' check(status in ('queued','working','filled','partial','rejected','expired','canceled')),
  reason text,
  context jsonb not null default '{}'::jsonb,
  policy_version bigint not null,
  requested_at timestamptz not null default now(),
  expires_at timestamptz not null,
  lease_token uuid,
  lease_until timestamptz,
  result jsonb,
  unique(account_id,command_key)
);
create index paper_orders_queue on public.paper_orders(status,requested_at);
create index paper_orders_band on public.paper_orders(band_id);
create index paper_orders_signal on public.paper_orders(signal_id);
alter table public.paper_orders enable row level security;
revoke all on public.paper_orders from anon,authenticated,service_role;
grant select on public.paper_orders to authenticated;
grant all on public.paper_orders to service_role;
create policy owner_read on public.paper_orders for select to authenticated using(exists(
  select 1 from public.paper_accounts a where a.account_id=paper_orders.account_id and a.owner_id=(select auth.uid())));

create table public.paper_activity (
  event_id bigint generated always as identity primary key,
  account_id uuid not null references public.paper_accounts(account_id),
  order_id uuid references public.paper_orders(order_id),
  event_type text not null,
  occurred_at timestamptz not null default clock_timestamp(),
  payload jsonb not null,
  cash_delta numeric not null default 0,
  unique(order_id,event_type)
);
create index paper_activity_account on public.paper_activity(account_id,event_id desc);
alter table public.paper_activity enable row level security;
revoke all on public.paper_activity from anon,authenticated,service_role;
grant select on public.paper_activity to authenticated;
grant select,insert on public.paper_activity to service_role;
grant usage on sequence public.paper_activity_event_id_seq to service_role;
create policy owner_read on public.paper_activity for select to authenticated using(exists(
  select 1 from public.paper_accounts a where a.account_id=paper_activity.account_id and a.owner_id=(select auth.uid())));
create trigger paper_activity_immutable before update or delete on public.paper_activity
  for each row execute function arbdesk_private.immutable_record();
create trigger paper_activity_no_truncate before truncate on public.paper_activity
  for each statement execute function arbdesk_private.immutable_record();

create function arbdesk_private.create_paper_account(p_name text,p_starting_cash numeric) returns uuid
language plpgsql security definer set search_path='' as $$
declare id uuid;
begin
  if auth.uid() is null or not exists(select 1 from public.desk_members where user_id=auth.uid()) then
    raise exception 'Desk owner sign-in required'; end if;
  insert into public.paper_accounts(owner_id,name,starting_cash,cash)
    values(auth.uid(),p_name,p_starting_cash,p_starting_cash) returning account_id into id;
  insert into public.paper_activity(account_id,event_type,payload,cash_delta)
    values(id,'account_opened',jsonb_build_object('starting_cash',p_starting_cash),p_starting_cash);
  return id;
end $$;
revoke all on function arbdesk_private.create_paper_account(text,numeric) from public,anon;
grant usage on schema arbdesk_private to authenticated;
grant execute on function arbdesk_private.create_paper_account(text,numeric) to authenticated;
create function public.create_paper_account(p_name text,p_starting_cash numeric) returns uuid
language sql security invoker set search_path='' as $$ select arbdesk_private.create_paper_account(p_name,p_starting_cash) $$;
revoke all on function public.create_paper_account(text,numeric) from public,anon;
grant execute on function public.create_paper_account(text,numeric) to authenticated;

create function arbdesk_private.submit_paper_order(p_account uuid,p_command uuid,p_band uuid,p_side text,
  p_shares numeric,p_limit numeric,p_cash_ceiling numeric,p_reason text default '') returns uuid
language plpgsql security definer set search_path='' as $$
declare a public.paper_accounts; b public.bands; m public.markets; id uuid;
begin
  select * into a from public.paper_accounts where account_id=p_account for update;
  if not found or auth.uid() is null or a.owner_id<>auth.uid() then raise exception 'Account access denied'; end if;
  select order_id into id from public.paper_orders where account_id=p_account and command_key=p_command;
  if found then return id; end if;
  select * into b from public.bands where band_id=p_band;
  if not found or p_side not in ('YES','NO') then raise exception 'Unknown band or side'; end if;
  select * into m from public.markets where market_id=b.market_id;
  if m.closed or m.resolution_date < (now() at time zone 'UTC')::date-1 then raise exception 'Market is closed or historical'; end if;
  if p_cash_ceiling> a.cash-a.reserved_cash or p_cash_ceiling<p_shares*p_limit then
    raise exception 'Insufficient available cash or fee-inclusive ceiling'; end if;
  insert into public.paper_orders(account_id,command_key,band_id,token_id,side,action,origin,shares,
    limit_price,cash_ceiling,reason,policy_version,expires_at,context)
  values(p_account,p_command,p_band,case when p_side='YES' then b.token_yes else b.token_no end,
    p_side,'BUY','manual',p_shares,p_limit,p_cash_ceiling,p_reason,a.policy_version,now()+interval '5 minutes',
    jsonb_build_object('market',to_jsonb(m),'band',to_jsonb(b),'actor',auth.uid())) returning order_id into id;
  update public.paper_accounts set reserved_cash=reserved_cash+p_cash_ceiling where account_id=p_account;
  insert into public.paper_activity(account_id,order_id,event_type,payload)
    values(p_account,id,'order_submitted',jsonb_build_object('origin','manual','reason',p_reason));
  return id;
end $$;
revoke all on function arbdesk_private.submit_paper_order(uuid,uuid,uuid,text,numeric,numeric,numeric,text) from public,anon;
grant usage on schema arbdesk_private to authenticated;
grant execute on function arbdesk_private.submit_paper_order(uuid,uuid,uuid,text,numeric,numeric,numeric,text) to authenticated;
create function public.submit_paper_order(p_account uuid,p_command uuid,p_band uuid,p_side text,
  p_shares numeric,p_limit numeric,p_cash_ceiling numeric,p_reason text default '') returns uuid
language sql security invoker set search_path='' as $$ select arbdesk_private.submit_paper_order(p_account,p_command,p_band,p_side,p_shares,p_limit,p_cash_ceiling,p_reason) $$;
revoke all on function public.submit_paper_order(uuid,uuid,uuid,text,numeric,numeric,numeric,text) from public,anon;
grant execute on function public.submit_paper_order(uuid,uuid,uuid,text,numeric,numeric,numeric,text) to authenticated;

create function arbdesk_private.cancel_paper_order(p_order uuid) returns boolean
language plpgsql security definer set search_path='' as $$
declare o public.paper_orders; a public.paper_accounts;
begin
  select * into o from public.paper_orders where order_id=p_order;
  select * into a from public.paper_accounts where account_id=o.account_id for update;
  if not found or auth.uid() is null or a.owner_id<>auth.uid() then raise exception 'Account access denied'; end if;
  select * into o from public.paper_orders where order_id=p_order for update;
  if o.status<>'queued' then return false; end if;
  update public.paper_orders set status='canceled' where order_id=p_order;
  update public.paper_accounts set reserved_cash=reserved_cash-o.cash_ceiling where account_id=o.account_id;
  insert into public.paper_activity(account_id,order_id,event_type,payload)
    values(o.account_id,p_order,'order_canceled','{}');
  return true;
end $$;
revoke all on function arbdesk_private.cancel_paper_order(uuid) from public,anon;
grant usage on schema arbdesk_private to authenticated;
grant execute on function arbdesk_private.cancel_paper_order(uuid) to authenticated;
create function public.cancel_paper_order(p_order uuid) returns boolean
language sql security invoker set search_path='' as $$ select arbdesk_private.cancel_paper_order(p_order) $$;
revoke all on function public.cancel_paper_order(uuid) from public,anon;
grant execute on function public.cancel_paper_order(uuid) to authenticated;
create table public.paper_positions (
  account_id uuid not null references public.paper_accounts(account_id),
  band_id uuid not null references public.bands(band_id),
  side text not null check(side in ('YES','NO')),
  shares numeric not null check(shares>=0),
  cost_basis numeric not null check(cost_basis>=0),
  realized_pnl numeric not null default 0,
  primary key(account_id,band_id,side)
);
create index paper_positions_band on public.paper_positions(band_id);
alter table public.paper_positions enable row level security;
revoke all on public.paper_positions from anon,authenticated,service_role;
grant select on public.paper_positions to authenticated;
grant all on public.paper_positions to service_role;
create policy owner_read on public.paper_positions for select to authenticated using(exists(
 select 1 from public.paper_accounts a where a.account_id=paper_positions.account_id and a.owner_id=(select auth.uid())));

create table public.paper_book_evidence (
  snapshot_id text primary key,
  token_id text not null,
  observed_at timestamptz not null,
  captured_at timestamptz not null default clock_timestamp(),
  payload jsonb not null
);
alter table public.paper_book_evidence enable row level security;
revoke all on public.paper_book_evidence from anon,authenticated,service_role;
grant select,insert on public.paper_book_evidence to service_role;
create trigger book_evidence_immutable before update or delete on public.paper_book_evidence
  for each row execute function arbdesk_private.immutable_record();
create trigger book_evidence_no_truncate before truncate on public.paper_book_evidence
  for each statement execute function arbdesk_private.immutable_record();

create function public.claim_paper_order() returns jsonb
language plpgsql security invoker set search_path='' as $$
declare o public.paper_orders; a public.paper_accounts;
begin
  -- Lock the account first in every command; only one active lease per account.
  select * into a from public.paper_accounts x where exists(
    select 1 from public.paper_orders q where q.account_id=x.account_id and
      (q.status='queued' or (q.status='working' and q.lease_until<now())))
    and not exists(select 1 from public.paper_orders w where w.account_id=x.account_id
      and w.status='working' and w.lease_until>=now())
    order by x.created_at for update skip locked limit 1;
  if not found then return null; end if;
  select * into o from public.paper_orders where account_id=a.account_id and
    (status='queued' or (status='working' and lease_until<now())) order by requested_at
    for update skip locked limit 1;
  if not found then return null; end if;
  update public.paper_orders set status='working',lease_token=gen_random_uuid(),lease_until=now()+interval '3 minutes'
    where order_id=o.order_id returning * into o;
  return to_jsonb(o);
end $$;
revoke all on function public.claim_paper_order() from public,anon,authenticated;
grant execute on function public.claim_paper_order() to service_role;

create function public.complete_paper_order(p_order uuid,p_lease uuid,p_result jsonb) returns jsonb
language plpgsql security invoker set search_path='' as $$
declare o public.paper_orders; a public.paper_accounts; pos public.paper_positions;
  q numeric; cost numeric; fee numeric; delta numeric; basis numeric; evidence public.paper_book_evidence;
begin
  select * into o from public.paper_orders where order_id=p_order;
  select * into a from public.paper_accounts where account_id=o.account_id for update;
  if not found then raise exception 'Unknown account/order'; end if;
  select * into o from public.paper_orders where order_id=p_order for update;
  if o.status in ('filled','partial','rejected','expired','canceled') then return o.result; end if;
  if o.lease_token is distinct from p_lease or o.lease_until<now() then raise exception 'Lease lost'; end if;
  if o.status<>'working' or p_lease is null then raise exception 'Active lease required'; end if;
  if p_result->>'status' is null or p_result->>'status' not in ('filled','partial','rejected','expired') then raise exception 'Invalid terminal status'; end if;
  q:=(p_result->>'shares')::numeric; cost:=(p_result->>'notional')::numeric; fee:=(p_result->>'fee')::numeric;
  if q is null or cost is null or fee is null or q<0 or q>o.shares or cost<0 or fee<0
    or q::text in ('NaN','Infinity','-Infinity') or cost::text in ('NaN','Infinity','-Infinity')
    or fee::text in ('NaN','Infinity','-Infinity') then raise exception 'Invalid amounts'; end if;
  if q>0 then
    if p_result->>'status' not in ('filled','partial') or now()>o.expires_at then raise exception 'Expired or invalid fill'; end if;
    if (p_result->>'status'='filled')<>(q=o.shares) then raise exception 'Fill status disagrees with quantity'; end if;
    if jsonb_typeof(p_result->'fills') is distinct from 'array' then raise exception 'Fill evidence required'; end if;
    if exists(select 1 from jsonb_array_elements(p_result->'fills') f where
      not coalesce((f->>'shares')::numeric>0 and (f->>'shares')::numeric<=o.shares
        and (f->>'price')::numeric>0 and (f->>'price')::numeric<1
        and (f->>'fee')::numeric>=0 and (f->>'fee')::numeric<=(f->>'notional')::numeric
        and (f->>'notional')::numeric=(f->>'shares')::numeric*(f->>'price')::numeric,false))
      then raise exception 'Invalid fill arithmetic'; end if;
    select * into evidence from public.paper_book_evidence where snapshot_id=p_result->>'snapshot_id';
    if not found or evidence.token_id<>o.token_id or evidence.observed_at>now()
      or evidence.observed_at<now()-make_interval(secs=>o.max_book_age_seconds) then raise exception 'Missing or stale book evidence'; end if;
    if abs((select coalesce(sum((f->>'shares')::numeric),0) from jsonb_array_elements(p_result->'fills') f)-q)>0.00000001
      or abs((select coalesce(sum((f->>'notional')::numeric),0) from jsonb_array_elements(p_result->'fills') f)-cost)>0.00000001
      or abs((select coalesce(sum((f->>'fee')::numeric),0) from jsonb_array_elements(p_result->'fills') f)-fee)>0.00000001
      then raise exception 'Fill totals disagree'; end if;
    if exists(select 1 from jsonb_array_elements(p_result->'fills') f
      where (f->>'shares')::numeric<=0 or (f->>'price')::numeric<=0 or (f->>'price')::numeric>=1
      or (o.action='BUY' and (f->>'price')::numeric>o.limit_price)
      or (o.action='SELL' and (f->>'price')::numeric<o.limit_price)) then raise exception 'Fill outside approved limit'; end if;
    if o.action='BUY' then
      delta:=-cost-fee;
      if -delta>o.cash_ceiling or -delta>a.cash then raise exception 'Cash ceiling exceeded'; end if;
      insert into public.paper_positions(account_id,band_id,side,shares,cost_basis)
        values(o.account_id,o.band_id,o.side,q,cost+fee)
      on conflict(account_id,band_id,side) do update set
        shares=paper_positions.shares+excluded.shares,cost_basis=paper_positions.cost_basis+excluded.cost_basis;
    else
      select * into pos from public.paper_positions where account_id=o.account_id and band_id=o.band_id and side=o.side for update;
      if not found or q>pos.shares then raise exception 'Insufficient shares'; end if;
      basis:=pos.cost_basis*q/pos.shares; delta:=cost-fee;
      update public.paper_positions set shares=shares-q,cost_basis=cost_basis-basis,realized_pnl=realized_pnl+delta-basis
        where account_id=o.account_id and band_id=o.band_id and side=o.side;
    end if;
  else
    if cost<>0 or fee<>0 or p_result->>'status' in ('filled','partial') then raise exception 'Zero fill has nonzero economic result'; end if;
    delta:=0;
  end if;
  update public.paper_accounts set cash=cash+delta,reserved_cash=reserved_cash-o.cash_ceiling where account_id=o.account_id;
  update public.paper_orders set status=p_result->>'status',reason=p_result->>'reason',result=p_result,lease_until=null where order_id=p_order;
  insert into public.paper_activity(account_id,order_id,event_type,payload,cash_delta)
    values(o.account_id,p_order,'execution_completed',p_result,delta);
  return p_result;
end $$;
revoke all on function public.complete_paper_order(uuid,uuid,jsonb) from public,anon,authenticated;
grant execute on function public.complete_paper_order(uuid,uuid,jsonb) to service_role;
commit;
