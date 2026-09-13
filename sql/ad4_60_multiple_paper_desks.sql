-- ===========================================================================
-- ad4_60_multiple_paper_desks.sql - MORE THAN ONE PAPER DESK.
--
-- Safe to run any time. Drops one unique index, redefines one function to be
-- deterministic, and changes no data.
--
-- The desk wants several independent paper runs - each with its own budget,
-- its own strategies, its own cities - so a setting can be tried without
-- disturbing the one that is already running.
--
-- Almost all of that already existed. paper_accounts.policy already carries
-- {strategies[], cities[], max_plan_usd, max_exposure_usd, min_edge},
-- set_single_paper_policy() already validates it per account, and the Paper
-- page already renders an account SWITCHER over an array. Every command
-- function - submit_single_paper_order, cancel, approve, exit - already takes
-- p_account and checks that one row.
--
-- Exactly two things forced a single desk:
--
--   1. create unique index one_single_paper_desk
--        on paper_accounts(access_mode) where access_mode='single_desk';
--
--   2. the /api/paper-desk route listing accounts with .limit(1).
--
-- This file removes the first. The second is in the route.
--
--
-- WHY DROPPING IT IS SAFE, CHECKED RATHER THAN ASSUMED
--
-- The index came with single-operator mode
-- (supabase/migrations/20260912151318_single_desk_paper_access.sql), whose
-- point was removing the Paper Trades sign-in - NOT limiting the desk to one
-- account. Its functions were written account-id-first and are unaffected:
-- each one looks up `where account_id = p_account` and then asserts
-- access_mode='single_desk' and owner_id is null, which stays true of every
-- desk here.
--
-- The one exception is create_single_paper_account(), which answers "give me
-- the desk, making it if absent" with
--
--     select account_id into id from paper_accounts where access_mode='single_desk';
--
-- With one row that is exact. With several, plpgsql's SELECT INTO quietly
-- takes whichever row the scan happens to return first, so the bootstrap
-- could answer differently between calls. Its contract does not change here -
-- it still means "ensure a desk exists" and still returns one - it is only
-- made deterministic: the OLDEST desk, which is the one that already existed
-- before any of this. New desks come from paper_desk_create() in ad4_59.
--
-- access_mode stays 'single_desk' on every desk. It is the name of the ACCESS
-- model - no per-user owner, commands through the server-side API - not a
-- count. Renaming it would touch every function and the Edge gateway for no
-- behavioural gain.
-- ===========================================================================

do $ad4$
begin
  if to_regclass('public.paper_accounts') is null then
    raise exception 'ad4_60 needs paper_accounts - run sql/ad4_paper_engine_columns.sql first';
  end if;
end $ad4$;

drop index if exists public.one_single_paper_desk;

-- Keeps the bootstrap honest now that several rows can match.
create or replace function public.create_single_paper_account(
  p_name text, p_starting_cash numeric
) returns uuid
language plpgsql security invoker set search_path = '' as $ad4$
declare id uuid;
begin
  -- ORDER BY created_at: see the header. Without it this answers arbitrarily
  -- once a second desk exists.
  select account_id into id
    from public.paper_accounts
   where access_mode = 'single_desk'
   order by created_at
   limit 1;
  if found then return id; end if;

  insert into public.paper_accounts (owner_id, access_mode, name, starting_cash, cash)
  values (null, 'single_desk', p_name, p_starting_cash, p_starting_cash)
  returning account_id into id;

  insert into public.paper_activity (account_id, event_type, payload, cash_delta)
  values (id, 'account_opened',
          jsonb_build_object('starting_cash', p_starting_cash, 'access_mode', 'single_desk'),
          p_starting_cash);
  return id;
end
$ad4$;

-- ad4_59's paper_desk_create refused a second desk while the index stood, and
-- named it in the error. With the index gone that check must go too, or it
-- would keep refusing.
create or replace function public.paper_desk_create(
  p_name text, p_starting_cash numeric default 10000, p_mode text default 'manual',
  p_parent uuid default null, p_policy jsonb default '{}'::jsonb
) returns uuid language plpgsql security definer set search_path = public as $ad4$
declare v_id uuid; v_parent_of_parent uuid;
begin
  p_name := btrim(coalesce(p_name, ''));
  if length(p_name) < 1 or length(p_name) > 100 then
    raise exception 'a desk needs a name between 1 and 100 characters';
  end if;
  if p_starting_cash is null or p_starting_cash <= 0 or p_starting_cash >= 1000000000 then
    raise exception 'starting cash must be above 0 and below 1,000,000,000 (got %)', p_starting_cash;
  end if;
  if p_mode not in ('manual','assisted','automatic') then
    raise exception 'mode must be manual, assisted or automatic (got %)', p_mode;
  end if;
  if exists (select 1 from paper_accounts where lower(name) = lower(p_name)
                                            and archived_at is null) then
    raise exception 'a desk named % already exists - two desks with one name is a test you cannot read afterwards', p_name;
  end if;
  if p_parent is not null then
    select parent_account_id into strict v_parent_of_parent
      from paper_accounts where account_id = p_parent;
    if v_parent_of_parent is not null then
      raise exception 'sub-accounts are one level deep - % is itself a sub-account', p_parent;
    end if;
  end if;

  insert into paper_accounts (name, starting_cash, cash, reserved_cash, mode,
                              entries_paused, policy, policy_version,
                              access_mode, owner_id, parent_account_id)
  -- Paused on creation, always: a desk that starts trading the moment it is
  -- named is not a thing anyone wants to discover afterwards.
  values (p_name, p_starting_cash, p_starting_cash, 0, p_mode, true,
          coalesce(p_policy, '{}'::jsonb), 1, 'single_desk', null, p_parent)
  returning account_id into v_id;

  insert into paper_activity (account_id, event_type, payload, cash_delta)
  values (v_id, 'account_opened',
          jsonb_build_object('starting_cash', p_starting_cash, 'mode', p_mode,
                             'parent_account_id', p_parent),
          p_starting_cash);
  return v_id;
end;
$ad4$;

do $ad4$
declare r text;
begin
  foreach r in array array['authenticated', 'service_role'] loop
    if exists (select 1 from pg_roles where rolname = r) then
      execute format('grant execute on function paper_desk_create(text, numeric, text, uuid, jsonb) to %I', r);
    end if;
  end loop;
end $ad4$;

do $ad4$
declare v int;
begin
  select count(*) into v from paper_accounts;
  raise notice 'ad4_60: % paper desk(s); more may now be created', v;
end $ad4$;
