-- ===========================================================================
-- THE WEATHER TABLES KEEP THREE DAYS (Fresh Supabase, part 2b; Hassan, 8 Oct:
-- "offload Supabase completely daily to the repo, keep it fresh each run
-- day"; no paid plan).
--
-- The database holds what a day's runs read; history is read from the
-- repository the archive writes before every prune (export, verify, commit,
-- then delete). Measured 9 Oct 05:41Z: weather_observations 34,922,496 bytes
-- (142,122 rows from 7 Sep), weather_forecasts 44,294,144 bytes (105,542 rows
-- from 9 Sep), the database 512,363,667 bytes.
--
--   prune_observations  32 -> 3 days. Part 2a (20261008200000) moved the
--     month-long readers onto the caches refresh_city_day_hours keeps for
--     every whole day, or onto the repository. One guard is added: every
--     whole day going must have its peak in derived_city_day_peak, which
--     refresh_weather_peak_city reads for the days the readings no longer
--     hold (the other three caches were guarded already).
--   prune_forecasts  30 -> 3 days. The long readers read the repository
--     (scripts/weather_history.py) or what the nightly freeze kept; the
--     ingest refills only the days the table still holds, so three, not two
--     (the floor's comment has the measured late writes).
--
-- Each function's other guards stay: the verified count, the caches and the
-- freezes, the scoring, the lock. sql/ad4_29 and sql/ad4_63 match.
--
-- Both tables now shed a day every night, so their reclaim backstops go from
-- weekly to daily at the times they had (06:20 and 06:35), long after the
-- archive (dispatched 02:36, up to 45 minutes). sql/ad4_66 matches.
--
-- v_city_observation_health's feed age reads the station-day cache for a
-- city the readings no longer hold: 20261009090100, a migration of its own.
-- Nothing is deleted here. Re-runnable: cron.schedule replaces a job by name.
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
  v_oldest timestamptz;
  v_unkept_peak bigint;
begin
  -- THREE DAYS (Fresh Supabase, part 2b; 32 before). Every reader of more
  -- than the last two days reads the caches below for the days the readings
  -- no longer hold whole, or the repository (scripts/weather_history.py).
  if p_keep_days < 3 then
    return jsonb_build_object(
      'ok', false,
      'error', 'keep_days must be at least 3 - live readers take the readings of the last 48 hours; older days are read from the caches and the archive'
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

  -- ...and in derived_city_day_peak, which refresh_weather_peak_city reads
  -- for those days (Fresh Supabase, part 2b): every day with a temperature
  -- the readings hold whole, by refresh_city_day_hours' rule - from the local
  -- day of the oldest reading, or the next one if that day began before it.
  -- The day the last prune cut into was never whole, so nothing kept its peak.
  select min(valid_at) into v_oldest
    from public.weather_observations;

  select count(*) into v_unkept_peak
  from (
    select distinct
           o.city_key,
           (o.valid_at at time zone coalesce(c.timezone, 'UTC'))::date as d,
           (v_oldest at time zone coalesce(c.timezone, 'UTC'))::date
             + case when ((v_oldest at time zone coalesce(c.timezone, 'UTC'))::date::timestamp
                          at time zone coalesce(c.timezone, 'UTC')) < v_oldest
                    then 1 else 0 end as first_whole
      from public.weather_observations o
      join public.cities c on c.city_key = o.city_key
     where o.valid_at < v_before
       and o.temp_c is not null
  ) x
  where x.d >= x.first_whole
    and not exists (
      select 1
        from public.derived_city_day_peak k
       where k.city_key = x.city_key
         and k.obs_date = x.d
    );

  if v_unkept_peak > 0 then
    return jsonb_build_object(
      'ok', false,
      'error', format(
        '%s whole city-day(s) older than %s are not in derived_city_day_peak. Run common.refresh_feature_cache first (it keeps them) - the peak hour reads them after the prune.',
        v_unkept_peak,
        v_cut
      ),
      'unkept_peak_days', v_unkept_peak
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
  'Delete archived observations only when p_expected_rows matches the verified export count and every affected city-local day is cached - in derived_city_day_features, derived_city_day_hours (the trajectory evidence) and, per source, derived_station_day_sources (the station''s day), and every whole day''s peak in derived_city_day_peak. Refuses under 3 days. Committed calls require the expected count.';

revoke all on function public.prune_observations(integer, boolean, timestamptz, bigint)
  from public, anon, authenticated;
grant execute on function public.prune_observations(integer, boolean, timestamptz, bigint)
  to service_role;

create or replace function public.prune_forecasts(
  p_keep_days integer,
  p_dry_run boolean default true,
  p_before date default null,
  p_expected_rows bigint default null
)
returns jsonb
language plpgsql
security definer
set search_path to 'public', 'pg_temp'
as $ad4$
declare
  v_before date := coalesce(p_before, current_date - p_keep_days);
  v_doomed bigint;
  v_keep bigint;
  v_skill bigint;
  v_skill_at timestamptz;
  v_newest_doomed date;
  v_freed text;
  v_unfrozen bigint;
  v_unfrozen_latest bigint;
begin
  -- THREE DAYS (Fresh Supabase, part 2b; 30 before). Skill, the regime's
  -- history, the backtest and the databank read older days from the
  -- repository (scripts/weather_history.py); the hit forecasts and the
  -- forecast standing at each lead are frozen below. Three, not two: the
  -- ingest refills only the days the table still holds. Of what it held on
  -- 9 Oct, every forecast written two or more days after its day came in on
  -- 12-14 Sep, catching up an outage: 378 two days late, 378 three, 140 four
  -- and 21 five. A three-day keep still takes the first 756, not the 161.
  if p_keep_days < 3 then
    return jsonb_build_object(
      'ok', false,
      'error', 'keep_days must be at least 3 - the ingest refills only the days the table holds; older forecasts are read from the archive'
    );
  end if;

  if not p_dry_run and p_expected_rows is null then
    return jsonb_build_object(
      'ok', false,
      'error', 'p_expected_rows is required for a committed prune'
    );
  end if;

  if not p_dry_run then
    lock table public.weather_forecasts in share row exclusive mode;
  end if;

  select count(*), max(for_date) into v_doomed, v_newest_doomed
    from public.weather_forecasts
   where for_date < v_before;

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

  select count(*), max(computed_at) into v_skill, v_skill_at
    from public.derived_forecast_skill;

  if v_skill = 0 then
    return jsonb_build_object(
      'ok', false,
      'error', 'derived_forecast_skill is empty - these forecasts have never been scored, and the score is what survives the prune. Run the daily pipeline first.',
      'would_delete', v_doomed
    );
  end if;

  if v_skill_at is null or v_skill_at::date < v_newest_doomed then
    return jsonb_build_object(
      'ok', false,
      'error', format(
        'derived_forecast_skill was last computed %s, before the newest forecast being removed (%s). Their contribution was never measured. Run the daily pipeline first.',
        coalesce(v_skill_at::date::text, 'never'),
        v_newest_doomed
      ),
      'would_delete', v_doomed
    );
  end if;

  -- Every v_hit_forecasts row for the days going is frozen (plan v2 P1.6
  -- phase 2): hit_tournament.py reads 120 days, the table keeps about 30.
  select count(*) into v_unfrozen
  from (
    select city_key, for_date, lane, model, forecast_max_c, known_at
      from public.v_hit_forecasts_live where for_date < v_before
    except
    select city_key, for_date, lane, model, forecast_max_c, known_at
      from public.derived_hit_forecasts where for_date < v_before
  ) x;

  if v_unfrozen > 0 then
    return jsonb_build_object(
      'ok', false,
      'error', format(
        '%s v_hit_forecasts row(s) dated before %s are not in derived_hit_forecasts. Run common.refresh_feature_cache first (it freezes them) - the hit tournament reads them after the prune.',
        v_unfrozen,
        v_before
      ),
      'unfrozen_rows', v_unfrozen,
      'would_delete', v_doomed
    );
  end if;

  -- ...and so is the forecast standing at each lead on those days (plan v2
  -- P1.6 phase 2, step 5): both convergence views read 45 days back through
  -- v_forecast_latest, which serves derived_forecast_latest for them.
  select count(*) into v_unfrozen_latest
  from (
    (select distinct on (city_key, for_date, model, lead_days)
            city_key, for_date, model, lead_days, forecast_max_c, run_at
       from public.weather_forecasts
      where for_date < v_before
        and forecast_max_c is not null
      order by city_key, for_date, model, lead_days, run_at desc)
    except
    select city_key, for_date, model, lead_days, forecast_max_c, run_at
      from public.derived_forecast_latest
     where for_date < v_before
  ) x;

  if v_unfrozen_latest > 0 then
    return jsonb_build_object(
      'ok', false,
      'error', format(
        '%s forecast(s) standing at a lead on a day before %s are not in derived_forecast_latest. Run common.refresh_feature_cache first (it freezes them) - the convergence views read them after the prune.',
        v_unfrozen_latest,
        v_before
      ),
      'unfrozen_latest_rows', v_unfrozen_latest,
      'would_delete', v_doomed
    );
  end if;

  select count(*) into v_keep
    from public.weather_forecasts
   where for_date >= v_before;

  if p_dry_run then
    return jsonb_build_object(
      'ok', true,
      'dry_run', true,
      'would_delete', v_doomed,
      'would_keep', v_keep,
      'older_than', v_before,
      'skill_rows', v_skill,
      'skill_computed_at', v_skill_at,
      'expected_rows', p_expected_rows,
      'note', 'call again with p_dry_run => false to actually delete'
    );
  end if;

  delete from public.weather_forecasts
   where for_date < v_before;

  v_freed := pg_size_pretty(
    pg_total_relation_size('public.weather_forecasts')
  );

  return jsonb_build_object(
    'ok', true,
    'deleted', v_doomed,
    'kept', v_keep,
    'older_than', v_before,
    'skill_rows', v_skill,
    'expected_rows', p_expected_rows,
    'table_now', v_freed,
    'note', 'run VACUUM FULL weather_forecasts to return the space to the OS'
  );
end;
$ad4$;

comment on function public.prune_forecasts(integer, boolean, date, bigint) is
  'Delete archived forecasts only when p_expected_rows matches the verified export count, derived_forecast_skill proves the rows were scored, and what v_hit_forecasts and v_forecast_latest read for the days going is frozen (derived_hit_forecasts, derived_forecast_latest). Refuses under 3 days. Committed calls require the expected count.';

revoke all on function public.prune_forecasts(integer, boolean, date, bigint)
  from public, anon, authenticated;
grant execute on function public.prune_forecasts(integer, boolean, date, bigint)
  to service_role;

-- The backstops, daily (the same jobs sql/ad4_66 schedules).
do $$
begin
  if exists (select 1 from pg_namespace where nspname = 'cron') then
    perform cron.schedule('ad4_reclaim_weather_forecasts', '20 6 * * *',
                          'VACUUM (FULL, ANALYZE) public.weather_forecasts');
    perform cron.schedule('ad4_reclaim_weather_observations', '35 6 * * *',
                          'VACUUM (FULL, ANALYZE) public.weather_observations');
  end if;
end $$;
