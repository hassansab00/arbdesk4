-- ===========================================================================
-- ad4_59_paper_desks.sql - SEVERAL PAPER DESKS, AND A BROWSER THAT CAN SEE THEM.
--
-- Safe to run any time. Adds two nullable columns, one read policy per paper
-- table, four RPCs and one view. Deletes nothing.
--
--
-- WHY THE PAPER SECTION WAS EMPTY, AND IT WAS NOT THE SWITCHES
--
-- The desk has exactly one account - "Main paper account", access_mode
-- 'single_desk', owner_id NULL. The only SELECT policy on paper_accounts is
--
--     owner_read:  owner_id = auth.uid()
--
-- and NULL = anything is never true, so the browser reads zero accounts.
-- Measured by becoming the role: `set local role authenticated; select
-- count(*) from paper_accounts` returns 0. Every child table inherits the
-- same test through an EXISTS on the parent, so positions, orders and
-- activity are invisible too.
--
-- That is upstream of entries_paused, of the strategies being disabled, and
-- of the $100 balance. Unpausing all of it would have changed nothing on
-- screen, because there was no row to render.
--
-- single_desk is a deliberate mode - it means "this deployment is one desk,
-- there are no per-user owners". It just never got the matching policy. The
-- policies below say: a row owned by you, OR a row owned by nobody in a
-- single-desk deployment. Member accounts stay private exactly as before.
--
--
-- SEVERAL DESKS ARE BLOCKED BY A CONSTRAINT THIS FILE DOES NOT TOUCH
--
-- supabase/migrations/20260912151318_single_desk_paper_access.sql introduced
-- single-operator mode - it removed the Paper Trades sign-in and routed every
-- command through server-side functions, each of which hard-requires
-- access_mode='single_desk' and owner_id is null. It enforces that with
--
--     create unique index one_single_paper_desk
--       on paper_accounts(access_mode) where access_mode='single_desk';
--
-- exactly one such desk, ever. The other route, access_mode='member', needs
-- owner_id to reference desk_members, and desk_members holds zero rows and
-- needs real signed-in users - the thing single-operator mode exists to avoid.
--
-- So the database currently permits ONE paper account by design, and a second
-- paper_desk_create() fails on that index. Relaxing it is a design decision
-- about someone else's architecture, made a day before this file: it would
-- also make create_single_paper_account()'s "find the one desk" lookup
-- ambiguous. It is deliberately NOT done here. paper_desk_create() detects
-- the constraint and says so in a sentence rather than surfacing a bare
-- 23505 duplicate-key error.
--
-- Everything else in this file works on the desk that exists, and is what
-- makes the Paper page render at all.
--
--
-- SUB-ACCOUNTS
--
-- parent_account_id makes a desk a child of another, so a budget, a city
-- scope or a strategy set can be tried in isolation and compared against its
-- parent without disturbing it. It is a plain self reference - a child is a
-- full account with its own cash, positions and P&L, not a sub-ledger.
-- One level is enforced: a child cannot itself be a parent, because two desks
-- pointing at each other is a cycle nobody wants to debug at settlement.
--
-- The column and the checks are here and correct; they start being useful the
-- moment the index above is relaxed.
--
--
-- WRITES GO THROUGH RPCs, NOT THROUGH THE TABLE
--
-- Same rule ad4_rls sets for settings: the browser holds a key that must
-- never be able to UPDATE cash directly. Each function below is SECURITY
-- DEFINER, validates its arguments, and is the only granted write path.
-- ===========================================================================

do $ad4$
begin
  if to_regclass('public.paper_accounts') is null then
    raise exception 'ad4_59 needs paper_accounts - run sql/ad4_paper_engine_columns.sql first';
  end if;
end $ad4$;

-- --------------------------------------------------------------------------
-- 1. Columns.
-- --------------------------------------------------------------------------
alter table public.paper_accounts
  add column if not exists parent_account_id uuid references public.paper_accounts(account_id),
  add column if not exists archived_at timestamptz;

create index if not exists ad4_ix_paper_accounts_parent
  on public.paper_accounts (parent_account_id) where parent_account_id is not null;

-- --------------------------------------------------------------------------
-- 2. Let a single-desk deployment read its own desks.
--
--    Additive: the existing owner_read policies are untouched, and PostgreSQL
--    ORs permissive policies together. A member account (owner_id set) is
--    still readable only by its owner.
-- --------------------------------------------------------------------------
do $ad4$
declare t text; parent_col text;
begin
  foreach t in array array['paper_accounts', 'paper_positions', 'paper_orders',
                           'paper_activity', 'paper_trade_plans']
  loop
    if to_regclass('public.' || t) is null then
      raise notice 'ad4_59: no % - skipped', t; continue;
    end if;
    execute format('alter table public.%I enable row level security', t);
    execute format('drop policy if exists single_desk_read on public.%I', t);

    if t = 'paper_accounts' then
      execute format($p$create policy single_desk_read on public.%I
                        for select to authenticated
                        using (owner_id is null and access_mode = 'single_desk')$p$, t);
    else
      execute format($p$create policy single_desk_read on public.%I
                        for select to authenticated
                        using (exists (select 1 from public.paper_accounts a
                                        where a.account_id = %I.account_id
                                          and a.owner_id is null
                                          and a.access_mode = 'single_desk'))$p$, t, t);
    end if;
  end loop;
end $ad4$;

-- --------------------------------------------------------------------------
-- 3. Create, update, reset, archive.
-- --------------------------------------------------------------------------
create or replace function paper_desk_create(
  p_name           text,
  p_starting_cash  numeric default 10000,
  p_mode           text    default 'manual',
  p_parent         uuid    default null,
  p_policy         jsonb   default '{}'::jsonb
) returns uuid language plpgsql security definer
set search_path = public as $ad4$
declare v_id uuid; v_parent_of_parent uuid;
begin
  p_name := btrim(coalesce(p_name, ''));
  if length(p_name) < 1 or length(p_name) > 100 then
    raise exception 'a desk needs a name between 1 and 100 characters';
  end if;
  if p_starting_cash is null or p_starting_cash <= 0 or p_starting_cash >= 1000000000 then
    raise exception 'starting cash must be above 0 and below 1,000,000,000 (got %)', p_starting_cash;
  end if;
  if p_mode not in ('manual', 'assisted', 'automatic') then
    raise exception 'mode must be manual, assisted or automatic (got %)', p_mode;
  end if;

  if p_parent is not null then
    select parent_account_id into strict v_parent_of_parent
      from paper_accounts where account_id = p_parent;
    if v_parent_of_parent is not null then
      raise exception 'sub-accounts are one level deep - % is itself a sub-account', p_parent;
    end if;
  end if;

  -- Say what is actually in the way. Without this the caller gets
  -- "duplicate key value violates unique constraint one_single_paper_desk",
  -- which does not tell anyone what to do about it.
  if exists (select 1 from pg_indexes
              where schemaname = 'public' and indexname = 'one_single_paper_desk')
     and exists (select 1 from paper_accounts where access_mode = 'single_desk') then
    raise exception 'this deployment allows exactly one paper desk. single-operator mode (supabase/migrations/20260912151318_single_desk_paper_access.sql) enforces it with the unique index one_single_paper_desk, and every paper command requires access_mode=single_desk. Allowing several desks means dropping that index and deciding what create_single_paper_account() should resolve to - a change to single-operator mode, not to this file.';
  end if;

  insert into paper_accounts (name, starting_cash, cash, reserved_cash, mode,
                              entries_paused, policy, policy_version,
                              access_mode, owner_id, parent_account_id)
  -- Paused on creation, always. A desk that starts trading the moment it is
  -- named is not a thing anyone wants to discover afterwards.
  values (p_name, p_starting_cash, p_starting_cash, 0, p_mode,
          true, coalesce(p_policy, '{}'::jsonb), 1,
          'single_desk', null, p_parent)
  returning account_id into v_id;
  return v_id;
end;
$ad4$;

create or replace function paper_desk_update(
  p_account_id     uuid,
  p_name           text    default null,
  p_starting_cash  numeric default null,
  p_mode           text    default null,
  p_entries_paused boolean default null,
  p_policy         jsonb   default null
) returns void language plpgsql security definer
set search_path = public as $ad4$
declare v_has_activity boolean;
begin
  if not exists (select 1 from paper_accounts where account_id = p_account_id) then
    raise exception 'no such desk: %', p_account_id;
  end if;

  if p_name is not null then
    if length(btrim(p_name)) < 1 or length(btrim(p_name)) > 100 then
      raise exception 'a desk needs a name between 1 and 100 characters';
    end if;
    update paper_accounts set name = btrim(p_name) where account_id = p_account_id;
  end if;

  if p_starting_cash is not null then
    if p_starting_cash <= 0 or p_starting_cash >= 1000000000 then
      raise exception 'starting cash must be above 0 and below 1,000,000,000';
    end if;
    -- Changing the budget of a desk that has already traded would make its
    -- P&L meaningless - the return is measured against what it started with.
    -- Reset it first; that is what reset is for.
    select exists (select 1 from paper_positions where account_id = p_account_id)
        or exists (select 1 from paper_orders    where account_id = p_account_id)
      into v_has_activity;
    if v_has_activity then
      raise exception 'this desk has traded - reset it before changing its starting cash, or its P&L stops meaning anything';
    end if;
    update paper_accounts
       set starting_cash = p_starting_cash, cash = p_starting_cash, reserved_cash = 0
     where account_id = p_account_id;
  end if;

  if p_mode is not null then
    if p_mode not in ('manual', 'assisted', 'automatic') then
      raise exception 'mode must be manual, assisted or automatic (got %)', p_mode;
    end if;
    update paper_accounts set mode = p_mode where account_id = p_account_id;
  end if;

  if p_entries_paused is not null then
    update paper_accounts set entries_paused = p_entries_paused where account_id = p_account_id;
  end if;

  if p_policy is not null then
    update paper_accounts
       set policy = p_policy, policy_version = policy_version + 1
     where account_id = p_account_id;
  end if;
end;
$ad4$;

create or replace function paper_desk_reset(p_account_id uuid)
returns jsonb language plpgsql security definer
set search_path = public as $ad4$
declare v_start numeric; v_cash numeric; v_orders int; v_positions int;
        v_plans int; v_settled int;
begin
  select starting_cash, cash into v_start, v_cash
    from paper_accounts where account_id = p_account_id;
  if not found then raise exception 'no such desk: %', p_account_id; end if;

  -- Children keep their own money. Resetting a parent does not reach into a
  -- sub-account, or comparing the two afterwards would be meaningless.
  -- GET DIAGNOSTICS takes a bare ROW_COUNT, never an expression, so each
  -- delete gets its own counter rather than accumulating into one.
  delete from paper_position_settlements where account_id = p_account_id;
  get diagnostics v_settled = row_count;
  delete from paper_positions   where account_id = p_account_id;
  get diagnostics v_positions = row_count;
  delete from paper_orders      where account_id = p_account_id;
  get diagnostics v_orders = row_count;
  delete from paper_trade_plans where account_id = p_account_id;
  get diagnostics v_plans = row_count;
  -- paper_activity is APPEND-ONLY - arbdesk_private.immutable_record() raises
  -- "Append-only record; write a linked correction instead" on any delete.
  -- That is the right design: the activity log is the audit trail, and a
  -- reset is an event in the desk's history, not an erasure of it. So the
  -- reset is RECORDED rather than hidden.
  insert into paper_activity (account_id, event_type, payload, cash_delta)
  values (p_account_id, 'account_reset',
          jsonb_build_object('starting_cash', v_start, 'cash_before', v_cash,
                             'positions_cleared', v_positions,
                             'orders_cleared', v_orders,
                             'plans_cleared', v_plans,
                             'settlements_cleared', v_settled),
          v_start - v_cash);

  update paper_accounts
     set cash = v_start, reserved_cash = 0, entries_paused = true
   where account_id = p_account_id;

  return jsonb_build_object('account_id', p_account_id, 'cash', v_start,
                            'orders_cleared', v_orders, 'positions_cleared', v_positions,
                            'settlements_cleared', v_settled,
                            'plans_cleared', v_plans,
                            'activity', 'kept - append-only, a reset event was logged',
                            'entries_paused', true);
end;
$ad4$;

create or replace function paper_desk_archive(p_account_id uuid, p_archived boolean default true)
returns void language plpgsql security definer
set search_path = public as $ad4$
begin
  if not exists (select 1 from paper_accounts where account_id = p_account_id) then
    raise exception 'no such desk: %', p_account_id;
  end if;
  -- Archive, never delete: the activity of a desk is evidence about a
  -- strategy, and it outlives interest in the desk itself.
  update paper_accounts
     set archived_at = case when p_archived then now() else null end,
         entries_paused = case when p_archived then true else entries_paused end
   where account_id = p_account_id;
end;
$ad4$;

-- --------------------------------------------------------------------------
-- 4. What the switcher renders.
-- --------------------------------------------------------------------------
drop view if exists v_paper_desks;
create view v_paper_desks as
select
  a.account_id, a.name, a.parent_account_id,
  p.name                                             as parent_name,
  (a.parent_account_id is not null)                  as is_sub_account,
  a.mode, a.entries_paused, a.archived_at is not null as archived,
  a.starting_cash, a.cash, a.reserved_cash,
  round(a.cash - a.starting_cash, 2)                 as cash_pnl,
  a.policy, a.policy_version, a.created_at,
  coalesce(pos.n, 0)                                 as open_positions,
  coalesce(pos.realized, 0)                          as realized_pnl,
  coalesce(ord.n, 0)                                 as live_orders,
  coalesce(kid.n, 0)                                 as sub_accounts,
  -- One sentence for why this desk is or is not going to do anything.
  case
    when a.archived_at is not null then 'Archived. Kept for its history; it will not trade.'
    when a.entries_paused          then 'Paused - it will not open anything. Unpause to let it act.'
    when a.mode = 'manual'         then 'Manual: it acts only on orders you place yourself.'
    when not exists (select 1 from strategies s where s.enabled)
                                   then format('%s, but every strategy is disabled - nothing will generate an entry.', initcap(a.mode))
    else format('%s, entries live, %s strateg%s enabled.', initcap(a.mode),
                (select count(*) from strategies s where s.enabled),
                case when (select count(*) from strategies s where s.enabled) = 1 then 'y' else 'ies' end)
  end                                                as state
from paper_accounts a
left join paper_accounts p on p.account_id = a.parent_account_id
left join lateral (select count(*) n, sum(realized_pnl) realized
                     from paper_positions where account_id = a.account_id) pos on true
left join lateral (select count(*) n from paper_orders
                    where account_id = a.account_id and status in ('pending','leased')) ord on true
left join lateral (select count(*) n from paper_accounts k
                    where k.parent_account_id = a.account_id) kid on true
order by a.parent_account_id nulls first, a.created_at;

comment on view v_paper_desks is
  'Every paper desk with its live state: cash against what it started with, open positions, live orders, sub-accounts, and one sentence saying whether it will actually do anything.';

do $ad4$
declare r text;
begin
  foreach r in array array['anon', 'authenticated', 'service_role'] loop
    if exists (select 1 from pg_roles where rolname = r) then
      execute format('grant select on v_paper_desks to %I', r);
    end if;
  end loop;
  foreach r in array array['authenticated', 'service_role'] loop
    if exists (select 1 from pg_roles where rolname = r) then
      execute format('grant execute on function paper_desk_create(text, numeric, text, uuid, jsonb) to %I', r);
      execute format('grant execute on function paper_desk_update(uuid, text, numeric, text, boolean, jsonb) to %I', r);
      execute format('grant execute on function paper_desk_reset(uuid) to %I', r);
      execute format('grant execute on function paper_desk_archive(uuid, boolean) to %I', r);
    end if;
  end loop;
end $ad4$;

do $ad4$
declare v_visible int;
begin
  set local role authenticated;
  select count(*) into v_visible from paper_accounts;
  reset role;
  raise notice 'ad4_59: % desk(s) now visible to the browser', v_visible;
  if v_visible = 0 then
    raise warning 'ad4_59: still nothing visible - the Paper page will stay empty';
  end if;
end $ad4$;
