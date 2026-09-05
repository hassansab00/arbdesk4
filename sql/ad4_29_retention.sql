-- ===========================================================================
-- ad4_29_retention.sql - keep the database small enough to be free.
--
-- THE NUMBERS. On a 710k-row archive weather_observations is 184 MB of a
-- 500 MB free tier - 98% of everything - at 272 bytes a row. The same data as
-- gzipped CSV is 3.6 MB. Fifty-one times smaller, because a Postgres row
-- carries a 24-byte header, per-column length bytes and index entries, and a
-- CSV of mostly-repeating numbers compresses beautifully.
--
-- So: Postgres holds the working set, and cold history lives as a compressed
-- file somewhere that costs nothing. scripts/archive_observations.py does the
-- moving; this file makes it safe.
--
-- WHAT MAKES IT SAFE. Pruning raw observations is only acceptable because the
-- thing the desk actually models - derived_city_day_features, one row per
-- city-day - survives the prune. That is 15 MB against 184 MB and it is what
-- scripts/weather_model.py, the analytics views and the climb profile all read.
--
-- But refresh_feature_cache() as written DELETES every cached row and rebuilds
-- from the raw table. Run that after a prune and it would silently destroy
-- exactly the history the prune was supposed to preserve. That is the bug this
-- file exists to prevent, and it is why the refresh becomes incremental here
-- rather than in ad4_28.
--
-- Run order: after sql/ad4_28_feature_cache.sql. Re-runnable.
-- ===========================================================================

do $ad4$
begin
  if to_regclass('public.derived_city_day_features') is null then
    raise exception 'ad4_29 needs the ad4_28 caches - run sql/ad4_28_feature_cache.sql first';
  end if;
end
$ad4$;


-- --------------------------------------------------------------------------
-- 1. An INCREMENTAL refresh.
--
--    Rebuilds only the days still covered by raw observations and upserts
--    them, leaving older cached rows alone. The window functions inside
--    v_city_day_features (prev_max_c, pressure_change_24h_hpa) look one day
--    back, so the recomputed window is extended by a margin - otherwise the
--    first day of every refresh would get a null lag and quietly lose its
--    carry-over term.
-- --------------------------------------------------------------------------
-- ONE SIGNATURE, and the drops below make sure of it. Two overloads make a
-- bare `select refresh_feature_cache()` fail with "function is not unique",
-- which is exactly how scripts/capacity.py calls it. The drops clean up
-- databases that ran either earlier version - ad4_28's no-argument one, and
-- the one-argument one this file used to declare.
drop function if exists refresh_feature_cache();
drop function if exists refresh_feature_cache(int);

create or replace function refresh_feature_cache(p_days int default null,
                                                 p_city text default null)
returns jsonb language plpgsql security definer as $ad4$
declare
  t0 timestamptz := clock_timestamp();
  v_from date;
  v_days int := 0; v_hours int := 0; v_kept int; v_n int;
  v_city text;
begin
  -- null means "everything raw observations still cover", which is the right
  -- default both before and after a prune.
  if p_days is null then
    select min((valid_at at time zone 'UTC')::date) into v_from from weather_observations
     where p_city is null or city_key = p_city;
  else
    v_from := current_date - p_days;
  end if;
  -- two days of margin so the lag terms on the boundary day are real
  v_from := coalesce(v_from, current_date) - 2;

  -- p_city IS THE WHOLE POINT, and it exists because of how statement_timeout
  -- actually works.
  --
  -- The timer starts when the TOP-LEVEL statement starts and is never reset by
  -- the statements a function runs inside itself. So a plpgsql loop cannot
  -- rescue a call that is too slow: `select refresh_feature_cache()` is one
  -- statement whether it runs one query or a thousand, and on a 710k-row
  -- archive it took ~6.3 s and Supabase cancelled it - which reaches the caller
  -- over PostgREST as a bare HTTP 500 with no message. Archive Observations
  -- then refused to prune (correctly - the cache is what survives a prune) and
  -- Derived Recompute swallowed the same failure as a note, so the cache
  -- quietly stopped being refreshed at all while every job reported success.
  --
  -- Splitting has to happen where each slice is its own statement, so the
  -- CALLER loops: scripts/capacity.py and scripts/archive_observations.py call
  -- this once per city. Each call is ~100 ms and gets its own fresh timeout, on
  -- any box, at any archive size. Called with no city it still does everything,
  -- which is fine by hand and on a small database.
  for v_city in
    select city_key from cities
     where p_city is null or city_key = p_city
     order by city_key
  loop
    insert into derived_city_day_features (
      city_key, obs_date, max_c, min_c, diurnal_range_c, n_obs, prev_max_c,
      delta_max_c, morning_temp_c, morning_dewpoint_c, dewpoint_depression_c,
      morning_humidity, morning_pressure_hpa, morning_to_max_c, cloud_mean,
      cloud_max, wind_mean, wind_max, precip_total, pressure_change_24h_hpa,
      computed_at)
    select
      city_key, obs_date, max_c, min_c, diurnal_range_c, n_obs, prev_max_c,
      delta_max_c, morning_temp_c, morning_dewpoint_c, dewpoint_depression_c,
      morning_humidity, morning_pressure_hpa, morning_to_max_c, cloud_mean,
      cloud_max, wind_mean, wind_max, precip_total, pressure_change_24h_hpa,
      now()
    from v_city_day_features
    where city_key = v_city and obs_date >= v_from
    on conflict (city_key, obs_date) do update set
      max_c = excluded.max_c, min_c = excluded.min_c,
      diurnal_range_c = excluded.diurnal_range_c, n_obs = excluded.n_obs,
      prev_max_c = excluded.prev_max_c, delta_max_c = excluded.delta_max_c,
      morning_temp_c = excluded.morning_temp_c,
      morning_dewpoint_c = excluded.morning_dewpoint_c,
      dewpoint_depression_c = excluded.dewpoint_depression_c,
      morning_humidity = excluded.morning_humidity,
      morning_pressure_hpa = excluded.morning_pressure_hpa,
      morning_to_max_c = excluded.morning_to_max_c,
      cloud_mean = excluded.cloud_mean, cloud_max = excluded.cloud_max,
      wind_mean = excluded.wind_mean, wind_max = excluded.wind_max,
      precip_total = excluded.precip_total,
      pressure_change_24h_hpa = excluded.pressure_change_24h_hpa,
      computed_at = now();
    get diagnostics v_n = row_count;
    v_days := v_days + v_n;

    -- The climb profile is a whole-history aggregate, so it is still rebuilt
    -- whole - but from the CACHE, not from raw observations, so it keeps
    -- working after a prune. That is the reason it is redefined here. The
    -- delete sits inside the loop so a city is never left with its old rows
    -- gone and its new ones not yet written.
    delete from derived_climb_profile where city_key = v_city;
    insert into derived_climb_profile (
      city_key, local_hour, n_days, typical_climb_left_c, climb_left_sd_c,
      climb_left_p10_c, climb_left_p90_c, pct_already_peaked)
    select city_key, local_hour, n_days, typical_climb_left_c, climb_left_sd_c,
           climb_left_p10_c, climb_left_p90_c, pct_already_peaked
    from v_city_climb_profile_live
    where city_key = v_city;
    get diagnostics v_n = row_count;
    v_hours := v_hours + v_n;
  end loop;

  select count(*) into v_kept from derived_city_day_features;

  return jsonb_build_object(
    'ok', true, 'refreshed_from', v_from, 'city', p_city,
    'city_days_touched', v_days, 'city_days_total', v_kept,
    'city_hours', v_hours,
    'ms', round(extract(epoch from (clock_timestamp() - t0)) * 1000));
end;
$ad4$;


-- --------------------------------------------------------------------------
-- 2. What the database is actually spending its free tier on.
-- --------------------------------------------------------------------------
create or replace view v_storage_report as
select
  c.relname                                            as table_name,
  pg_size_pretty(pg_total_relation_size(c.oid))        as total,
  pg_total_relation_size(c.oid)                        as total_bytes,
  coalesce(s.n_live_tup, 0)                            as approx_rows,
  case when coalesce(s.n_live_tup, 0) > 0
       then round(pg_total_relation_size(c.oid)::numeric / s.n_live_tup)
  end                                                  as bytes_per_row
from pg_class c
join pg_namespace n on n.oid = c.relnamespace
left join pg_stat_user_tables s on s.relid = c.oid
where n.nspname = 'public' and c.relkind = 'r'
order by pg_total_relation_size(c.oid) desc;

comment on view v_storage_report is
  'Where the free tier is going. weather_observations is normally 90%+ of it and compresses about 50x as CSV - see scripts/archive_observations.py.';


-- --------------------------------------------------------------------------
-- 3. Prune, with the safety the whole design rests on.
--
--    REFUSES unless the cache already covers the rows about to be deleted.
--    A prune that runs before the refresh destroys history permanently, and
--    the only warning would be a model that quietly has less to learn from.
-- --------------------------------------------------------------------------
-- p_before EXISTS BECAUSE THE TWO CUTOFFS WERE NOT THE SAME CUTOFF.
--
-- The archive script computed `now() - keep_days` in Python, exported
-- everything older, uploaded it, verified it - and only then called this,
-- which computed `current_date - p_keep_days` again, minutes later. On a date
-- boundary that second cutoff is a day LATER, so a whole day of observations
-- was deleted having never been exported. Even inside one day the window only
-- ever moves forward, so the sliver between the two is deleted unarchived,
-- silently, every run.
--
-- The caller now passes the exact instant it exported to, and this deletes
-- strictly less than or equal to what was archived. p_keep_days stays, both
-- for a hand-run and to keep the 30-day floor honest.
drop function if exists prune_observations(int, boolean);

create or replace function prune_observations(p_keep_days int,
                                              p_dry_run boolean default true,
                                              p_before timestamptz default null)
returns jsonb language plpgsql security definer as $ad4$
declare
  -- p_before wins when the caller gives one: it is the instant actually
  -- exported. Falling back to the date keeps a hand-run working.
  v_before timestamptz := coalesce(p_before,
                                   (current_date - p_keep_days)::timestamptz);
  v_cut date := (v_before at time zone 'UTC')::date;
  v_doomed bigint; v_cached_before bigint; v_uncovered bigint; v_freed text;
begin
  if p_keep_days < 30 then
    -- The model needs MIN_DAYS (120) to fit at all and the trend views need a
    -- fortnight. Below a month there is nothing left to work with.
    return jsonb_build_object('ok', false,
      'error', 'keep_days must be at least 30 - the model needs months, not days');
  end if;

  select count(*) into v_doomed
    from weather_observations where valid_at < v_before;
  if v_doomed = 0 then
    return jsonb_build_object('ok', true, 'deleted', 0,
      'note', format('nothing older than %s', v_before));
  end if;

  -- Every city-day about to lose its raw rows must already be in the cache.
  select count(*) into v_uncovered from (
    select distinct o.city_key, (o.valid_at at time zone 'UTC')::date as d
      from weather_observations o
     where o.valid_at < v_before
  ) x
  where not exists (
    select 1 from derived_city_day_features f
     where f.city_key = x.city_key and f.obs_date = x.d
  );

  if v_uncovered > 0 then
    return jsonb_build_object('ok', false,
      'error', format('%s city-day(s) older than %s are not in derived_city_day_features. Run select refresh_feature_cache(); first - pruning now would destroy them.',
                      v_uncovered, v_cut),
      'uncovered_city_days', v_uncovered);
  end if;

  select count(*) into v_cached_before from derived_city_day_features;

  if p_dry_run then
    return jsonb_build_object('ok', true, 'dry_run', true, 'would_delete', v_doomed,
      'older_than', v_before, 'cached_city_days', v_cached_before,
      'note', 'call again with p_dry_run => false to actually delete');
  end if;

  delete from weather_observations where valid_at < v_before;
  v_freed := pg_size_pretty(pg_total_relation_size('weather_observations'));

  return jsonb_build_object('ok', true, 'deleted', v_doomed, 'older_than', v_before,
    'cached_city_days', v_cached_before, 'table_now', v_freed,
    'note', 'run VACUUM FULL weather_observations to return the space to the OS');
end;
$ad4$;

comment on function prune_observations(int, boolean, timestamptz) is
  'Delete raw observations older than p_before (or p_keep_days if not given). Refuses unless every affected city-day is already in derived_city_day_features. Dry run by default. Pass p_before with the exact instant you exported to - the two cutoffs must be the same cutoff or the gap between them is deleted unarchived.';


-- --------------------------------------------------------------------------
-- 4. Grants. Pruning is service_role only - it is the one destructive thing
--    on the desk, and the anon key lives in the browser.
-- --------------------------------------------------------------------------
do $ad4$
declare r text;
begin
  foreach r in array array['anon', 'authenticated'] loop
    if exists (select 1 from pg_roles where rolname = r) then
      execute format('grant select on v_storage_report to %I', r);
    end if;
  end loop;
  if exists (select 1 from pg_roles where rolname = 'service_role') then
    execute 'grant select on v_storage_report to service_role';
    execute 'grant execute on function prune_observations(int, boolean, timestamptz) to service_role';
  end if;
  foreach r in array array['anon', 'authenticated', 'service_role'] loop
    if exists (select 1 from pg_roles where rolname = r) then
      execute format('grant execute on function refresh_feature_cache(int, text) to %I', r);
    end if;
  end loop;
end
$ad4$;


do $ad4$
declare v_total bigint; v_obs bigint;
begin
  select coalesce(sum(total_bytes), 0) into v_total from v_storage_report;
  select coalesce(total_bytes, 0) into v_obs from v_storage_report where table_name = 'weather_observations';
  raise notice 'ad4_29: % of % is weather_observations',
    pg_size_pretty(v_obs), pg_size_pretty(v_total);
  raise notice 'ad4_29: archive and prune with scripts/archive_observations.py. Never prune without refreshing the cache first - prune_observations() refuses, but the Action does both in order.';
end
$ad4$;
