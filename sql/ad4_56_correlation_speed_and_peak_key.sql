-- ===========================================================================
-- ad4_56_correlation_speed_and_peak_key.sql
--
-- THE DAILY PIPELINE HAD NOT FINISHED SINCE 2026-09-08, AND SAID SO NOWHERE.
--
-- Safe to run any time. Creates two indexes, restores one unique index, and
-- changes no data.
--
-- Two separate faults, both ending in the same place - capacity.py stops, and
-- every table below it in the daily chain freezes:
--
--   derived_city_climate   5 days stale   City Clusters showed a 4-day-old
--                                         forecast because of this
--   derived_weather_peak   7 days stale
--
--
-- FAULT 1 - recompute_correlation() timed out over PostgREST.
--
-- Six consecutive runs of .github/workflows/pipeline_daily.yml died at
-- scripts/capacity.py line 27 with
--
--     500 Server Error for .../rest/v1/rpc/recompute_correlation
--
-- which the Postgres log records, at the same second, as `canceling statement
-- due to statement timeout`. The function is ONE top-level statement, so it
-- gets one timeout budget no matter what it does internally - the same
-- property documented at length in common.refresh_feature_cache.
--
-- Splitting it per city was the obvious fix and the wrong one. Measured with
-- EXPLAIN (ANALYZE, BUFFERS) against the live 342k-row archive, the pairwise
-- correlation - the part that looks expensive, joining every city against
-- every other - takes 100ms. The cost was somewhere else entirely:
--
--     Seq Scan on weather_forecasts                         4,694 ms
--       Filter: forecast_max_c IS NOT NULL AND lead_days = 1
--       Rows Removed by Filter: 293,640
--     HashAggregate over weather_observations               2,060 ms
--       Index Scan using wx_obs_valid, 226,911 rows, heap-fetching every one
--     the actual pairwise join                                100 ms
--                                                           --------
--                                                     total 7,055 ms
--
-- Seven of this table's indexes lead with city_key or for_date or run_at.
-- None leads with lead_days, so the one filter that throws away 86% of the
-- rows could not be used to find them, and the planner read all 342,605.
--
--   ad4_ix_fc_lead_date_city  puts lead_days first and carries for_date,
--   city_key and forecast_max_c, so the join reads the index and never the
--   heap. Partial on `forecast_max_c is not null` - the null rows are 86% of
--   the table and no caller of this index ever wants them.
--
--   ad4_ix_obs_valid_city_temp  carries the three columns the daily rollup
--   reads. wx_obs_valid holds valid_at alone, so 226k index entries each
--   cost a heap fetch to find out which city they belonged to.
--
-- Measured after, same call, same 1,299 rows: 7,330 ms -> 1,536 ms.
--
--
-- FAULT 2 - a unique index this repo deleted on purpose.
--
-- refresh_weather_peak() upserts with `on conflict (city_key, month)`, and
-- that needs a unique index on exactly those columns. ad4_00_preflight builds
-- one. ad4_50_index_dedupe then dropped it, because the table's primary key
-- is (city_key, month, computed_at) and rule 3 of that file retires an index
-- whose columns are a strict prefix of a longer one.
--
-- True of lookups, false of constraints, and the reason derived_weather_peak
-- had not been written since 2026-09-06:
--
--     42P10: there is no unique or exclusion constraint matching the
--     ON CONFLICT specification
--
-- ad4_50 no longer drops any unique index; this restores the casualty. Only
-- ONE table was affected - every other natural key in ad4_00's list survived,
-- checked directly against pg_index on the live database.
--
-- Restoring it is what put 609 rows back into derived_weather_peak, which
-- live_weather.minutes_to_peak, v_city_stats, v_trade_timing and strategy s7
-- had all been reading stale for a week.
-- ===========================================================================

-- --------------------------------------------------------------------------
-- 1. The two indexes recompute_correlation() needed.
-- --------------------------------------------------------------------------
do $ad4$
begin
  if to_regclass('public.weather_forecasts') is null then
    raise exception 'ad4_56 needs weather_forecasts - run sql/ad4_phase2.sql first';
  end if;
  if to_regclass('public.weather_observations') is null then
    raise exception 'ad4_56 needs weather_observations - run sql/ad4_phase2.sql first';
  end if;
end $ad4$;

create index if not exists ad4_ix_fc_lead_date_city
  on public.weather_forecasts (lead_days, for_date, city_key, forecast_max_c)
  where forecast_max_c is not null;

create index if not exists ad4_ix_obs_valid_city_temp
  on public.weather_observations (valid_at, city_key, temp_c);

analyze public.weather_forecasts;
analyze public.weather_observations;

-- --------------------------------------------------------------------------
-- 2. Put back the unique index ad4_50 removed.
--
-- ad4_ensure_natural_key() is defined by ad4_00_preflight. It dedupes first,
-- keeping the newest row per key, so this is re-runnable on a table that has
-- collected duplicates in the meantime - which is possible precisely because
-- the constraint was missing.
-- --------------------------------------------------------------------------
do $ad4$
declare v_msg text;
begin
  if to_regclass('public.derived_weather_peak') is null then
    raise notice 'ad4_56: no derived_weather_peak - nothing to key';
    return;
  end if;
  if not exists (select 1 from pg_proc p join pg_namespace n on n.oid = p.pronamespace
                  where n.nspname = 'public' and p.proname = 'ad4_ensure_natural_key') then
    raise exception 'ad4_56 needs ad4_ensure_natural_key() - run sql/ad4_00_preflight.sql first';
  end if;

  v_msg := ad4_ensure_natural_key('derived_weather_peak',
                                  array['city_key','month'], 'computed_at');
  raise notice 'ad4_56: %', v_msg;
  if v_msg like '%COULD NOT%' or v_msg like '%cannot key on%' then
    raise exception 'ad4_56: refresh_weather_peak() will keep failing 42P10 - %', v_msg;
  end if;
end $ad4$;

-- --------------------------------------------------------------------------
-- 3. refresh_weather_peak(), one city at a time.
--
-- Restoring the unique index made the upsert legal again, and left it slow:
-- measured on the live archive it takes between 5.0s and 17.4s for the same
-- 609 rows, depending on what else the database is doing. recompute_correlation
-- was cancelled at about 15s. Shipping this as one statement would be trading
-- a silent failure for a coin flip, which is not an improvement - and once
-- capacity.py stops swallowing the error, the coin flip is a red build.
--
-- Where the time goes, from EXPLAIN (ANALYZE, BUFFERS):
--
--     Incremental Sort over 519,022 rows                    5,272 ms
--     two WindowAggs over the same                            620 ms
--     the index scan that feeds them                          ~90 ms/city
--                                                           --------
--                                                     total 6,653 ms
--
-- No index helps. The sort key is (city_key, (valid_at at time zone
-- cities.timezone)::date, temp_c desc, valid_at) - an expression over a
-- column from the JOINED row, which no index on weather_observations can
-- provide. What makes it expensive is sorting three years of every city at
-- once, and the window functions only ever look WITHIN one city-day.
--
-- So the split costs nothing in correctness and removes the whole problem:
-- 54 statements of ~120ms, each with its own timeout budget, at any archive
-- size. Identical reasoning, and identical shape, to common.refresh_feature_cache
-- and to the per-city loop in refresh_feature_cache itself.
--
-- refresh_weather_peak() is left exactly as it was. It is what ad4_37's own
-- install block calls, it is correct, and on a small archive it is fine.
-- --------------------------------------------------------------------------
create or replace function refresh_weather_peak_city(p_city text, p_min_days int default 20)
returns int language plpgsql security definer as $ad4$
declare v_rows int;
begin
  with daily as (
    select
      o.city_key,
      (o.valid_at at time zone coalesce(c.timezone, 'UTC'))::date                          as local_date,
      extract(month from (o.valid_at at time zone coalesce(c.timezone, 'UTC')))::int       as month,
      extract(hour  from (o.valid_at at time zone coalesce(c.timezone, 'UTC')))
        + extract(minute from (o.valid_at at time zone coalesce(c.timezone, 'UTC'))) / 60.0 as local_hour,
      o.temp_c,
      count(*)      over (partition by (o.valid_at at time zone coalesce(c.timezone, 'UTC'))::date) as n_readings,
      row_number()  over (partition by (o.valid_at at time zone coalesce(c.timezone, 'UTC'))::date
                          order by o.temp_c desc, o.valid_at)                              as rk
    from weather_observations o
    join cities c on c.city_key = o.city_key
    where o.city_key = p_city
      and o.temp_c is not null
      and o.valid_at > now() - interval '3 years'
  ),
  peaks as (
    select city_key, month, local_date, local_hour
      from daily
     where rk = 1
       and n_readings >= 12
       -- A maximum at 02:00 is a front, not a diurnal peak.
       and local_hour between 8 and 22
  ),
  agg as (
    select
      city_key, month,
      count(*)::int                                            as n_days,
      percentile_cont(0.5)  within group (order by local_hour)  as p50,
      percentile_cont(0.10) within group (order by local_hour)  as p10,
      percentile_cont(0.90) within group (order by local_hour)  as p90
    from peaks
    group by city_key, month
    having count(*) >= p_min_days
  )
  insert into derived_weather_peak (city_key, month, peak_hour_local, window_width_h, n_days, computed_at)
  select a.city_key, a.month,
         round(a.p50::numeric, 2),
         -- Never zero: see ad4_37.
         greatest(round((a.p90 - a.p10)::numeric, 2), 1.0),
         a.n_days, now()
  from agg a
  on conflict (city_key, month) do update
    set peak_hour_local = excluded.peak_hour_local,
        window_width_h  = excluded.window_width_h,
        n_days          = excluded.n_days,
        computed_at     = excluded.computed_at;

  get diagnostics v_rows = row_count;
  return v_rows;
end;
$ad4$;

comment on function refresh_weather_peak_city(text, int) is
  'One city''s peak hour and window width per calendar month. Same measurement as refresh_weather_peak(), sliced so each call is its own top-level statement with its own timeout budget - the whole-archive version sorts 519k rows and takes 5-17s, which is inside Supabase''s statement timeout only some of the time.';

do $ad4$
declare r text;
begin
  foreach r in array array['authenticated', 'service_role'] loop
    if exists (select 1 from pg_roles where rolname = r) then
      execute format('grant execute on function refresh_weather_peak_city(text, int) to %I', r);
    end if;
  end loop;
end $ad4$;

-- --------------------------------------------------------------------------
-- 4. Prove the repair, rather than assert it.
--
-- Every `on conflict (cols)` in this schema needs a unique index on exactly
-- those columns. This checks the two that broke and names anything still
-- missing, so a half-applied install fails here instead of at 08:30 tomorrow
-- inside a job that prints one line to stderr and reports itself green.
-- --------------------------------------------------------------------------
do $ad4$
declare
  r       record;
  v_bad   text[] := '{}';
begin
  for r in
    select * from (values
      ('derived_weather_peak',    array['city_key','month']),
      ('derived_city_day_volume', array['city_key','trade_date'])
    ) as t(tbl, cols)
  loop
    if to_regclass('public.' || r.tbl) is null then continue; end if;
    if not exists (
      select 1 from pg_index i
       where i.indrelid = to_regclass('public.' || r.tbl)
         and i.indisunique and i.indpred is null
         and (select array_agg(a.attname::text order by a.attname)
                from unnest(i.indkey::int[]) k
                join pg_attribute a on a.attrelid = i.indrelid and a.attnum = k)
             = (select array_agg(c order by c) from unnest(r.cols) c))
    then
      v_bad := v_bad || format('%s(%s)', r.tbl, array_to_string(r.cols, ','));
    end if;
  end loop;

  if array_length(v_bad, 1) > 0 then
    raise exception 'ad4_56: still no unique index for on-conflict target(s): %',
                    array_to_string(v_bad, ', ');
  end if;
  raise notice 'ad4_56: on-conflict targets verified';
end $ad4$;
