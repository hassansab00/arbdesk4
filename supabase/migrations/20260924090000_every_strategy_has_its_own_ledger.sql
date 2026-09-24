-- EVERY STRATEGY HAS ITS OWN LEDGER (plan v2, step P5.1).
--
-- The account model the rest of phase 5 is built on. Two kinds of account:
--
--   shadow     One per strategy, opened automatically when the strategy is
--              registered. It trades only that strategy's decisions, at a
--              standard notional (settings 'shadow_ledger', default $1,000),
--              and nothing another strategy does can limit it. Paper capital
--              is free, so starving a strategy of capital only starves it of
--              evidence. Plan rule 6: shadow needs nobody's approval.
--
--   portfolio  One account, where the meta-allocator (P5.10) will split
--              capital across strategies that have earned it. It is opened
--              SUSPENDED, with no bankroll: putting capital on it is
--              Hassan's decision at the P5.10 gate (rule 6), and a
--              constraint below refuses to make it active until that
--              bankroll is written.
--
-- The four desks that existed before this are retired (P0.3) and keep a NULL
-- kind: they belong to neither model, and a retired row is frozen anyway.
-- paper_desk_create still makes a plain desk with a NULL kind, as before.
--
-- What changes on the order path:
--   1. queue_plan refuses a suspended desk the way it refuses a retired one.
--   2. queue_plan refuses a shadow ledger any strategy but its own, even if
--      someone edits its policy: the ledger IS that strategy's evidence.
--   3. No BUY order can be inserted for a suspended desk, whoever writes it
--      (the same trigger that already refuses every order for a retired one).
--      Exits still go through, so a suspended desk can always get out.
-- Settlement is untouched and applies to both kinds.
--
-- queue_plan and paper_order_desk_not_retired below are the live definitions
-- (md5 of prosrc matched 20260924050000 and 20260923100000 on 24 Sep) with
-- the added lines marked P5.1.
--
-- Idempotent: every statement is guarded, and the ledgers are opened by a
-- function that returns the existing one.

-- 1. The columns -----------------------------------------------------------------
-- 20260912151318 allowed one single_desk account; sql/ad4_60_multiple_paper_desks.sql
-- dropped that index in production long ago (absent from pg_indexes, 24 Sep).
-- The PGlite harness applies the sql/ files after the migrations, so there it
-- still stands here and would refuse the second ledger. Same statement as ad4_60.
drop index if exists public.one_single_paper_desk;

alter table public.paper_accounts add column if not exists kind text;
alter table public.paper_accounts add column if not exists strategy_id text;
alter table public.paper_accounts add column if not exists bankroll_usd numeric;

do $$
begin
  if not exists (select 1 from pg_constraint where conrelid = 'public.paper_accounts'::regclass
                    and conname = 'paper_accounts_kind_check') then
    alter table public.paper_accounts add constraint paper_accounts_kind_check
      check (kind is null or kind in ('shadow', 'portfolio'));
  end if;
  -- A strategy belongs to a shadow ledger and to nothing else, and a shadow
  -- ledger without one would be evidence for nothing.
  if not exists (select 1 from pg_constraint where conrelid = 'public.paper_accounts'::regclass
                    and conname = 'paper_accounts_shadow_has_a_strategy') then
    alter table public.paper_accounts add constraint paper_accounts_shadow_has_a_strategy
      check ((kind is not distinct from 'shadow') = (strategy_id is not null));
  end if;
  if not exists (select 1 from pg_constraint where conrelid = 'public.paper_accounts'::regclass
                    and conname = 'paper_accounts_bankroll_check') then
    alter table public.paper_accounts add constraint paper_accounts_bankroll_check
      check (bankroll_usd is null or (bankroll_usd > 0 and bankroll_usd < 1000000000));
  end if;
  if not exists (select 1 from pg_constraint where conrelid = 'public.paper_accounts'::regclass
                    and conname = 'paper_accounts_shadow_has_a_notional') then
    alter table public.paper_accounts add constraint paper_accounts_shadow_has_a_notional
      check (kind is distinct from 'shadow' or bankroll_usd is not null);
  end if;
  -- THE GATE. The portfolio account cannot be made active until its bankroll
  -- is written, and writing it is Hassan's decision (P5.10, rule 6).
  if not exists (select 1 from pg_constraint where conrelid = 'public.paper_accounts'::regclass
                    and conname = 'paper_accounts_portfolio_needs_a_bankroll') then
    alter table public.paper_accounts add constraint paper_accounts_portfolio_needs_a_bankroll
      check (kind is distinct from 'portfolio' or status <> 'active' or bankroll_usd is not null);
  end if;
  if not exists (select 1 from pg_constraint where conrelid = 'public.paper_accounts'::regclass
                    and conname = 'paper_accounts_strategy_id_fkey') then
    alter table public.paper_accounts add constraint paper_accounts_strategy_id_fkey
      foreign key (strategy_id) references public.strategies(strategy_id);
  end if;
end $$;

-- One live ledger per strategy, and one live portfolio account. A retired one
-- is history and does not count, so a strategy can be given a fresh ledger.
create unique index if not exists paper_accounts_one_shadow_per_strategy
  on public.paper_accounts (strategy_id) where kind = 'shadow' and status <> 'retired';
create unique index if not exists paper_accounts_one_portfolio
  on public.paper_accounts (kind) where kind = 'portfolio' and status <> 'retired';

-- A ledger's kind and strategy never change. Re-pointing a shadow ledger at
-- another strategy would file one strategy's fills as another's evidence.
create or replace function arbdesk_private.paper_account_kind_is_fixed()
returns trigger language plpgsql set search_path = '' as $$
begin
  if (new.kind, new.strategy_id) is distinct from (old.kind, old.strategy_id) then
    raise exception 'desk %: an account''s kind and strategy are fixed when it is opened', old.account_id;
  end if;
  return new;
end $$;
drop trigger if exists paper_account_kind_is_fixed on public.paper_accounts;
create trigger paper_account_kind_is_fixed
  before update on public.paper_accounts
  for each row execute function arbdesk_private.paper_account_kind_is_fixed();

-- 2. The standard notional, a setting ----------------------------------------------
-- The policy is what every existing automatic desk ran with (live, 24 Sep),
-- minus the caps, which are the notional itself: the strategy's own sizing
-- decides how much of it a decision uses. min_edge 0 means queue_plan still
-- refuses a plan whose verified net edge is negative.
insert into public.settings (key, value)
values ('shadow_ledger', jsonb_build_object(
  'notional_usd', 1000,
  'policy', jsonb_build_object('cities', jsonb_build_array('ALL'), 'min_edge', 0,
                               'auto_exit_enabled', true, 'stop_loss_fraction', 0.15,
                               'take_profit_fraction', 0.25),
  'note', 'plan v2 P5.1: every shadow ledger opens with notional_usd as its cash and its caps'))
on conflict (key) do nothing;

-- 3. Opening a ledger ------------------------------------------------------------
create or replace function arbdesk_private.open_shadow_ledger(p_strategy_id text)
returns uuid language plpgsql security definer set search_path = '' as $$
declare v_id uuid; v_cfg jsonb; v_notional numeric; v_policy jsonb;
begin
  select account_id into v_id from public.paper_accounts
   where kind = 'shadow' and strategy_id = p_strategy_id and status <> 'retired';
  if found then return v_id; end if;

  select value into v_cfg from public.settings where key = 'shadow_ledger';
  v_notional := coalesce((v_cfg->>'notional_usd')::numeric, 1000);
  v_policy := coalesce(v_cfg->'policy', '{}'::jsonb)
           || jsonb_build_object('strategies', jsonb_build_array(p_strategy_id),
                                 'max_plan_usd', v_notional, 'max_exposure_usd', v_notional);

  -- Automatic and not paused: the ledger acts on every decision its strategy
  -- makes. Whether the strategy makes any is the strategy's switch
  -- (strategies.enabled, P5.2's lifecycle state), not the ledger's.
  insert into public.paper_accounts (name, kind, strategy_id, bankroll_usd, starting_cash, cash,
                                     reserved_cash, mode, entries_paused, policy, policy_version,
                                     access_mode, owner_id, status)
  values (left('Shadow: ' || p_strategy_id, 100), 'shadow', p_strategy_id, v_notional,
          v_notional, v_notional, 0, 'automatic', false, v_policy, 1, 'single_desk', null, 'active')
  returning account_id into v_id;

  insert into public.paper_activity (account_id, event_type, payload, cash_delta)
  values (v_id, 'account_opened',
          jsonb_build_object('kind', 'shadow', 'strategy_id', p_strategy_id,
                             'starting_cash', v_notional, 'plan_step', 'P5.1'),
          v_notional);
  return v_id;
end $$;
revoke all on function arbdesk_private.open_shadow_ledger(text) from public;
grant execute on function arbdesk_private.open_shadow_ledger(text) to service_role;

-- Registered means opened. 'system' is not a strategy: it is the name the
-- health and anomaly ALERTs are filed under, and it never trades.
create or replace function arbdesk_private.strategy_opens_its_ledger()
returns trigger language plpgsql security definer set search_path = '' as $$
begin
  if new.strategy_id <> 'system' then
    perform arbdesk_private.open_shadow_ledger(new.strategy_id);
  end if;
  return null;
end $$;
drop trigger if exists strategy_opens_its_ledger on public.strategies;
create trigger strategy_opens_its_ledger
  after insert on public.strategies
  for each row execute function arbdesk_private.strategy_opens_its_ledger();

-- Every strategy registered before this migration.
do $$
declare r record;
begin
  for r in select strategy_id from public.strategies where strategy_id <> 'system' order by strategy_id loop
    perform arbdesk_private.open_shadow_ledger(r.strategy_id);
  end loop;
end $$;

-- 4. The portfolio account, suspended ----------------------------------------------
-- starting_cash has to be above zero (paper_accounts_starting_cash_check), so
-- it opens holding the standard notional as a placeholder. It is not a
-- bankroll: bankroll_usd stays NULL, the account is suspended, manual and
-- paused, and no reader that trades (paper_plans, signal_engine, paper_exits)
-- selects a manual or suspended desk. P5.10 writes the bankroll and resets
-- the cash to it when Hassan activates it.
do $$
declare v_id uuid; v_notional numeric;
begin
  if exists (select 1 from public.paper_accounts where kind = 'portfolio' and status <> 'retired') then
    return;
  end if;
  select coalesce((value->>'notional_usd')::numeric, 1000) into v_notional
    from public.settings where key = 'shadow_ledger';
  v_notional := coalesce(v_notional, 1000);
  insert into public.paper_accounts (name, kind, strategy_id, bankroll_usd, starting_cash, cash,
                                     reserved_cash, mode, entries_paused, policy, policy_version,
                                     access_mode, owner_id, status)
  values ('Portfolio', 'portfolio', null, null, v_notional, v_notional, 0, 'manual', true,
          '{}'::jsonb, 1, 'single_desk', null, 'suspended')
  returning account_id into v_id;
  insert into public.paper_activity (account_id, event_type, payload, cash_delta)
  values (v_id, 'account_opened',
          jsonb_build_object('kind', 'portfolio', 'status', 'suspended', 'plan_step', 'P5.1',
                             'note', 'cash is a placeholder; the bankroll is set at P5.10'),
          v_notional);
end $$;

-- 5. The order path ----------------------------------------------------------------
create or replace function arbdesk_private.queue_plan(p_plan uuid,p_origin text) returns uuid
language plpgsql set search_path='' as $$
declare plan public.paper_trade_plans; a public.paper_accounts; leg jsonb; b public.bands;
  total numeric; exposure numeric; id uuid;
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

create or replace function arbdesk_private.paper_order_desk_not_retired()
returns trigger language plpgsql set search_path = '' as $$
begin
  if exists (select 1 from public.paper_accounts
              where account_id = new.account_id and status = 'retired') then
    raise exception 'Desk retired: desk % takes no orders', new.account_id;
  end if;
  -- P5.1: a suspended desk may still sell what it holds, never buy.
  if new.action = 'BUY' and exists (select 1 from public.paper_accounts
              where account_id = new.account_id and status = 'suspended') then
    raise exception 'Desk suspended: desk % takes no new entries', new.account_id;
  end if;
  return new;
end $$;

-- 6. The single-desk bootstrap never hands out an engine ledger ----------------------
-- create_single_paper_account answers the oldest single_desk account. The
-- ledgers are single_desk rows too, and on a fresh database they exist before
-- anyone bootstraps a desk, so it would hand the s1 ledger to the manual order
-- and policy screens. Live it still answers the retired "Main paper account"
-- (the oldest row, kind NULL), so this changes nothing there. The live body
-- was the ad4_60 logic, compacted; this is that logic plus `and kind is null`,
-- the same change made to sql/ad4_60_multiple_paper_desks.sql.
create or replace function public.create_single_paper_account(
  p_name text, p_starting_cash numeric
) returns uuid
language plpgsql security invoker set search_path = '' as $ad4$
declare id uuid;
begin
  select account_id into id from public.paper_accounts
   where access_mode = 'single_desk' and kind is null order by created_at limit 1;
  if found then return id; end if;
  insert into public.paper_accounts (owner_id, access_mode, name, starting_cash, cash)
  values (null, 'single_desk', p_name, p_starting_cash, p_starting_cash) returning account_id into id;
  insert into public.paper_activity (account_id, event_type, payload, cash_delta)
  values (id, 'account_opened',
          jsonb_build_object('starting_cash', p_starting_cash, 'access_mode', 'single_desk'), p_starting_cash);
  return id;
end $ad4$;
