-- STRATEGY LIFECYCLE STATES (plan v2, step P5.2).
--
-- Every strategy is in exactly one state:
--
--   research    replay only; no live decisions
--   shadow      live decisions on its own shadow ledger (P5.1). Free (rule 6)
--   portfolio   also eligible for the portfolio account's allocation (P5.10)
--   suspended   no live decisions; re-tested automatically after 14 days
--   retired     kept as history; terminal
--
-- strategies.enabled stays, DERIVED from the state (shadow or portfolio), so
-- the UI and every reader that already filters on it keep working. The two are
-- kept in step both ways: a change of state sets enabled, and the older paths
-- that flip enabled directly (set_strategy_enabled, set_strategies_enabled, an
-- UPDATE) move the state - on to shadow, off to research - through the same
-- transition rules, so nothing can put the two out of step.
--
-- The transition rules are enforced here, not trusted to callers:
--
--   research  -> shadow, retired
--   shadow    -> research, suspended, portfolio*, retired
--   suspended -> shadow, research, retired
--   portfolio -> shadow, suspended, research, retired
--   retired   -> (nothing)
--
--   * only through promote_strategy_to_portfolio(), which requires the name
--     of who approved it and a reason. Entering the portfolio is Hassan's
--     decision (plan v2 P5.2, rule 6); a direct update is refused.
--
-- The AUTOMATIC transitions (shadow -> suspended on a losing record,
-- suspended -> shadow after 14 days) are made nightly by
-- scripts/strategy_lifecycle.py through set_strategy_state().
--
-- Every change is written to strategy_state_history, append-only.
--
-- Idempotent: tables and constraints are guarded, functions replaced, and the
-- backfill inserts only strategies that have no state yet.

create table if not exists public.strategy_state (
  strategy_id text primary key references public.strategies(strategy_id),
  state       text not null check (state in ('research', 'shadow', 'portfolio', 'suspended', 'retired')),
  since       timestamptz not null default now(),
  reason      text not null check (length(btrim(reason)) > 0),
  changed_by  text not null default session_user
);

create table if not exists public.strategy_state_history (
  history_id  bigint generated always as identity primary key,
  strategy_id text not null,
  from_state  text,
  to_state    text not null,
  reason      text not null,
  changed_by  text not null,
  changed_at  timestamptz not null default now()
);

alter table public.strategy_state enable row level security;
alter table public.strategy_state_history enable row level security;
revoke all on public.strategy_state, public.strategy_state_history from public, anon, authenticated;
grant select on public.strategy_state, public.strategy_state_history to anon, authenticated;
grant select, insert, update on public.strategy_state to service_role;
grant select on public.strategy_state_history to service_role;
do $$
begin
  if not exists (select 1 from pg_policy where polrelid = 'public.strategy_state'::regclass
                    and polname = 'strategy_state_read') then
    create policy strategy_state_read on public.strategy_state for select using (true);
  end if;
  if not exists (select 1 from pg_policy where polrelid = 'public.strategy_state_history'::regclass
                    and polname = 'strategy_state_history_read') then
    create policy strategy_state_history_read on public.strategy_state_history for select using (true);
  end if;
end $$;

-- 1. The rules, and the record ---------------------------------------------------
create or replace function arbdesk_private.strategy_state_guard()
returns trigger language plpgsql security definer set search_path = '' as $$
declare v_ok boolean;
begin
  if tg_op = 'UPDATE' and new.state = old.state then
    return new;
  end if;
  if tg_op = 'UPDATE' then
    v_ok := case old.state
      when 'research'  then new.state in ('shadow', 'retired')
      when 'shadow'    then new.state in ('research', 'suspended', 'portfolio', 'retired')
      when 'suspended' then new.state in ('shadow', 'research', 'retired')
      when 'portfolio' then new.state in ('shadow', 'suspended', 'research', 'retired')
      else false
    end;
    if not v_ok then
      raise exception 'strategy %: % -> % is not an allowed transition', new.strategy_id, old.state, new.state;
    end if;
  end if;
  if new.state = 'portfolio' and (tg_op = 'INSERT' or old.state <> 'portfolio')
     and coalesce(current_setting('arbdesk.portfolio_approved_by', true), '') = '' then
    raise exception 'strategy %: entering the portfolio is Hassan''s decision - use promote_strategy_to_portfolio()',
      new.strategy_id;
  end if;
  new.since := now();
  new.changed_by := session_user;
  insert into public.strategy_state_history (strategy_id, from_state, to_state, reason, changed_by)
  values (new.strategy_id, case when tg_op = 'UPDATE' then old.state end, new.state, new.reason,
          coalesce(nullif(current_setting('arbdesk.portfolio_approved_by', true), ''), session_user));
  return new;
end $$;
drop trigger if exists strategy_state_guard on public.strategy_state;
create trigger strategy_state_guard
  before insert or update on public.strategy_state
  for each row execute function arbdesk_private.strategy_state_guard();

-- The history is append-only.
create or replace function arbdesk_private.strategy_state_history_is_append_only()
returns trigger language plpgsql set search_path = '' as $$
begin
  raise exception 'strategy_state_history is append-only';
end $$;
drop trigger if exists strategy_state_history_append_only on public.strategy_state_history;
create trigger strategy_state_history_append_only
  before update or delete on public.strategy_state_history
  for each row execute function arbdesk_private.strategy_state_history_is_append_only();

-- 2. enabled follows the state ------------------------------------------------------
create or replace function arbdesk_private.strategy_state_sets_enabled()
returns trigger language plpgsql security definer set search_path = '' as $$
begin
  perform set_config('arbdesk.change_reason', 'lifecycle: ' || new.state || ' - ' || new.reason, true);
  update public.strategies set enabled = (new.state in ('shadow', 'portfolio'))
   where strategy_id = new.strategy_id and enabled is distinct from (new.state in ('shadow', 'portfolio'));
  return null;
end $$;
drop trigger if exists strategy_state_sets_enabled on public.strategy_state;
create trigger strategy_state_sets_enabled
  after insert or update of state on public.strategy_state
  for each row execute function arbdesk_private.strategy_state_sets_enabled();

-- 3. ...and a direct flip of enabled moves the state -------------------------------
-- pg_trigger_depth() = 1 means the enabled change came from outside (a
-- function, a script, the UI's RPC), not from the trigger above - so this
-- never answers its own echo.
create or replace function arbdesk_private.strategy_enabled_moves_state()
returns trigger language plpgsql security definer set search_path = '' as $$
declare v_state text;
begin
  if pg_trigger_depth() > 1 or new.enabled is not distinct from old.enabled then
    return null;
  end if;
  select state into v_state from public.strategy_state where strategy_id = new.strategy_id;
  if not found then
    return null;
  end if;
  if new.enabled and v_state not in ('shadow', 'portfolio') then
    update public.strategy_state
       set state = 'shadow',
           reason = coalesce(nullif(current_setting('arbdesk.change_reason', true), ''), 'strategies.enabled switched on')
     where strategy_id = new.strategy_id;
  elsif not new.enabled and v_state in ('shadow', 'portfolio') then
    update public.strategy_state
       set state = 'research',
           reason = coalesce(nullif(current_setting('arbdesk.change_reason', true), ''), 'strategies.enabled switched off')
     where strategy_id = new.strategy_id;
  end if;
  return null;
end $$;
drop trigger if exists strategy_enabled_moves_state on public.strategies;
create trigger strategy_enabled_moves_state
  after update of enabled on public.strategies
  for each row execute function arbdesk_private.strategy_enabled_moves_state();

-- 4. A registered strategy gets a state, from its switch -----------------------------
-- 'system' is the alert channel, not a strategy (as in P5.1's ledger trigger).
create or replace function arbdesk_private.strategy_gets_a_state()
returns trigger language plpgsql security definer set search_path = '' as $$
begin
  if new.strategy_id <> 'system' then
    insert into public.strategy_state (strategy_id, state, reason)
    values (new.strategy_id, case when new.enabled then 'shadow' else 'research' end,
            'registered')
    on conflict (strategy_id) do nothing;
  end if;
  return null;
end $$;
drop trigger if exists strategy_gets_a_state on public.strategies;
create trigger strategy_gets_a_state
  after insert on public.strategies
  for each row execute function arbdesk_private.strategy_gets_a_state();

-- Every strategy registered before this migration: its state is what its
-- switch says today, so nothing changes behaviour.
insert into public.strategy_state (strategy_id, state, reason)
select s.strategy_id, case when s.enabled then 'shadow' else 'research' end,
       'plan v2 P5.2 backfill from strategies.enabled'
  from public.strategies s
 where s.strategy_id <> 'system'
on conflict (strategy_id) do nothing;

-- 5. The two ways to change a state -------------------------------------------------
create or replace function public.set_strategy_state(p_strategy_id text, p_state text, p_reason text)
returns jsonb language plpgsql security definer set search_path = '' as $$
declare v_old text;
begin
  if p_reason is null or length(btrim(p_reason)) < 1 then
    raise exception 'a change of state needs a reason';
  end if;
  if p_state = 'portfolio' then
    raise exception 'entering the portfolio is Hassan''s decision - use promote_strategy_to_portfolio()';
  end if;
  select state into v_old from public.strategy_state where strategy_id = p_strategy_id for update;
  if not found then
    raise exception 'no strategy %', p_strategy_id;
  end if;
  update public.strategy_state set state = p_state, reason = btrim(p_reason)
   where strategy_id = p_strategy_id;
  return jsonb_build_object('strategy_id', p_strategy_id, 'from', v_old, 'to', p_state);
end $$;
revoke all on function public.set_strategy_state(text, text, text) from public, anon, authenticated;
grant execute on function public.set_strategy_state(text, text, text) to service_role;

create or replace function public.promote_strategy_to_portfolio(p_strategy_id text, p_approved_by text, p_reason text)
returns jsonb language plpgsql security definer set search_path = '' as $$
declare v_old text;
begin
  if coalesce(btrim(p_approved_by), '') = '' or coalesce(btrim(p_reason), '') = '' then
    raise exception 'a promotion to the portfolio names who approved it and why';
  end if;
  select state into v_old from public.strategy_state where strategy_id = p_strategy_id for update;
  if not found then
    raise exception 'no strategy %', p_strategy_id;
  end if;
  perform set_config('arbdesk.portfolio_approved_by', btrim(p_approved_by), true);
  update public.strategy_state set state = 'portfolio', reason = btrim(p_reason)
   where strategy_id = p_strategy_id;
  perform set_config('arbdesk.portfolio_approved_by', '', true);
  return jsonb_build_object('strategy_id', p_strategy_id, 'from', v_old, 'to', 'portfolio',
                            'approved_by', btrim(p_approved_by));
end $$;
revoke all on function public.promote_strategy_to_portfolio(text, text, text) from public, anon, authenticated;
grant execute on function public.promote_strategy_to_portfolio(text, text, text) to service_role;
