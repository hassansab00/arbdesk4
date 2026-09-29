-- ===========================================================================
-- ad4_97_evidence_cache.sql - THE TRAJECTORY'S HOURS AND THE HIT
-- TOURNAMENT'S FORECASTS OUTLAST THE WEATHER TABLES' KEEP (plan v2 P1.6
-- phase 2, step 6 part (b), 29 Sep)
--
-- Safe to run any time. Two functions; each rewrites only its own cache.
--
-- Hassan, 29 Sep: "do step 6 ten step 5". Step 5 lowers the weather tables'
-- keep to about 30 days. Two SQL readers look further back and would lose
-- what they read (measured 29 Sep):
--
--   v_trajectory_evidence  trajectory.py reads 120 days of it; each row is a
--                          day's hourly readings, and its climb as of the 30
--                          days before. It read weather_observations only.
--   v_hit_forecasts        hit_tournament.py reads 120 days; 16,560 rows from
--                          21 Aug, off both forecast tables.
--
-- refresh_city_day_hours keeps each city's local day as its 24 hourly
-- maxima in derived_city_day_hours (sql/ad4_86), written every night while
-- the day is whole and never rewritten once the prune has cut into it -
-- refresh_feature_cache's rule, and for the same reason: a cut day
-- recomputed from what the cut left reads several degrees low (28 Sep).
-- v_trajectory_evidence reads the readings for whole days and this before.
--
-- freeze_hit_forecasts copies v_hit_forecasts_live into
-- derived_hit_forecasts (sql/ad4_88) for every day both forecast tables still
-- hold; v_hit_forecasts serves the frozen rows for the days before.
--
-- Step 5 (29 Sep) adds the last two long readers:
--
--   v_station_day_max      the settlement agreement (each city's observation
--                          trust), the settlement-gap report and databank's
--                          late proofs read every day it has, off
--                          weather_observations only.
--   v_forecast_convergence and v_forecast_convergence_all read 45 days of
--                          the forecast standing at each lead.
--
-- refresh_city_day_hours also keeps each city's local day per source in
-- derived_station_day_sources (sql/ad4_29) under the same rule, and
-- freeze_forecast_latest copies the forecast standing at each lead on every
-- day that has passed into derived_forecast_latest (sql/ad4_31).
--
-- All three run inside common.refresh_feature_cache - every night in
-- capacity.py and before every weather prune in archive_observations.py,
-- which refuses to prune when they fail - and the three weather prunes each
-- refuse to delete what these have not kept (sql/ad4_29, ad4_63, ad4_95).
--
-- Phase 3, step 3.1 (29 Sep) adds freeze_edge_marks, in the same call: each
-- band's YES edge at the two cutoffs v_hit_ladders and v_city_hit_history
-- read, into derived_edge_marks (sql/ad4_80), so the edges prune can keep two
-- days. That prune's own view holds back every mark not copied yet.
-- ===========================================================================

create or replace function public.refresh_city_day_hours(p_city text default null)
returns jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $fn$
declare
  t0 timestamptz := clock_timestamp();
  v_oldest  timestamptz;
  v_city    text;
  v_tz      text;
  v_first   date;
  v_n       int;
  v_written int := 0;
  v_station int := 0;
  v_cities  int := 0;
begin
  select min(valid_at) into v_oldest from weather_observations;
  if v_oldest is null then
    return jsonb_build_object('ok', true, 'city', p_city, 'days_written', 0,
                              'note', 'weather_observations holds no readings');
  end if;

  for v_city, v_tz in
    select city_key, coalesce(timezone, 'UTC') from cities
     where p_city is null or city_key = p_city
     order by city_key
  loop
    -- The first whole local day: the one the oldest reading held falls on,
    -- unless it began before that reading (refresh_feature_cache's rule).
    v_first := (v_oldest at time zone v_tz)::date;
    if (v_first::timestamp at time zone v_tz) < v_oldest then
      v_first := v_first + 1;
    end if;

    -- Every local day the readings hold, as 24 slots of hourly maxima. A cut
    -- day is inserted when it was never cached and never updated.
    insert into derived_city_day_hours (city_key, obs_date, temp_c, n_hours, computed_at)
    select v_city, x.d, array_agg(h.temp_c order by g.slot), count(h.temp_c)::int, now()
      from (select distinct (o.valid_at at time zone v_tz)::date as d
              from weather_observations o
             where o.city_key = v_city and o.temp_c is not null) x
      cross join generate_series(0, 23) as g(slot)
      left join (select (o.valid_at at time zone v_tz)::date as d,
                        extract(hour from o.valid_at at time zone v_tz)::int as hr,
                        max(o.temp_c) as temp_c
                   from weather_observations o
                  where o.city_key = v_city and o.temp_c is not null
                  group by 1, 2) h
        on h.d = x.d and h.hr = g.slot
     group by x.d
    on conflict (city_key, obs_date) do update
       set temp_c = excluded.temp_c, n_hours = excluded.n_hours, computed_at = now()
     where excluded.obs_date >= v_first;
    get diagnostics v_n = row_count;
    v_written := v_written + v_n;

    -- The same days per source, for v_station_day_max (sql/ad4_82): the
    -- maximum in both units, the readings, the first and last one and the
    -- station. A null source is kept as ''; the view's primary-source test
    -- is false for both.
    insert into derived_station_day_sources
           (city_key, obs_date, source, max_c, max_f, n_readings, last_reading_at, station, computed_at,
            first_reading_at)
    select v_city, (o.valid_at at time zone v_tz)::date, coalesce(o.source, ''),
           max(o.temp_c), max(o.temp_f), count(*), max(o.valid_at), min(o.station), now(),
           min(o.valid_at)
      from weather_observations o
     where o.city_key = v_city and o.temp_c is not null
     group by 2, 3
    on conflict (city_key, obs_date, source) do update
       set max_c = excluded.max_c, max_f = excluded.max_f, n_readings = excluded.n_readings,
           last_reading_at = excluded.last_reading_at, station = excluded.station, computed_at = now(),
           first_reading_at = excluded.first_reading_at
     where excluded.obs_date >= v_first;
    get diagnostics v_n = row_count;
    v_station := v_station + v_n;

    v_cities := v_cities + 1;
  end loop;

  return jsonb_build_object(
    'ok', true, 'city', p_city, 'cities', v_cities, 'days_written', v_written,
    'station_days_written', v_station,
    'whole_from_instant', v_oldest,
    'ms', round(extract(epoch from (clock_timestamp() - t0)) * 1000));
end;
$fn$;

comment on function public.refresh_city_day_hours(text) is
  'Write each city''s local days as 24 hourly maxima into derived_city_day_hours, and per source into derived_station_day_sources: every day the readings hold whole, and a cut day only if it was never cached (plan v2 P1.6 phase 2). Called once a night for every city - retired ones too, since the prune cuts theirs - by common.refresh_feature_cache.';

revoke all on function public.refresh_city_day_hours(text) from public, anon, authenticated;
grant execute on function public.refresh_city_day_hours(text) to service_role;


create or replace function public.freeze_hit_forecasts()
returns jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $fn$
declare
  t0 timestamptz := clock_timestamp();
  v_from    date;
  v_removed int;
  v_written int;
begin
  -- The oldest for_date both forecast tables hold: from it on, the live rows
  -- are whole, and they replace whatever was frozen for those days.
  select greatest((select min(for_date) from weather_forecasts),
                  (select min(for_date) from weather_forecast_models))
    into v_from;
  if v_from is null then
    return jsonb_build_object('ok', true, 'rows_written', 0,
                              'note', 'neither forecast table holds a row');
  end if;

  delete from derived_hit_forecasts where for_date >= v_from;
  get diagnostics v_removed = row_count;

  insert into derived_hit_forecasts (city_key, for_date, lane, model, forecast_max_c, known_at, frozen_at)
  select city_key, for_date, lane, model, forecast_max_c, known_at, now()
    from v_hit_forecasts_live
   where for_date >= v_from;
  get diagnostics v_written = row_count;

  return jsonb_build_object(
    'ok', true, 'from', v_from, 'rows_replaced', v_removed, 'rows_written', v_written,
    'rows_total', (select count(*) from derived_hit_forecasts),
    'ms', round(extract(epoch from (clock_timestamp() - t0)) * 1000));
end;
$fn$;

comment on function public.freeze_hit_forecasts() is
  'Copy v_hit_forecasts_live into derived_hit_forecasts for every day both forecast tables still hold, replacing what was frozen for them; the days before are left as frozen (plan v2 P1.6 phase 2). Called once a night by common.refresh_feature_cache.';

revoke all on function public.freeze_hit_forecasts() from public, anon, authenticated;
grant execute on function public.freeze_hit_forecasts() to service_role;


create or replace function public.freeze_forecast_latest()
returns jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $fn$
declare
  t0 timestamptz := clock_timestamp();
  v_from    date;
  v_removed int;
  v_written int;
begin
  -- The oldest for_date the forecast table holds: from it on the table's days
  -- are whole (the prune cuts by date and the ingest writes nothing older -
  -- scripts/ingest_forecasts.py), and they replace what was frozen for them.
  select min(for_date) into v_from from weather_forecasts;
  if v_from is null then
    return jsonb_build_object('ok', true, 'rows_written', 0,
                              'note', 'weather_forecasts holds no row');
  end if;

  delete from derived_forecast_latest where for_date >= v_from;
  get diagnostics v_removed = row_count;

  -- Days that have passed: a day still ahead is read from the table.
  insert into derived_forecast_latest (city_key, for_date, model, lead_days, forecast_max_c, run_at, frozen_at)
  select distinct on (city_key, for_date, model, lead_days)
         city_key, for_date, model, lead_days, forecast_max_c, run_at, now()
    from weather_forecasts
   where forecast_max_c is not null
     and for_date < current_date
   order by city_key, for_date, model, lead_days, run_at desc;
  get diagnostics v_written = row_count;

  return jsonb_build_object(
    'ok', true, 'from', v_from, 'rows_replaced', v_removed, 'rows_written', v_written,
    'rows_total', (select count(*) from derived_forecast_latest),
    'ms', round(extract(epoch from (clock_timestamp() - t0)) * 1000));
end;
$fn$;

comment on function public.freeze_forecast_latest() is
  'Copy the forecast standing at each lead - the newest run per city, day, model and lead - into derived_forecast_latest for every day weather_forecasts holds that has passed, replacing what was frozen for them; the days before are left as frozen (plan v2 P1.6 phase 2). Called once a night by common.refresh_feature_cache.';

revoke all on function public.freeze_forecast_latest() from public, anon, authenticated;
grant execute on function public.freeze_forecast_latest() to service_role;


-- ---------------------------------------------------------------------------
-- THE MARKS (plan v2 P1.6 phase 3, step 3.1). Each band's YES edge at its eve
-- and pre-day cutoffs, copied once the cutoff is six hours past - edge_engine
-- stamps a run's rows with the moment the run began, so a row can land after
-- the cutoff it is before - and never rewritten after. A band with no YES
-- edge before a cutoff has nothing to copy, and both readers read nothing.
-- ---------------------------------------------------------------------------
create or replace function public.freeze_edge_marks()
returns jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $fn$
declare
  t0 timestamptz := clock_timestamp();
  v_written int;
begin
  insert into derived_edge_marks (band_id, mark, cutoff_at, computed_at, market_price, edge_id, source)
  select m.band_id, m.mark, m.cutoff_at, m.computed_at, m.market_price, m.edge_id, 'edges'
    from v_edge_marks_live m
   where m.cutoff_at < now() - interval '6 hours'
     and not exists (select 1 from derived_edge_marks d
                      where d.band_id = m.band_id and d.mark = m.mark)
  on conflict (band_id, mark) do nothing;
  get diagnostics v_written = row_count;

  return jsonb_build_object(
    'ok', true, 'rows_written', v_written,
    'rows_total', (select count(*) from derived_edge_marks),
    'ms', round(extract(epoch from (clock_timestamp() - t0)) * 1000));
end;
$fn$;

comment on function public.freeze_edge_marks() is
  'Copy each band''s YES edge at its eve and pre-day cutoffs (v_edge_marks_live) into derived_edge_marks once the cutoff is six hours past; a frozen mark is never rewritten (plan v2 P1.6 phase 3). Called once a night by common.refresh_feature_cache; the edges prune holds back every mark not copied yet.';

revoke all on function public.freeze_edge_marks() from public, anon, authenticated;
grant execute on function public.freeze_edge_marks() to service_role;
