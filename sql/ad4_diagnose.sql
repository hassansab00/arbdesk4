-- ===========================================================================
-- ad4_diagnose.sql - READ-ONLY. Answers "why does the platform say that?"
--
-- Nothing here writes. Paste the whole file into the Supabase SQL editor and
-- read the grid. Every row is (section, item, value, what it means), so the
-- answer to a specific complaint is a row, not a paragraph.
--
-- It is built to survive a partially-installed database: every lookup is
-- guarded, so a missing view produces a row saying which file creates it
-- rather than aborting the query.
--
-- Sections:
--   1 OBJECTS      which ad4 objects exist, and which file creates the ones
--                  that do not
--   2 FRESHNESS    the newest row in each feed, and how old it is
--   3 TEMPERATURE  per city: what the platform is SHOWING as today, where
--                  that number came from, and what was actually observed
--   4 BUCKETS      how many buckets each market has, against the widest
--                  ladder on the same day
--   5 TRADING      why nothing is tradeable, counted by reason
-- ===========================================================================

with
-- ---------------------------------------------------------------- 1 -------
objects as (
  select * from (values
    ('cities',                      'sql/ad4_00_preflight.sql'),
    ('bands',                       'sql/ad4_00_preflight.sql'),
    ('book_snapshots',              'sql/ad4_00_preflight.sql'),
    ('trades_observed',             'Phase 0 / n8n P0.4'),
    ('weather_observations',        'sql/ad4_00_preflight.sql'),
    ('weather_forecasts',           'sql/ad4_00_preflight.sql'),
    ('live_weather',                'sql/ad4_live_weather.sql'),
    ('v_opportunities',             'sql/ad4_phase2_ranking.sql'),
    ('v_latest_book',               'sql/ad4_13_reconcile.sql'),
    ('v_city_stats',                'sql/ad4_17_city_stats.sql'),
    ('derived_city_climate',        'sql/ad4_19_stats_cache.sql'),
    ('v_city_day_features',         'sql/ad4_21_weather_features.sql'),
    ('v_persistence_skill',         'sql/ad4_21_weather_features.sql'),
    ('derived_weather_model',       'sql/ad4_21_weather_features.sql'),
    ('v_opportunity_context',       'sql/ad4_22_opportunity_context.sql'),
    ('v_city_reasoning',            'sql/ad4_23_reasoning.sql'),
    ('weather_forecast_features',   'sql/ad4_24_nws_gridpoint.sql'),
    ('v_forecast_features',         'sql/ad4_24_nws_gridpoint.sql'),
    ('v_condition_skill',           'sql/ad4_24_nws_gridpoint.sql (needs ad4_21)'),
    ('derived_model_forecast',      'sql/ad4_25_model_forecast.sql'),
    ('v_model_disagreement',        'sql/ad4_25_model_forecast.sql'),
    ('v_model_forecast_skill',      'sql/ad4_25_model_forecast.sql (needs ad4_21)')
  ) as t(obj, src)
),
sec1 as (
  select 1 as sec, '1 OBJECTS' as section, o.obj as item,
         case when to_regclass('public.' || o.obj) is null then 'MISSING' else 'ok' end as value,
         case when to_regclass('public.' || o.obj) is null
              then 'run ' || o.src else o.src end as meaning
  from objects o
),

-- ---------------------------------------------------------------- 2 -------
sec2 as (
  select 2, '2 FRESHNESS', f.item, f.value, f.meaning from (
    select 'weather_observations (IEM)' as item,
           coalesce(to_char(max(valid_at), 'YYYY-MM-DD HH24:MI'), 'none') as value,
           coalesce('age ' || date_trunc('minute', now() - max(valid_at))::text,
                    'no rows - Actions -> Observations') as meaning
      from weather_observations where source = 'IEM'
    union all
    select 'weather_observations (NWS)',
           coalesce(to_char(max(valid_at), 'YYYY-MM-DD HH24:MI'), 'none'),
           coalesce('age ' || date_trunc('minute', now() - max(valid_at))::text,
                    'no rows - n8n P1.2 has never written')
      from weather_observations where source = 'NWS'
    union all
    select 'live_weather',
           coalesce(to_char(max(observed_at), 'YYYY-MM-DD HH24:MI'), 'none'),
           coalesce('age ' || date_trunc('minute', now() - max(observed_at))::text,
                    'no rows - Actions -> Live Weather, or n8n P1.2')
      from live_weather
    union all
    select 'weather_forecasts',
           coalesce(to_char(max(run_at), 'YYYY-MM-DD HH24:MI'), 'none'),
           coalesce('newest model run, age ' || date_trunc('hour', now() - max(run_at))::text,
                    'no rows - Actions -> Forecasts')
      from weather_forecasts
    union all
    select 'book_snapshots',
           coalesce(to_char(max(observed_at), 'YYYY-MM-DD HH24:MI'), 'none'),
           coalesce('age ' || date_trunc('minute', now() - max(observed_at))::text,
                    'no rows - n8n P0.3. Without this NOTHING has a price.')
      from book_snapshots
    union all
    select 'trades_observed',
           coalesce(to_char(max(observed_at), 'YYYY-MM-DD HH24:MI'), 'none'),
           coalesce('age ' || date_trunc('minute', now() - max(observed_at))::text,
                    'no rows - n8n P0.4. Every volume figure reads $0.')
      from trades_observed
  ) f
),

-- ---------------------------------------------------------------- 3 -------
-- What the UI shows as "Today" is coalesce(forecast_max_c, running_max_c),
-- converted to the city's unit. If that number looks wrong, exactly one of
-- three things is true and this section says which: the forecast row is
-- stale, the forecast row is for a different day, or the observed archive
-- disagrees with it.
tz as (
  select city_key, coalesce(timezone, 'UTC') as tzname,
         coalesce(unit, 'C') as unit,
         (now() at time zone coalesce(timezone, 'UTC'))::date as local_today
    from cities
),
fc as (
  select distinct on (f.city_key)
         f.city_key, f.forecast_max_c, f.model, f.lead_days, f.run_at, f.for_date
    from weather_forecasts f
    join tz on tz.city_key = f.city_key and f.for_date = tz.local_today
   where f.forecast_max_c is not null
   order by f.city_key, f.run_at desc
),
obs as (
  select o.city_key,
         (o.valid_at at time zone tz.tzname)::date as d,
         max(o.temp_c) as max_c,
         count(*) as n
    from weather_observations o
    join tz on tz.city_key = o.city_key
   where o.valid_at > now() - interval '4 days' and o.temp_c is not null
   group by 1, 2
),
obs_roll as (
  select city_key,
         max(max_c) filter (where d = (select local_today from tz t2 where t2.city_key = obs.city_key)) as today_c,
         max(max_c) as max_3d_c
    from obs group by city_key
),
sec3 as (
  select 3, '3 TEMPERATURE', tz.city_key as item,
         -- shown exactly as the UI computes it, in the city's own unit
         case when coalesce(fc.forecast_max_c, lw.running_max_c) is null then 'no figure'
              when tz.unit = 'F' then
                round(coalesce(fc.forecast_max_c, lw.running_max_c) * 9 / 5 + 32, 1)::text || 'F'
              else round(coalesce(fc.forecast_max_c, lw.running_max_c), 1)::text || 'C'
         end as value,
         concat_ws(' · ',
           case when fc.forecast_max_c is not null
                then 'from forecast ' || coalesce(fc.model, '?')
                     || ' lead ' || coalesce(fc.lead_days::text, '?')
                     || ', run ' || date_trunc('hour', now() - fc.run_at)::text || ' ago'
                when lw.running_max_c is not null then 'NO forecast for the local day - showing the running max instead'
                else 'no forecast and no live reading' end,
           'observed today ' || coalesce(round(o.today_c, 1)::text, 'none'),
           'observed max last 3 days ' || coalesce(round(o.max_3d_c, 1)::text, 'none'),
           case when fc.forecast_max_c is not null and o.max_3d_c is not null
                     and fc.forecast_max_c - o.max_3d_c > 4
                then 'FORECAST IS >4C ABOVE ANYTHING OBSERVED IN 3 DAYS - suspect'
                else null end
         ) as meaning
    from tz
    left join fc on fc.city_key = tz.city_key
    left join live_weather lw on lw.city_key = tz.city_key
    left join obs_roll o on o.city_key = tz.city_key
),

-- ---------------------------------------------------------------- 4 -------
buckets as (
  select m.market_id, m.city_key, m.resolution_date, count(b.band_id) as n
    from markets m left join bands b on b.market_id = m.market_id
   where m.resolution_date >= current_date - 1
   group by 1, 2, 3
),
widest as (select resolution_date, max(n) as w from buckets group by 1),
sec4 as (
  select 4, '4 BUCKETS', b.city_key || ' ' || b.resolution_date as item,
         b.n::text || ' buckets' as value,
         case when b.n = 0 then 'no bands at all - n8n P0.2 Market Discovery has not written this market'
              when b.n < w.w then 'widest ladder this day is ' || w.w
                                  || ' - this market is SHORT, re-run n8n P0.2'
              else 'matches the widest ladder on this day' end as meaning
    from buckets b join widest w on w.resolution_date = b.resolution_date
),

-- ---------------------------------------------------------------- 5 -------
sec5 as (
  select 5, '5 TRADING', s.item, s.value, s.meaning from (
    select 'strategies enabled' as item,
           count(*) filter (where enabled)::text || ' of ' || count(*)::text as value,
           case when count(*) filter (where enabled) = 0
                then 'ALL DISABLED - the Signal Engine cannot fire a single trade signal. update strategies set enabled = true where ...'
                else 'ok' end as meaning
      from strategies
    union all
    select 'signals last 24h',
           count(*)::text,
           case when count(*) = 0 then 'none - needs enabled strategies AND priced bands' else 'ok' end
      from signals where fired_at > now() - interval '24 hours'
  ) s
)

select section, item, value, meaning from (
  select * from sec1 union all select * from sec2 union all select * from sec3
  union all select * from sec4 union all select * from sec5
) all_rows
order by sec, item;
