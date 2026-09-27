-- ===========================================================================
-- THE ENGINE'S ORDERS REACH ITS SHADOW LEDGERS (plan v2 P5.12 part 3b)
--
-- Since part 3a the one engine decides in the hourly tick and writes each
-- decision to `decisions`, ordering nothing. Its BUYs now reach the
-- strategy's own shadow ledger, through the same gate every entry passes:
--
-- publish_engine_plan(account, decision, legs, evidence)
--     The engine's counterpart of publish_paper_plan. An engine decision is
--     not a `signals` row, and writing one would also offer it to the old
--     plan path, so the plan names the DECISION (evidence.decision_id,
--     signal_id null) and must find: a BUY decided in the last 15 minutes, by
--     the ledger's own strategy, on a shadow ledger, for a strategy that is
--     switched on (strategies.enabled follows strategy_state: shadow or
--     portfolio). One plan per decision and ledger (the command key is the
--     decision's). On an automatic ledger it calls arbdesk_private.queue_plan
--     - every rail, cap, policy and holding check - and a refusal leaves the
--     plan 'blocked' with the reason, as publish_paper_plan does.
--
-- claim_account_order(account)
--     claim_paper_order, for one ledger: the tick fills its own orders inside
--     the minute, before their 5-minute expiry, and must not spend it on
--     another desk's queue. The same lease: one working order per account.
--
-- AUTO EXITS OFF ON THE ENGINE'S LEDGERS. paper_exits sells on a 15% stop
-- or a 25% gain for every automatic account whose policy has
-- auto_exit_enabled; the six ledgers were registered with it on. The engine
-- holds what it bought until settlement unless its own rules sell (S10's
-- certainly lost and switch), and a stop the engine did not decide would put
-- a different strategy's exits into its evidence. The change is logged like
-- set_paper_policy's and bumps policy_version.
--
-- Nothing here switches a strategy on: that is set_strategy_state(id,
-- 'shadow', reason), after this is live and checked.
-- ===========================================================================

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
  if not found or d.action <> 'BUY' then raise exception 'An engine BUY decision is required'; end if;
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

create or replace function public.claim_account_order(p_account uuid)
returns jsonb
language plpgsql
set search_path = ''
as $$
declare o public.paper_orders; a public.paper_accounts;
begin
  -- As claim_paper_order: the account first, one active lease per account.
  select * into a from public.paper_accounts x where x.account_id = p_account
    and not exists(select 1 from public.paper_orders w where w.account_id = x.account_id
                   and w.status = 'working' and w.lease_until >= now())
    for update skip locked;
  if not found then return null; end if;
  select * into o from public.paper_orders where account_id = a.account_id and
    (status = 'queued' or (status = 'working' and lease_until < now())) order by requested_at
    for update skip locked limit 1;
  if not found then return null; end if;
  update public.paper_orders set status = 'working', lease_token = gen_random_uuid(), lease_until = now() + interval '3 minutes'
    where order_id = o.order_id returning * into o;
  return to_jsonb(o);
end $$;

revoke all on function public.claim_account_order(uuid) from public, anon, authenticated;
grant execute on function public.claim_account_order(uuid) to service_role;

with changed as (
  update public.paper_accounts
     set policy = policy || '{"auto_exit_enabled": false}'::jsonb, policy_version = policy_version + 1
   where kind = 'shadow'
     and strategy_id in ('s10_winner', 's10_growth', 's10_lock', 's11_ladder', 's11_lock', 's12_no')
     and coalesce((policy->>'auto_exit_enabled')::boolean, false)
  returning account_id)
insert into public.paper_activity(account_id, event_type, payload)
select account_id, 'policy_changed',
       jsonb_build_object('auto_exit_enabled', false,
                          'why', 'plan v2 P5.12 part 3b: the engine exits only by its own rules')
  from changed;

-- A POSITION NAMES THE DECISION THAT OPENED IT (P5.0 item 4, P5.11).
-- paper_positions.entry_decision_id was declared uuid on 24 Sep, "NULL until
-- P5.11 creates the decisions table"; decisions.decision_id is a bigint.
-- Retyped only while it is still uuid and empty (27 Sep: 0 of 143 rows set,
-- no view depends on it), so a re-run is a no-op.
do $$
begin
  if exists (select 1 from information_schema.columns where table_schema = 'public'
               and table_name = 'paper_positions' and column_name = 'entry_decision_id' and data_type = 'uuid')
     and not exists (select 1 from public.paper_positions where entry_decision_id is not null) then
    alter table public.paper_positions alter column entry_decision_id type bigint using null;
  end if;
end $$;

comment on column public.paper_positions.entry_decision_id is
  'The decisions row whose BUY opened this position (plan v2 P5.11, P5.12 part 3b). NULL for entries made on the signal path.';

-- The entry trigger fills it for an engine order: the plan's evidence (copied
-- onto each order as context by queue_plan) carries decision_id and, per leg,
-- the probability the engine believed. Every other order is recorded exactly
-- as before.
create or replace function arbdesk_private.position_records_its_entry()
returns trigger language plpgsql security definer set search_path = '' as $$
declare filled numeric; v_decision bigint; v_leg jsonb;
begin
  if new.action <> 'BUY' or new.status not in ('filled', 'partial')
     or old.status is not distinct from new.status then
    return new;
  end if;
  filled := coalesce((new.result->>'shares')::numeric, 0);
  if filled <= 0 then
    return new;
  end if;
  v_decision := nullif(new.context->>'decision_id', '')::bigint;
  if v_decision is not null then
    v_leg := new.context->'legs_p'->(new.band_id::text || ':' || new.side);
    update public.paper_positions p
       set strategy_id = new.strategy_id,
           ledger_id = new.account_id,
           entry_order_id = new.order_id,
           group_id = new.plan_id,
           p_at_entry = nullif(v_leg->>'p', '')::numeric,
           entered_at = now(),
           entry_decision_id = v_decision,
           p_cons_at_entry = null,
           params_version = (select d.params_version from public.decisions d where d.decision_id = v_decision)
     where p.account_id = new.account_id and p.band_id = new.band_id and p.side = new.side
       and p.shares = filled;
    return new;
  end if;
  update public.paper_positions p
     set strategy_id = new.strategy_id,
         ledger_id = new.account_id,
         entry_order_id = new.order_id,
         group_id = new.plan_id,
         p_at_entry = (select s.prob_at_fire from public.signals s where s.signal_id = new.signal_id),
         entered_at = now(),
         entry_decision_id = null, p_cons_at_entry = null, params_version = null
   where p.account_id = new.account_id and p.band_id = new.band_id and p.side = new.side
     and p.shares = filled;          -- the position is exactly this fill: a new entry
  return new;
end $$;
