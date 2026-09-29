-- ===========================================================================
-- A DAY THE PRUNE HAS CUT INTO KEEPS ITS CACHED VALUES (plan v2 P1.6, 28 Sep)
--
-- prune_observations cuts every city at one instant (the archiver's cutoff,
-- ~02:45Z), part-way through most cities' local day. refresh_feature_cache
-- then recomputed that day from the readings the cut left and overwrote the
-- value cached while the day was whole; the next prune took the rest, so the
-- wrong value stayed in derived_city_day_features - the table the weekly fit
-- trains on and every prune is guarded by.
--
-- Measured 28 Sep against the raw readings in data/archive/observations, on
-- the nine cut dates since 18 Jun: of 470 cached city-days, 415 held fewer
-- readings than the raw data and 152 a daily maximum too low, by 3.49 C on
-- average and 12.0 C at worst, never too high (nyc 28 Jul: 23.89 C from 2
-- readings against 26.67 C from 24). The first whole day after each cut also
-- took its prev_max_c, delta_max_c and pressure change from the part-day.
--
-- Now: a city's local day that began before the oldest reading still held is
-- left as cached, and the first whole day's day-over-day terms come from the
-- cached day before it. Every other day is written exactly as before (proved
-- live 28 Sep: 3,189 city-days in the view, 51 cut days skipped, one per city,
-- all on 29-30 Jul). The damaged rows are repaired separately, from the repo.
--
-- The body is the one in sql/ad4_28_feature_cache.sql and sql/ad4_29_retention.sql
-- (identical). Same signature, so the grants stand. Re-runnable.
-- ===========================================================================

create or replace function public.refresh_feature_cache(p_days int default null,
                                                 p_city text default null)
returns jsonb language plpgsql security definer as $ad4$
declare
  t0 timestamptz := clock_timestamp();
  v_from date;
  v_days int := 0; v_hours int := 0; v_kept int; v_n int;
  v_city text;
  v_tz text;
  v_complete_from timestamptz;
  v_first date;
  v_skipped int := 0;
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

  -- A LOCAL DAY THE PRUNE HAS CUT INTO IS NEVER RECOMPUTED (28 Sep).
  -- prune_observations cuts every city at one instant, which falls part-way
  -- through most cities' local day. This used to recompute that day from the
  -- readings the cut left - a US day from its last few evening hours - and
  -- overwrite the value it had cached while the day was whole; the next prune
  -- took the rest, so the wrong value stayed. Measured 28 Sep against the raw
  -- readings in data/archive/observations, on the nine cut dates since 18 Jun:
  -- of 470 cached city-days, 415 held fewer readings than the raw data and 152
  -- a daily maximum too low, by 3.49 C on average and 12.0 C at worst (never
  -- too high); nyc 28 Jul cached 23.89 C from 2 readings against 26.67 C from
  -- 24. This is what the weekly fit trains on. Every city's day that began
  -- before the oldest reading still held is left as it was cached - whole,
  -- since it was refreshed every night before the cut reached it.
  select min(valid_at) into v_complete_from from weather_observations;
  v_complete_from := coalesce(v_complete_from, '-infinity'::timestamptz);

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
    -- A day is whole when its local midnight is no earlier than the oldest
    -- reading held: the local date that reading falls on, unless it fell
    -- exactly at midnight, is the last day the cut reached.
    select coalesce(timezone, 'UTC') into v_tz from cities where city_key = v_city;
    v_first := (v_complete_from at time zone v_tz)::date;
    if (v_first::timestamp at time zone v_tz) < v_complete_from then
      v_first := v_first + 1;
    end if;
    if exists (select 1 from weather_observations
                where city_key = v_city and valid_at < (v_first::timestamp at time zone v_tz)) then
      v_skipped := v_skipped + 1;
    end if;

    -- A cut day is still INSERTED when it was never cached (a new database's
    -- first day), so the prune's guard can pass; it is never UPDATED.
    insert into derived_city_day_features (
      city_key, obs_date, max_c, min_c, diurnal_range_c, n_obs, prev_max_c,
      delta_max_c, morning_temp_c, morning_dewpoint_c, dewpoint_depression_c,
      morning_humidity, morning_pressure_hpa, morning_to_max_c, cloud_mean,
      cloud_max, wind_mean, wind_max, precip_total, pressure_change_24h_hpa,
      wind_u_mean, wind_v_mean, computed_at)
    select
      city_key, obs_date, max_c, min_c, diurnal_range_c, n_obs, prev_max_c,
      delta_max_c, morning_temp_c, morning_dewpoint_c, dewpoint_depression_c,
      morning_humidity, morning_pressure_hpa, morning_to_max_c, cloud_mean,
      cloud_max, wind_mean, wind_max, precip_total, pressure_change_24h_hpa,
      wind_u_mean, wind_v_mean, now()
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
      -- COALESCE, NOT OVERWRITE. refresh_feature_cache can only see
      -- what weather_observations still holds - about 90 days - while
      -- the cache goes back 14 months. A plain assignment would blank
      -- every backfilled wind vector outside the retention window the
      -- first time this ran, which is the whole history the fit needs.
      wind_u_mean = coalesce(excluded.wind_u_mean, derived_city_day_features.wind_u_mean),
      wind_v_mean = coalesce(excluded.wind_v_mean, derived_city_day_features.wind_v_mean),
      computed_at = now()
    where excluded.obs_date >= v_first;
    get diagnostics v_n = row_count;
    v_days := v_days + v_n;

    -- ...and the first whole day's day-over-day terms come from the cached
    -- day before it, not from the part of it the raw table still holds (the
    -- view's lag() sees only that part). Every later day's lag is the same
    -- either way, because the cache and the raw table hold the same days.
    update derived_city_day_features f
       set prev_max_c = p.max_c,
           delta_max_c = f.max_c - p.max_c,
           pressure_change_24h_hpa = round(f.morning_pressure_hpa - p.morning_pressure_hpa, 2)
      from derived_city_day_features p
     where f.city_key = v_city and f.obs_date = v_first
       and p.city_key = v_city
       and p.obs_date = (select max(q.obs_date) from derived_city_day_features q
                          where q.city_key = v_city and q.obs_date < v_first);

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
    'cut_days_left_as_cached', v_skipped, 'whole_days_from', v_complete_from,
    'city_hours', v_hours,
    'ms', round(extract(epoch from (clock_timestamp() - t0)) * 1000));
end;
$ad4$;
