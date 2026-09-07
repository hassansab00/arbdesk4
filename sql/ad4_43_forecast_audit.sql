-- ===========================================================================
-- ad4_43_forecast_audit.sql - WHY IS THE BOARD SHOWING THAT NUMBER?
--
-- THE PROBLEM THIS EXISTS FOR
-- ---------------------------
-- The board said New York would reach 87F. The day made about 76F and the
-- market settled 74-75F. Nothing on the page said where 87 came from, which
-- model said it, how far ahead it was issued, or that it was six degrees
-- above anything that city had actually done in three days.
--
-- A forecast is not one number. For any city-day there are usually several
-- rows in weather_forecasts - one per model, one per lead, one per run - and
-- the board picks ONE of them. Which one it picks, and how far the others sit
-- from it, is the difference between "the model was wrong" and "the view read
-- the wrong row", and those need completely different fixes.
--
-- WHAT THIS ANSWERS, per city, in one query:
--
--   what the board is showing, and which row that is
--   every other row available for the same day, and what each says
--   how far apart the models are
--   what has actually been observed today, and over three days
--   what is normal for this city at this time of year
--   a verdict, in words, naming the most likely cause
--
-- Nothing here changes a value. It is a read-only second opinion on the one
-- number every other page is built on.
--
-- RUN ORDER: after ad4_17_city_stats.sql. Re-runnable. Creates views only.
-- ===========================================================================

drop view if exists v_forecast_audit cascade;
drop view if exists v_forecast_candidates cascade;

-- --------------------------------------------------------------------------
-- 1. Every forecast row that exists for each city's local today, with the
--    reason the board did or did not choose it.
--
--    The board's rule is `distinct on (city_key) order by lead_days asc,
--    run_at desc` - the SHORTEST LEAD, not the newest run. That rule was
--    itself a fix: `run_at desc` once picked a week-old lead-7 row and showed
--    Chicago at 98F. Stating the rule here, next to the rows it chooses
--    between, is how it stays checkable.
-- --------------------------------------------------------------------------
create view v_forecast_candidates as
select
  f.city_key,
  cl.local_today                                          as for_date,
  f.model,
  f.lead_days,
  f.run_at,
  f.forecast_max_c,
  c.unit,
  band_local_value(f.forecast_max_c, c.unit)              as forecast_max_local,
  row_number() over (partition by f.city_key
                     order by f.lead_days asc nulls last, f.run_at desc) as rank,
  (row_number() over (partition by f.city_key
                      order by f.lead_days asc nulls last, f.run_at desc) = 1)
                                                          as board_uses_this,
  round(extract(epoch from (now() - f.run_at)) / 3600.0, 1) as run_age_hours
from weather_forecasts f
join cities c        on c.city_key = f.city_key
join v_city_climate cl on cl.city_key = f.city_key and f.for_date = cl.local_today
where f.forecast_max_c is not null;

comment on view v_forecast_candidates is
  'Every forecast row available for each city today, ranked the way v_city_stats ranks them - shortest lead first, then newest run. board_uses_this marks the one the board actually shows.';


-- --------------------------------------------------------------------------
-- 2. The audit: the chosen number against everything that could contradict it.
-- --------------------------------------------------------------------------
create view v_forecast_audit as
with chosen as (
  select * from v_forecast_candidates where board_uses_this
),
spread as (
  select city_key,
         count(*)::int                     as n_rows,
         count(distinct model)::int        as n_models,
         min(forecast_max_c)               as coldest_c,
         max(forecast_max_c)               as warmest_c,
         max(forecast_max_c) - min(forecast_max_c) as spread_c
    from v_forecast_candidates
   group by city_key
),
-- What the city has actually DONE. Local day, because a daily maximum is a
-- local-calendar quantity - that is what the market settles on.
observed as (
  select o.city_key,
         max(o.temp_c) filter (
           where (o.valid_at at time zone coalesce(c.timezone, 'UTC'))::date
               = (now() at time zone coalesce(c.timezone, 'UTC'))::date)   as today_max_c,
         count(*) filter (
           where (o.valid_at at time zone coalesce(c.timezone, 'UTC'))::date
               = (now() at time zone coalesce(c.timezone, 'UTC'))::date)::int as today_readings,
         max(o.temp_c) filter (where o.valid_at > now() - interval '3 days') as max_3d_c,
         max(o.temp_c) filter (where o.valid_at > now() - interval '14 days') as max_14d_c
    from weather_observations o
    join cities c on c.city_key = o.city_key
   where o.temp_c is not null and o.valid_at > now() - interval '14 days'
   group by o.city_key
)
select
  ch.city_key,
  ch.for_date,
  ch.unit,

  -- what the board shows, in the unit the market is quoted in
  round(ch.forecast_max_local, 1)                         as board_shows,
  ch.model                                                as board_model,
  ch.lead_days                                            as board_lead_days,
  ch.run_at                                               as board_run_at,
  ch.run_age_hours,

  -- what else was available
  sp.n_rows, sp.n_models,
  round(band_local_value(sp.coldest_c, ch.unit), 1)       as coldest_available,
  round(band_local_value(sp.warmest_c, ch.unit), 1)       as warmest_available,
  round(band_local_value(sp.spread_c, ch.unit)
        - band_local_value(0, ch.unit), 1)                as model_spread,

  -- what has actually happened
  round(band_local_value(ob.today_max_c, ch.unit), 1)     as observed_today,
  ob.today_readings,
  round(band_local_value(ob.max_3d_c, ch.unit), 1)        as observed_max_3d,
  round(band_local_value(ob.max_14d_c, ch.unit), 1)       as observed_max_14d,
  round(band_local_value(cl.normal_max_c, ch.unit), 1)    as normal_for_today,
  round(cl.sd_max_c, 2)                                   as sd_c,

  -- how far the board's number is from what the city normally does, in that
  -- city's own terms. Degrees are not comparable between Chicago and Beirut.
  case when cl.sd_max_c is null or cl.sd_max_c = 0 then null
       else round((ch.forecast_max_c - cl.normal_max_c) / cl.sd_max_c, 1) end
                                                          as sigma_from_normal,

  -- THE VERDICT, in the order that actually decides it.
  case
    when ob.today_readings is null or ob.today_readings = 0
      then 'No observation today to check it against. If the market has already settled far from this number, the observation feed is the thing that is broken, not the forecast.'
    when ch.run_age_hours > 30
      then format('STALE: this row was issued %s hours ago and is still the shortest lead available. The forecast jobs have stopped - nothing newer has been written for today.', round(ch.run_age_hours))
    when ch.lead_days is null or ch.lead_days > 1
      then format('LONG LEAD: the best row for today was issued %s day(s) ahead. A same-day forecast has not been written - P1.5 (or P1.3) has not run today.', ch.lead_days)
    when cl.sd_max_c is not null and cl.sd_max_c > 0
     and abs(ch.forecast_max_c - cl.normal_max_c) / cl.sd_max_c > 3
      then format('IMPLAUSIBLE: %s sigma from this city''s normal. A forecast that extreme is almost always a unit or a parsing fault, not weather.',
                  round((ch.forecast_max_c - cl.normal_max_c) / cl.sd_max_c, 1))
    when ob.max_3d_c is not null and ch.forecast_max_c - ob.max_3d_c > 4
      then format('SUSPECT: %s degrees above anything this city has done in three days.',
                  round(band_local_value(ch.forecast_max_c, ch.unit) - band_local_value(ob.max_3d_c, ch.unit), 1))
    when sp.spread_c is not null and sp.spread_c > 3
      then format('MODELS DISAGREE by %s degrees across %s row(s). The board shows one of them and says nothing about the others.',
                  round(band_local_value(sp.spread_c, ch.unit) - band_local_value(0, ch.unit), 1), sp.n_rows)
    when ob.today_max_c is not null
     and ob.today_max_c - ch.forecast_max_c > 2
      then 'The day has ALREADY exceeded the forecast. The number on the board is behind reality; the running maximum is the better read from here.'
    else 'Consistent with the record: within range of what this city does and of what the other rows say.'
  end                                                     as verdict
from chosen ch
left join spread sp    on sp.city_key = ch.city_key
left join observed ob  on ob.city_key = ch.city_key
left join v_city_climate cl on cl.city_key = ch.city_key;

comment on view v_forecast_audit is
  'One row per city: the forecast the board is showing, which model and lead it came from, every other row available, what has actually been observed, what is normal, and a verdict naming the most likely cause when the number looks wrong. Read this the moment a forecast looks off.';


-- --------------------------------------------------------------------------
-- 3. Grants. Read-only.
-- --------------------------------------------------------------------------
do $ad4$
declare o text; r text;
begin
  foreach o in array array['v_forecast_audit', 'v_forecast_candidates'] loop
    foreach r in array array['anon', 'authenticated', 'service_role'] loop
      if exists (select 1 from pg_roles where rolname = r) then
        execute format('grant select on %I to %I', o, r);
      end if;
    end loop;
  end loop;
end
$ad4$;

do $ad4$
declare v_bad int; v_all int;
begin
  select count(*) filter (where verdict not like 'Consistent%'), count(*)
    into v_bad, v_all from v_forecast_audit;
  raise notice 'ad4_43: % of % city forecast(s) look questionable.', v_bad, v_all;
end
$ad4$;

-- THE QUERY TO READ. Worst first.
select city_key, board_shows, unit, board_model, board_lead_days, run_age_hours,
       observed_today, observed_max_3d, normal_for_today, sigma_from_normal,
       n_rows, model_spread, verdict
  from v_forecast_audit
 order by (verdict not like 'Consistent%') desc, city_key;
