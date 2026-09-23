-- A RETIRED DESK STAYS RETIRED (plan v2, step P0.3).
--
-- Hassan decided on 23 Sep to retire every existing paper desk and rebuild
-- the engine (plan v2 P5). Retiring deletes nothing: every order, plan,
-- position, settlement, trade and cash movement stays where it is, as
-- history. This migration adds the state and the guards. The retirement
-- itself is done by tools/retire_desks.py, which exports each desk's history
-- to the repository first and then calls paper_desk_retire() once per desk.
--
-- Before this, the only "off" a desk had was archived_at, and it was a
-- two-way door: paper_desk_archive(id, false) brought a desk straight back,
-- and nothing on the order path read archived_at at all (queue_plan checks
-- mode and entries_paused, never archived_at). Retirement is one-way:
--
--   1. paper_accounts gains status / retired_at / retired_reason.
--   2. paper_desk_retire() is the only way in. It refuses a desk that still
--      has shares, a live order or an open plan, so nothing can be stranded
--      half-traded, and it also sets archived_at, so every reader that already
--      hides archived desks (the board's desk list, v_strategy_desk_board,
--      the risk budget, the signal engine) hides retired ones too, with no
--      second mechanism.
--   3. A retired row is frozen: any change other than its name is refused.
--      That covers un-archiving, un-pausing, a reset, a re-policy and a cash
--      movement, however it arrives.
--   4. No order of any kind can be inserted for a retired desk (a trigger on
--      paper_orders, so every writer is covered, not just queue_plan), and
--      queue_plan refuses first with a plain message.
--   5. strategy_config_history records every change to a strategy's switch
--      and settings, because P0.3 disables s1-s9 and the plan (P5.2) moves
--      the switch into lifecycle states.
--
-- Idempotent: every statement is guarded, so re-running it is a no-op.

-- 1. The state -----------------------------------------------------------------
-- archived_at is created by sql/ad4_59_paper_desks.sql, which the PGlite
-- harness applies after the migrations. Adding it here too (same type, same
-- `if not exists`) lets this file stand on its own.
alter table public.paper_accounts add column if not exists archived_at timestamptz;
alter table public.paper_accounts add column if not exists status text not null default 'active';
alter table public.paper_accounts add column if not exists retired_at timestamptz;
alter table public.paper_accounts add column if not exists retired_reason text;

do $$
begin
  if not exists (select 1 from pg_constraint
                  where conrelid = 'public.paper_accounts'::regclass
                    and conname = 'paper_accounts_status_check') then
    alter table public.paper_accounts add constraint paper_accounts_status_check
      check (status in ('active', 'suspended', 'retired'));
  end if;
  if not exists (select 1 from pg_constraint
                  where conrelid = 'public.paper_accounts'::regclass
                    and conname = 'paper_accounts_retired_is_stamped') then
    alter table public.paper_accounts add constraint paper_accounts_retired_is_stamped
      check (status <> 'retired' or (retired_at is not null and retired_reason is not null
                                      and archived_at is not null and entries_paused));
  end if;
end $$;

-- 2. The only way in ------------------------------------------------------------
create or replace function public.paper_desk_retire(p_account_id uuid, p_reason text)
returns jsonb
language plpgsql security definer set search_path = '' as $$
declare a public.paper_accounts; v_shares int; v_orders int; v_plans int;
begin
  select * into a from public.paper_accounts where account_id = p_account_id for update;
  if not found then raise exception 'no such desk: %', p_account_id; end if;
  if a.status = 'retired' then
    return jsonb_build_object('account_id', a.account_id, 'status', 'retired',
                              'retired_at', a.retired_at, 'already', true);
  end if;
  if p_reason is null or length(btrim(p_reason)) < 1 then
    raise exception 'a retirement needs a reason';
  end if;

  select count(*) into v_shares from public.paper_positions
   where account_id = p_account_id and shares > 0;
  select count(*) into v_orders from public.paper_orders
   where account_id = p_account_id and status in ('queued', 'working');
  select count(*) into v_plans from public.paper_trade_plans
   where account_id = p_account_id and status in ('pending_approval', 'queued');
  if v_shares > 0 or v_orders > 0 or v_plans > 0 then
    raise exception 'desk % still has % open position(s), % live order(s) and % open plan(s); '
                    'settle or cancel them before retiring it', p_account_id, v_shares, v_orders, v_plans;
  end if;

  update public.paper_accounts
     set status = 'retired', retired_at = now(), retired_reason = btrim(p_reason),
         archived_at = coalesce(archived_at, now()), entries_paused = true
   where account_id = p_account_id;

  insert into public.paper_activity (account_id, event_type, payload)
  values (p_account_id, 'account_retired',
          jsonb_build_object('reason', btrim(p_reason), 'cash', a.cash,
                             'starting_cash', a.starting_cash, 'mode', a.mode));

  return jsonb_build_object('account_id', p_account_id, 'status', 'retired',
                            'cash', a.cash, 'already', false);
end $$;
revoke all on function public.paper_desk_retire(uuid, text) from public, anon, authenticated;
grant execute on function public.paper_desk_retire(uuid, text) to service_role;

-- 3. A retired row is frozen -----------------------------------------------------
create or replace function arbdesk_private.paper_account_retired_is_frozen()
returns trigger language plpgsql set search_path = '' as $$
begin
  if old.status = 'retired' and (
       new.status, new.retired_at, new.retired_reason, new.archived_at, new.entries_paused,
       new.mode, new.policy, new.policy_version, new.cash, new.reserved_cash, new.starting_cash,
       new.owner_id, new.access_mode, new.parent_account_id)
     is distinct from (
       old.status, old.retired_at, old.retired_reason, old.archived_at, old.entries_paused,
       old.mode, old.policy, old.policy_version, old.cash, old.reserved_cash, old.starting_cash,
       old.owner_id, old.access_mode, old.parent_account_id) then
    raise exception 'desk % is retired: it is kept as history and cannot change (only its name can)',
      old.account_id;
  end if;
  return new;
end $$;
drop trigger if exists paper_account_retired_is_frozen on public.paper_accounts;
create trigger paper_account_retired_is_frozen
  before update on public.paper_accounts
  for each row execute function arbdesk_private.paper_account_retired_is_frozen();

-- 4. No order for a retired desk, whoever writes it --------------------------------
create or replace function arbdesk_private.paper_order_desk_not_retired()
returns trigger language plpgsql set search_path = '' as $$
begin
  if exists (select 1 from public.paper_accounts
              where account_id = new.account_id and status = 'retired') then
    raise exception 'Desk retired: desk % takes no orders', new.account_id;
  end if;
  return new;
end $$;
drop trigger if exists paper_order_desk_not_retired on public.paper_orders;
create trigger paper_order_desk_not_retired
  before insert on public.paper_orders
  for each row execute function arbdesk_private.paper_order_desk_not_retired();

-- queue_plan: identical to the definition in
-- 20260912083728_paper_approvals_exits_and_policies.sql (checked against the
-- live function on 23 Sep), with one added line: the retired check, placed
-- straight after the plan and account are locked.
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

-- 5. Every change to a strategy's switch or settings is recorded -------------------
-- strategies is created by sql/ad4_rpc.sql; the PGlite harness builds it in
-- its fixture, matching the live columns.
create table if not exists public.strategy_config_history (
  history_id bigint generated always as identity primary key,
  strategy_id text not null,
  changed_at timestamptz not null default now(),
  changed_by text not null default current_user,
  operation text not null check (operation in ('INSERT', 'UPDATE')),
  -- why: set with `set local arbdesk.change_reason = '...'` in the same
  -- transaction; null when the writer gave none.
  reason text,
  old_row jsonb,
  new_row jsonb not null
);
create index if not exists strategy_config_history_strategy
  on public.strategy_config_history (strategy_id, changed_at);
alter table public.strategy_config_history enable row level security;
revoke all on public.strategy_config_history from public, anon, authenticated, service_role;
grant select on public.strategy_config_history to anon, authenticated, service_role;
drop policy if exists anon_read on public.strategy_config_history;
create policy anon_read on public.strategy_config_history for select to anon, authenticated using (true);

create or replace function arbdesk_private.record_strategy_change()
returns trigger language plpgsql security definer set search_path = '' as $$
begin
  if tg_op = 'UPDATE' and to_jsonb(new) = to_jsonb(old) then return new; end if;
  insert into public.strategy_config_history (strategy_id, operation, reason, old_row, new_row, changed_by)
  values (new.strategy_id, tg_op, nullif(current_setting('arbdesk.change_reason', true), ''),
          case when tg_op = 'UPDATE' then to_jsonb(old) end, to_jsonb(new), session_user);
  return new;
end $$;
revoke all on function arbdesk_private.record_strategy_change() from public, anon, authenticated;
drop trigger if exists strategy_config_history_record on public.strategies;
create trigger strategy_config_history_record
  after insert or update on public.strategies
  for each row execute function arbdesk_private.record_strategy_change();

-- The switch, with its reason, in one call. `set local` needs a transaction,
-- and a PostgREST PATCH gives the caller none it can put a setting into, so a
-- script that wants its reason on record calls this instead.
create or replace function public.set_strategies_enabled(p_ids text[], p_enabled boolean, p_reason text)
returns integer
language plpgsql security definer set search_path = '' as $$
declare n integer;
begin
  if p_reason is null or length(btrim(p_reason)) < 1 then
    raise exception 'a change to a strategy switch needs a reason';
  end if;
  perform set_config('arbdesk.change_reason', btrim(p_reason), true);
  update public.strategies set enabled = p_enabled
   where strategy_id = any(p_ids) and enabled is distinct from p_enabled;
  get diagnostics n = row_count;
  return n;
end $$;
revoke all on function public.set_strategies_enabled(text[], boolean, text) from public, anon, authenticated;
grant execute on function public.set_strategies_enabled(text[], boolean, text) to service_role;
