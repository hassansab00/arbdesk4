-- ===========================================================================
-- A RUNNING-MAXIMUM RULE IN A CITY WE GET RIGHT HALF THE TIME IS HALF A RULE.
--
-- v_settlement_agreement (sql/ad4_82) measures, per city, how often the
-- maximum we read off the station feed falls inside the band the venue
-- declared the winner. Measured 2026-09-22 the roster is nowhere near
-- uniform:
--
--     29 cities   95.7%   the observation names the winning band 9 times in 10
--     15 cities   78.3%   misses about one in four, by one band
--      3 cities   59.9%   dallas 46.2%, beijing 66.7%, singapore 66.7%
--
-- and nothing in the system knew. s5_running_max_lock and s7_pre_peak_gradient
-- are the two strategies whose entire premise is that the thermometer is
-- right, and they were firing in Dallas on exactly the same terms as in a
-- city we get right nineteen times in twenty.
--
-- A COLUMN ON `cities`, NOT A JOIN TO THE VIEW, and the reason is install
-- order. v_settlement_agreement needs band_contains(), which sql/ad4_34
-- creates, so ad4_82 must run after it - while v_trade_timing (ad4_33) and
-- v_trade_plan (ad4_34) are the two things that need to READ the trust. A
-- view cannot reference something created after it. So the measurement is
-- computed by the view and written to a column both engines can read at any
-- point in the install, by a function the daily pipeline calls.
--
-- NULL MEANS UNMEASURED, NOT UNTRUSTWORTHY. Under ten settled ladders
-- v_settlement_agreement returns null, and every consumer here treats null as
-- "no opinion" and lets the strategy through. A new city has not failed; it
-- has not been measured. Reading null as zero would silently retire every
-- city the moment this shipped.
--
-- THE FIRST REFRESH IS PART OF THE MIGRATION ONLY WHERE THE VIEW EXISTS. The
-- PGlite contract harness applies every migration against a fixture that has
-- no v_settlement_agreement, so the call is guarded - the column lands there,
-- the values do not.
--
-- Idempotent: one nullable column, one create-or-replace function, one
-- conditional refresh.
-- ===========================================================================

alter table public.cities add column if not exists observation_trust numeric;

comment on column public.cities.observation_trust is
  'How often this city''s station maximum falls inside the band the venue settled on, 0-1, from v_settlement_agreement. Null under ten settled ladders, which means unmeasured and must not be read as zero. Refreshed by refresh_observation_trust().';

create or replace function public.refresh_observation_trust()
returns jsonb
language plpgsql
security definer
set search_path = public, extensions
as $ad4$
declare
  v_set int := 0;
  v_null int := 0;
begin
  if to_regclass('public.v_settlement_agreement') is null then
    return jsonb_build_object('ok', false,
      'error', 'v_settlement_agreement missing - run sql/ad4_82_settlement_agreement.sql');
  end if;

  -- Written for every city the view has an opinion about. A city it cannot
  -- judge keeps whatever it had rather than being reset to null: losing a
  -- measurement because today's window happens to be short is not an update.
  update public.cities c
     set observation_trust = a.observation_trust
    from public.v_settlement_agreement a
   where a.city_key = c.city_key
     and a.observation_trust is not null
     and c.observation_trust is distinct from a.observation_trust;
  get diagnostics v_set = row_count;

  select count(*) into v_null
    from public.cities
   where coalesce(status, 'active') = 'active' and observation_trust is null;

  return jsonb_build_object('ok', true, 'updated', v_set, 'unmeasured_active', v_null);
end;
$ad4$;

comment on function public.refresh_observation_trust() is
  'Copy v_settlement_agreement.observation_trust onto cities, so v_trade_timing and v_trade_plan can read it without depending on a view created after them.';

do $ad4$
declare r text;
begin
  foreach r in array array['service_role'] loop
    if exists (select 1 from pg_roles where rolname = r) then
      execute format('grant execute on function public.refresh_observation_trust() to %I', r);
    end if;
  end loop;
end
$ad4$;

do $ad4$
begin
  if to_regclass('public.v_settlement_agreement') is not null then
    perform public.refresh_observation_trust();
  else
    raise notice 'v_settlement_agreement absent - column added, values left for the next refresh';
  end if;
end
$ad4$;
