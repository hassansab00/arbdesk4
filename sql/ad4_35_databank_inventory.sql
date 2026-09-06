-- ===========================================================================
-- ad4_35_databank_inventory.sql - what the desk has actually collected, and
-- what it has built out of it.
--
-- WHY. The desk's one genuine asset is its archive: two years of station
-- observations, the forecasts made against them, the books that were quoted,
-- and the frozen record of who was right. Nothing in the app could show that.
-- The Analytics page had a small Data Bank block covering three FACT tables
-- and nothing about the raw collection underneath them - so "is the archive
-- any good" was a question you answered by typing count(*) into a SQL editor.
--
-- Two layers, kept apart on purpose:
--
--   COLLECTED  what came in from outside - observations, forecasts, books,
--              trades, market definitions. If this is thin, everything below
--              it is decoration.
--   SYNTHESISED what the desk computed from it - features, skill, the climb
--              profile, the caches, the frozen outcomes. These are derived,
--              so a stale one is not missing data, it is a job that has not
--              run, and the row says which job.
--
-- COST. These are aggregates over the biggest tables in the schema. Each one
-- is timed on install below and warns if it would be felt on a page load.
-- The per-day series is bounded to 90 days for the same reason: unbounded it
-- is two years x five datasets and no chart needs that.
--
-- Run order: any time after sql/ad4_00_preflight.sql. Re-runnable.
-- ===========================================================================

do $ad4$
declare v text;
begin
  foreach v in array array['v_archive_daily', 'v_archive_by_city',
                           'v_synthesis_inventory', 'v_archive_inventory'] loop
    execute format('drop view if exists %I cascade', v);
  end loop;
end
$ad4$;


-- --------------------------------------------------------------------------
-- 1. What came in from outside.
--
--    One row per dataset. `feeder` names the job that fills it, because
--    "0 rows" is only useful next to what to run about it.
-- --------------------------------------------------------------------------
create or replace view v_archive_inventory as
with parts as (
  select 'Station observations'::text as dataset, 1 as ord,
         'n8n P1.2 / P1.5, or Actions -> Observations'::text as feeder,
         count(*)::bigint as rows, count(distinct city_key)::int as cities,
         min(valid_at) as first_at, max(valid_at) as last_at
    from weather_observations
  union all
  select 'Forecasts', 2, 'n8n P1.3 / P1.4 / P1.5, or Actions -> Forecasts',
         count(*), count(distinct city_key), min(run_at), max(run_at)
    from weather_forecasts
  union all
  select 'Order books', 3, 'n8n P0.3 book snapshot',
         count(*), (select count(distinct m.city_key)
                      from book_snapshots s2
                      join bands b2 on b2.band_id = s2.band_id
                      join markets m on m.market_id = b2.market_id),
         min(observed_at), max(observed_at)
    from book_snapshots
  union all
  select 'Trades seen', 4, 'n8n P0.4 trade history',
         count(*), (select count(distinct m.city_key)
                      from trades_observed t2
                      join bands b3 on b3.band_id = t2.band_id
                      join markets m on m.market_id = b3.market_id),
         min(traded_at), max(traded_at)
    from trades_observed
  union all
  -- WHICH TIMESTAMP COLUMN markets HAS depends on when the database was
  -- built: the live one carries first_seen_at / last_seen_at from an early
  -- hand-run migration, a fresh install from ad4_00 carries created_at, and
  -- naming either one directly makes this view un-installable on the other.
  -- Both were caught by installing every file onto an empty database and then
  -- onto a populated one. Reading through to_jsonb names none of them.
  select 'Markets', 5, 'n8n P0.2 market discovery',
         count(*), count(distinct city_key),
         min(coalesce((to_jsonb(m) ->> 'first_seen_at')::timestamptz,
                      (to_jsonb(m) ->> 'created_at')::timestamptz)),
         max(coalesce((to_jsonb(m) ->> 'last_seen_at')::timestamptz,
                      (to_jsonb(m) ->> 'first_seen_at')::timestamptz,
                      (to_jsonb(m) ->> 'created_at')::timestamptz))
    from markets m
  union all
  select 'Weather events', 6, 'n8n P1.2 / Actions -> Live Weather',
         count(*), count(distinct city_key), min(detected_at), max(detected_at)
    from weather_events
)
select
  p.dataset,
  p.ord,
  p.feeder,
  p.rows,
  p.cities,
  p.first_at,
  p.last_at,
  case when p.first_at is null then null
       else greatest(1, (p.last_at::date - p.first_at::date) + 1) end          as days_covered,
  case when p.first_at is null or p.rows = 0 then null
       else round(p.rows::numeric
                  / greatest(1, (p.last_at::date - p.first_at::date) + 1), 1)
  end                                                                          as rows_per_day,
  case when p.last_at is null then null
       else round(extract(epoch from (now() - p.last_at)) / 3600.0, 1) end      as hours_since,
  case
    when p.rows = 0                                        then 'EMPTY'
    when p.last_at is null                                 then 'NO TIMESTAMP'
    when p.last_at > now() - interval '6 hours'            then 'CURRENT'
    when p.last_at > now() - interval '48 hours'           then 'LAGGING'
    else                                                        'STALE'
  end                                                                          as freshness
from parts p
order by p.ord;

comment on view v_archive_inventory is
  'What the desk has actually collected from outside, per dataset, with the job that fills it. "0 rows" is only useful next to what to run about it.';


-- --------------------------------------------------------------------------
-- 2. What the desk built out of it.
--
--    Everything here is DERIVED. A stale row is not missing data - it is a
--    job that has not run, and it names the job.
-- --------------------------------------------------------------------------
-- ADAPTIVE. Some of these tables only exist once their own SQL file has been
-- run (derived_climb_profile comes from ad4_28, for instance), and a view that
-- names a missing relation cannot be created at all - which would take the
-- whole page down over one un-run file. So the row list is assembled from the
-- relations that actually exist, and a layer that has never been installed
-- says so instead of vanishing.
do $ad4$
declare
  v_parts text[] := array[]::text[];
  r record;
begin
  for r in
    select * from (values
      ('Day features',            1, 'per city-day: max, range, cloud, wind, the shape of the day',
       'sql/ad4_28 refresh_feature_cache, or Actions -> Feature Cache', 'weather_forecast_features', 'captured_at'),
      ('Forecast skill',          2, 'per city and lead: mean error, bias, how often it lands in the right band',
       'Actions -> Measure Skill', 'derived_forecast_skill', 'computed_at'),
      ('Climb profile',           3, 'per city and local hour: how much this city HISTORICALLY still climbs',
       'sql/ad4_28 refresh_feature_cache', 'derived_climb_profile', 'computed_at'),
      ('Peak hour',               4, 'per city and month: when the maximum is actually made, and how wide the window is',
       'sql/ad4_37 refresh_weather_peak, via Actions -> Derived Recompute', 'derived_weather_peak', 'computed_at'),
      ('City correlation',        5, 'which cities move together - ten positions across correlated cities is not ten bets',
       'Actions -> Capacity', 'derived_city_correlation', 'computed_at'),
      ('Frozen band outcomes',    6, 'what was predicted against what settled - the only asset nobody else has',
       'Actions -> Data Bank', 'fact_band_outcome', 'captured_at'),
      ('Frozen forecast outcomes',7, 'every forecast against the observation that settled it',
       'Actions -> Data Bank', 'fact_forecast_outcome', 'captured_at'),
      ('Frozen signal outcomes',  8, 'every signal against whether it filled and what it made',
       'Actions -> Data Bank', 'fact_signal_outcome', 'captured_at')
    ) as t(layer, ord, what, builder, rel, ts_col)
  loop
    if to_regclass('public.' || r.rel) is null then
      -- Never installed. Say so; do not leave a hole where a layer should be.
      v_parts := v_parts || format(
        'select %L::text, %s::int, %L::text, %L::text, 0::bigint, null::timestamptz, false',
        r.layer, r.ord, r.what, r.builder);
    elsif not exists (select 1 from information_schema.columns
                       where table_schema = 'public' and table_name = r.rel
                         and column_name = r.ts_col) then
      v_parts := v_parts || format(
        'select %L::text, %s::int, %L::text, %L::text, (select count(*) from %I)::bigint, null::timestamptz, true',
        r.layer, r.ord, r.what, r.builder, r.rel);
    else
      v_parts := v_parts || format(
        'select %L::text, %s::int, %L::text, %L::text, (select count(*) from %I)::bigint, (select max(%I) from %I), true',
        r.layer, r.ord, r.what, r.builder, r.rel, r.ts_col, r.rel);
    end if;
  end loop;

  execute format($v$
    create or replace view v_synthesis_inventory as
    with parts(layer, ord, what, builder, rows, last_at, installed) as (%s)
    select
      p.layer, p.ord, p.what, p.builder, p.rows, p.last_at, p.installed,
      case when p.last_at is null then null
           else round(extract(epoch from (now() - p.last_at)) / 3600.0, 1) end   as hours_since,
      case
        when not p.installed                         then 'NOT INSTALLED'
        when p.rows = 0                              then 'NEVER BUILT'
        when p.last_at is null                       then 'BUILT, NO TIMESTAMP'
        when p.last_at > now() - interval '36 hours' then 'CURRENT'
        when p.last_at > now() - interval '8 days'   then 'AGEING'
        else                                              'STALE'
      end                                                                        as freshness
    from parts p
    order by p.ord $v$, array_to_string(v_parts, ' union all '));
end
$ad4$;

comment on view v_synthesis_inventory is
  'What the desk computed from the archive, and which job builds each layer. Everything here is derived, so a stale row is a job that has not run rather than data that is missing.';


-- --------------------------------------------------------------------------
-- 3. Per city, for the two datasets where per-city completeness decides
--    whether that city is tradeable at all.
--
--    A city with no forward forecast has no price to disagree with, and a
--    city whose last observation is two days old cannot be timed. Both are
--    invisible in a total.
-- --------------------------------------------------------------------------
create or replace view v_archive_by_city as
with obs as (
  select city_key, count(*)::bigint as n, min(valid_at) as first_at, max(valid_at) as last_at,
         count(distinct (valid_at at time zone 'UTC')::date)::int as days
    from weather_observations group by city_key
),
fc as (
  select city_key, count(*)::bigint as n, max(run_at) as last_at,
         count(*) filter (where for_date >= current_date)::bigint as forward
    from weather_forecasts group by city_key
),
bk as (
  select m.city_key, count(*)::bigint as n, max(s.observed_at) as last_at
    from book_snapshots s
    join bands b   on b.band_id = s.band_id
    join markets m on m.market_id = b.market_id
   group by m.city_key
)
select
  c.city_key,
  c.display_name,
  coalesce(c.status, 'active')                                     as status,
  coalesce(o.n, 0)                                                 as observations,
  o.first_at                                                       as obs_first_at,
  o.last_at                                                        as obs_last_at,
  coalesce(o.days, 0)                                              as obs_days,
  case when o.last_at is null then null
       else round(extract(epoch from (now() - o.last_at)) / 3600.0, 1) end as obs_age_h,
  -- Readings per observed day. Under about 12 the day's maximum is understated
  -- and every feature built on it inherits that.
  case when coalesce(o.days, 0) = 0 then null
       else round(o.n::numeric / o.days, 1) end                    as readings_per_day,
  coalesce(f.n, 0)                                                 as forecasts,
  coalesce(f.forward, 0)                                           as forecasts_forward,
  f.last_at                                                        as forecast_last_at,
  coalesce(k.n, 0)                                                 as book_snapshots,
  k.last_at                                                        as book_last_at,
  case
    when coalesce(o.n, 0) = 0                     then 'no observations at all'
    when o.last_at < now() - interval '24 hours'  then 'observations have stopped'
    when coalesce(f.forward, 0) = 0               then 'no forward forecast - nothing to price against'
    when coalesce(o.n, 0) / greatest(o.days, 1) < 12
                                                  then 'sparsely observed - the daily maximum is understated'
    when coalesce(k.n, 0) = 0                     then 'no book has ever been snapshotted'
    else                                               'complete'
  end                                                              as verdict
from cities c
left join obs o on o.city_key = c.city_key
left join fc  f on f.city_key = c.city_key
left join bk  k on k.city_key = c.city_key
order by c.city_key;

comment on view v_archive_by_city is
  'Per-city completeness. A city with no forward forecast has no price to disagree with, and a sparsely observed one has an understated daily maximum - neither is visible in a total.';


-- --------------------------------------------------------------------------
-- 4. Rows per day, last 90 days. Bounded on purpose: unbounded this is two
--    years across five datasets and no chart needs that.
-- --------------------------------------------------------------------------
create or replace view v_archive_daily as
select 'Station observations'::text as dataset,
       (valid_at at time zone 'UTC')::date as day,
       count(*)::bigint as rows,
       count(distinct city_key)::int as cities
  from weather_observations
 where valid_at >= current_date - 90
 group by 2
union all
select 'Forecasts', (run_at at time zone 'UTC')::date, count(*), count(distinct city_key)
  from weather_forecasts
 where run_at >= current_date - 90
 group by 2
union all
select 'Order books', (observed_at at time zone 'UTC')::date, count(*), 0
  from book_snapshots
 where observed_at >= current_date - 90
 group by 2
union all
select 'Trades seen', (traded_at at time zone 'UTC')::date, count(*), 0
  from trades_observed
 where traded_at >= current_date - 90
 group by 2;

comment on view v_archive_daily is
  'Rows per day for the last 90 days, per dataset. The shape of this series is how an outage looks: a flat stretch is a job that stopped, not a quiet week.';


-- --------------------------------------------------------------------------
-- 5. Grants.
-- --------------------------------------------------------------------------
do $ad4$
declare o text; r text;
begin
  foreach o in array array['v_archive_inventory', 'v_synthesis_inventory',
                           'v_archive_by_city', 'v_archive_daily'] loop
    foreach r in array array['anon', 'authenticated', 'service_role'] loop
      if exists (select 1 from pg_roles where rolname = r) then
        execute format('grant select on %I to %I', o, r);
      end if;
    end loop;
  end loop;
end
$ad4$;


do $ad4$
declare v text; t0 timestamptz; ms numeric; n bigint;
begin
  foreach v in array array['v_archive_inventory', 'v_synthesis_inventory',
                           'v_archive_by_city', 'v_archive_daily'] loop
    t0 := clock_timestamp();
    execute format('select count(*) from %I', v) into n;
    ms := round(extract(epoch from (clock_timestamp() - t0)) * 1000);
    raise notice 'ad4_35: % - % row(s), % ms', v, n, ms;
    if ms > 800 then
      raise warning 'ad4_35: % took % ms. It aggregates the whole archive; the Data Bank page polls it every five minutes, but if this grows past a couple of seconds it wants a cache like sql/ad4_28.', v, ms;
    end if;
  end loop;
end
$ad4$;
