-- ===========================================================================
-- ad4_88_hit_tournament.sql - WHAT WAS KNOWN THE EVENING BEFORE, AND WHAT PAID.
--
-- Safe to run any time. Three views and one table (P1.6 phase 2). Changes no
-- data.
--
-- The evidence for plan v2.1 P3.8, the hit tournament: every settled city-day
-- with exactly one winning bucket, the venue's ladder for it, and everything
-- the desk could have known at the checkpoint - 18:00 on the city's own clock,
-- the evening before (`d1_eve`, the first of P4.2's checkpoints).
--
--   v_hit_ladders    one row per bucket of every settled ladder: the
--                    canonical edges and unit (5,103 of 13,052 fact rows
--                    carry pre-correction edges, measured 23 Sep), which one
--                    paid, the verified maximum, and - at the cutoff - the
--                    live engine's price and the market's YES price
--   v_hit_forecasts  per settled city-day and model, the newest forecast
--                    known by the cutoff (lane 'asof') - and, apart, the
--                    previous-runs values at nominal lead 1 (lane 'research')
--
-- THE TWO LANES ARE NOT THE SAME EVIDENCE. 'asof' uses only forecasts whose
-- issue time is known and at or before the cutoff (P2.6: NWS's own
-- updateTime, or our fetch time). 'research' uses Open-Meteo previous-runs
-- values whose issue time nobody has verified (P2.6, P7.2): they show which
-- models tend to be right, and they may never promote a recipe.
--
-- Measured 23 Sep (rolled-back rehearsal): 798 settled city-days, 656 with a
-- verified maximum, 450 with a live price and 401 with a market price by the
-- cutoff; 'asof' forecasts: open_meteo_forecast 611 city-days in 51 cities,
-- nws 148 in 11; 'research': open_meteo_best_match 793. Service role only.
-- ===========================================================================

create or replace view public.v_hit_ladders as
with day as (
  select o.city_key, o.for_date,
         (((o.for_date - 1)::timestamp + interval '18 hours') at time zone c.timezone) as cutoff_at
    from public.v_fact_band_outcome_clean o
    join public.cities c using (city_key)
   group by o.city_key, o.for_date, c.timezone
  having count(*) filter (where o.settled_yes) = 1
)
select d.city_key, d.for_date, d.cutoff_at, o.band_id,
       cb.band_lo, cb.band_hi, cb.open_low, cb.open_high, cm.unit,
       o.settled_yes, v.observed_max_c,
       (select coalesce(bp.calibrated_prob, bp.raw_prob)
          from public.band_probabilities bp
         where bp.band_id = o.band_id and bp.computed_at <= d.cutoff_at
         order by bp.computed_at desc, bp.prob_id desc limit 1)          as live_prob,
       (select e.market_price
          from public.edges e
         where e.band_id = o.band_id and e.side = 'YES' and e.computed_at <= d.cutoff_at
         order by e.computed_at desc limit 1)                            as market_price
  from day d
  join public.v_fact_band_outcome_clean o on o.city_key = d.city_key and o.for_date = d.for_date
  join public.v_canonical_bands cb on cb.band_id = o.band_id
  join public.v_canonical_markets cm on cm.market_id = cb.market_id
  left join public.v_verified_weather_outcomes v
         on v.city_key = d.city_key and v.for_date = d.for_date;

comment on view public.v_hit_ladders is
  'Every settled ladder bucket with the live price and market price as they stood at 18:00 local the evening before (plan v2.1 P3.8).';

-- WHAT WAS KNOWN, KEPT PAST THE FORECASTS' KEEP (plan v2 P1.6 phase 2, step
-- 6 part (b), 29 Sep). hit_tournament.py reads 120 days of this; the forecast
-- tables keep 30-odd (step 5). v_hit_forecasts_live is the computation
-- itself; freeze_hit_forecasts (sql/ad4_97) copies it into
-- derived_hit_forecasts every night for every day both forecast tables still
-- hold, and v_hit_forecasts serves the live rows for those days and the
-- frozen rows for the days before them. The tables are pruned by whole
-- for_dates, so a day is either held whole or frozen.
create table if not exists public.derived_hit_forecasts (
  city_key       text        not null,
  for_date       date        not null,
  lane           text        not null,
  model          text        not null,
  forecast_max_c numeric,
  known_at       timestamptz not null,
  frozen_at      timestamptz not null default now(),
  primary key (city_key, for_date, lane, model, known_at)
);

comment on table public.derived_hit_forecasts is
  'v_hit_forecasts_live as it stood the last night both forecast tables held the day whole: what v_hit_forecasts serves for the days the forecast tables no longer hold (plan v2 P1.6 phase 2). Written by freeze_hit_forecasts.';

alter table public.derived_hit_forecasts enable row level security;
revoke all on public.derived_hit_forecasts from public, anon, authenticated;
grant select, insert, update, delete on public.derived_hit_forecasts to service_role;

create or replace view public.v_hit_forecasts_live as
with day as (
  select distinct city_key, for_date, cutoff_at from public.v_hit_ladders
)
select d.city_key, d.for_date, 'asof'::text as lane, f.model, f.forecast_max_c, f.issued_at as known_at
  from day d
  cross join lateral (
    select distinct on (i.model) i.model, i.forecast_max_c, i.issued_at
      from public.v_forecast_issued i
     where i.city_key = d.city_key and i.for_date = d.for_date
       and i.issued_at <= d.cutoff_at
       and i.issued_at_source in ('provider_update_time', 'ingest_time')
       and i.forecast_max_c is not null
     order by i.model, i.issued_at desc) f
union all
select d.city_key, d.for_date, 'asof', m.model, m.forecast_max_c, m.observed_at
  from day d
  cross join lateral (
    select distinct on (w.model) w.model, w.forecast_max_c, w.observed_at
      from public.weather_forecast_models w
     where w.city_key = d.city_key and w.for_date = d.for_date
       and w.source = 'open-meteo-models-current'
       and w.observed_at <= d.cutoff_at
     order by w.model, w.observed_at desc) m
union all
select d.city_key, d.for_date, 'research', f.model, f.forecast_max_c, f.run_at
  from day d
  join public.weather_forecasts f
    on f.city_key = d.city_key and f.for_date = d.for_date
   and f.source = 'open-meteo-previous-runs' and f.lead_days = 1
   and f.forecast_max_c is not null
union all
select d.city_key, d.for_date, 'research', w.model, w.forecast_max_c, w.run_at
  from day d
  join public.weather_forecast_models w
    on w.city_key = d.city_key and w.for_date = d.for_date
   and w.source = 'open-meteo-previous-runs' and w.lead_days = 1;

comment on view public.v_hit_forecasts_live is
  'v_hit_forecasts computed from the forecast tables as they stand: correct for every day both tables still hold. freeze_hit_forecasts copies it nightly; v_hit_forecasts serves it (plan v2 P1.6 phase 2).';

-- The oldest for_date both tables hold whole: from it on the live rows, before
-- it the frozen ones. greatest() skips an empty table; both empty, all frozen.
create or replace view public.v_hit_forecasts as
with held as (
  select greatest((select min(for_date) from public.weather_forecasts),
                  (select min(for_date) from public.weather_forecast_models)) as from_date
)
select l.city_key, l.for_date, l.lane, l.model, l.forecast_max_c, l.known_at
  from public.v_hit_forecasts_live l, held h
 where l.for_date >= coalesce(h.from_date, 'infinity'::date)
union all
select f.city_key, f.for_date, f.lane, f.model, f.forecast_max_c, f.known_at
  from public.derived_hit_forecasts f, held h
 where f.for_date < coalesce(h.from_date, 'infinity'::date);

comment on view public.v_hit_forecasts is
  'Per settled city-day and model: the newest forecast known by 18:00 local the evening before (lane asof), and previous-runs values at nominal lead 1 whose issue time is unverified (lane research, never promotes). Plan v2.1 P3.8. Live while both forecast tables hold the day, frozen (derived_hit_forecasts) before (plan v2 P1.6 phase 2).';

revoke all on public.v_hit_ladders   from public, anon, authenticated;
revoke all on public.v_hit_forecasts from public, anon, authenticated;
revoke all on public.v_hit_forecasts_live from public, anon, authenticated;
grant select on public.v_hit_ladders   to service_role;
grant select on public.v_hit_forecasts to service_role;
grant select on public.v_hit_forecasts_live to service_role;
