-- ===========================================================================
-- THE WEATHER TABLES KEEP THIRTY DAYS (plan v2 P1.6 phase 2, step 5 part (b),
-- 29 Sep)
--
-- Hassan, 29 Sep: "do step 6 ten step 5". Steps 1-4, 6 and 5 part (a) moved
-- every reader that looks further back than about 30 days onto the
-- repository (scripts/weather_history.py) or onto caches the prunes refuse to
-- outrun (#270). scripts/archive_observations.py now keeps observations 32
-- days and both forecast tables 30.
--
-- prune_observations refused under 30 days; it now refuses under 32. The
-- climb profile (v_city_climb_profile_live, #264) reads the 30 whole local
-- days before today; the oldest can begin 14 hours before its UTC date
-- (UTC+14) and the prune cuts part-way through a day, so the readings must
-- reach 32 days back. The archive asks for 32; this is the second lock, for a
-- hand-run. The body is the live one (20260929180000) with that one line and
-- its message changed; sql/ad4_29_retention.sql carries the same floor.
-- prune_forecasts and prune_forecast_models already refuse under 30.
-- Nothing is deleted here. Re-runnable.
-- ===========================================================================
create or replace function public.prune_observations(
  p_keep_days integer,
  p_dry_run boolean default true,
  p_before timestamptz default null,
  p_expected_rows bigint default null
)
returns jsonb
language plpgsql
security definer
set search_path to 'public', 'pg_temp'
as $ad4$
declare
  v_before timestamptz := coalesce(
    p_before,
    (current_date - p_keep_days)::timestamptz
  );
  v_cut date := (v_before at time zone 'UTC')::date;
  v_doomed bigint;
  v_cached_before bigint;
  v_uncovered bigint;
  v_freed text;
  v_unkept bigint;
  v_unkept_station bigint;
begin
  if p_keep_days < 32 then
    return jsonb_build_object(
      'ok', false,
      'error', 'keep_days must be at least 32 - the climb profile reads the 30 whole local days before today'
    );
  end if;

  if not p_dry_run and p_expected_rows is null then
    return jsonb_build_object(
      'ok', false,
      'error', 'p_expected_rows is required for a committed prune'
    );
  end if;

  if not p_dry_run then
    lock table public.weather_observations in share row exclusive mode;
  end if;

  select count(*) into v_doomed
    from public.weather_observations
   where valid_at < v_before;

  if p_expected_rows is not null and v_doomed <> p_expected_rows then
    return jsonb_build_object(
      'ok', false,
      'error', format(
        'archive row count mismatch: verified %s rows but prune would delete %s',
        p_expected_rows,
        v_doomed
      ),
      'expected_rows', p_expected_rows,
      'would_delete', v_doomed
    );
  end if;

  if v_doomed = 0 then
    return jsonb_build_object(
      'ok', true,
      'deleted', 0,
      'note', format('nothing older than %s', v_before)
    );
  end if;

  select count(*) into v_uncovered
  from (
    select distinct
           o.city_key,
           (o.valid_at at time zone coalesce(c.timezone, 'UTC'))::date as d
      from public.weather_observations o
      join public.cities c on c.city_key = o.city_key
     where o.valid_at < v_before
  ) x
  where not exists (
    select 1
      from public.derived_city_day_features f
     where f.city_key = x.city_key
       and f.obs_date = x.d
  );

  if v_uncovered > 0 then
    return jsonb_build_object(
      'ok', false,
      'error', format(
        '%s city-day(s) older than %s are not in derived_city_day_features. Run select refresh_feature_cache(); first - pruning now would destroy them.',
        v_uncovered,
        v_cut
      ),
      'uncovered_city_days', v_uncovered
    );
  end if;

  -- ...and in derived_city_day_hours, which v_trajectory_evidence reads for
  -- every day the readings no longer hold (plan v2 P1.6 phase 2). A day with
  -- no temperature has no hours to keep.
  select count(*) into v_unkept
  from (
    select distinct
           o.city_key,
           (o.valid_at at time zone coalesce(c.timezone, 'UTC'))::date as d
      from public.weather_observations o
      join public.cities c on c.city_key = o.city_key
     where o.valid_at < v_before
       and o.temp_c is not null
  ) x
  where not exists (
    select 1
      from public.derived_city_day_hours h
     where h.city_key = x.city_key
       and h.obs_date = x.d
  );

  if v_unkept > 0 then
    return jsonb_build_object(
      'ok', false,
      'error', format(
        '%s city-day(s) older than %s are not in derived_city_day_hours. Run common.refresh_feature_cache first (it refreshes the hours) - the trajectory evidence reads them after the prune.',
        v_unkept,
        v_cut
      ),
      'unkept_city_days', v_unkept
    );
  end if;

  -- ...and in derived_station_day_sources, which v_station_day_max reads for
  -- those days (plan v2 P1.6 phase 2, step 5): every source of every day.
  select count(*) into v_unkept_station
  from (
    select distinct
           o.city_key,
           (o.valid_at at time zone coalesce(c.timezone, 'UTC'))::date as d,
           coalesce(o.source, '') as s
      from public.weather_observations o
      join public.cities c on c.city_key = o.city_key
     where o.valid_at < v_before
       and o.temp_c is not null
  ) x
  where not exists (
    select 1
      from public.derived_station_day_sources k
     where k.city_key = x.city_key
       and k.obs_date = x.d
       and k.source = x.s
  );

  if v_unkept_station > 0 then
    return jsonb_build_object(
      'ok', false,
      'error', format(
        '%s city-day source(s) older than %s are not in derived_station_day_sources. Run common.refresh_feature_cache first (it refreshes them) - the settlement agreement reads them after the prune.',
        v_unkept_station,
        v_cut
      ),
      'unkept_station_days', v_unkept_station
    );
  end if;

  select count(*) into v_cached_before
    from public.derived_city_day_features;

  if p_dry_run then
    return jsonb_build_object(
      'ok', true,
      'dry_run', true,
      'would_delete', v_doomed,
      'older_than', v_before,
      'cached_city_days', v_cached_before,
      'expected_rows', p_expected_rows,
      'note', 'call again with p_dry_run => false to actually delete'
    );
  end if;

  delete from public.weather_observations
   where valid_at < v_before;

  v_freed := pg_size_pretty(
    pg_total_relation_size('public.weather_observations')
  );

  return jsonb_build_object(
    'ok', true,
    'deleted', v_doomed,
    'older_than', v_before,
    'cached_city_days', v_cached_before,
    'expected_rows', p_expected_rows,
    'table_now', v_freed,
    'note', 'run VACUUM FULL weather_observations to return the space to the OS'
  );
end;
$ad4$;

comment on function public.prune_observations(integer, boolean, timestamptz, bigint) is
  'Delete archived observations only when p_expected_rows matches the verified export count and every affected city-local day is cached - in derived_city_day_features, derived_city_day_hours (the trajectory evidence) and, per source, derived_station_day_sources (the station''s day). Committed calls require the expected count.';

revoke all on function public.prune_observations(integer, boolean, timestamptz, bigint)
  from public, anon, authenticated;
grant execute on function public.prune_observations(integer, boolean, timestamptz, bigint)
  to service_role;
