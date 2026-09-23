-- ===========================================================================
-- ad4_88_hit_tournament.sql - WHAT WAS KNOWN THE EVENING BEFORE, AND WHAT PAID.
--
-- Safe to run any time. Two views. Changes no data.
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
    from public.fact_band_outcome o
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
  join public.fact_band_outcome o on o.city_key = d.city_key and o.for_date = d.for_date
  join public.v_canonical_bands cb on cb.band_id = o.band_id
  join public.v_canonical_markets cm on cm.market_id = cb.market_id
  left join public.v_verified_weather_outcomes v
         on v.city_key = d.city_key and v.for_date = d.for_date;

comment on view public.v_hit_ladders is
  'Every settled ladder bucket with the live price and market price as they stood at 18:00 local the evening before (plan v2.1 P3.8).';

create or replace view public.v_hit_forecasts as
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

comment on view public.v_hit_forecasts is
  'Per settled city-day and model: the newest forecast known by 18:00 local the evening before (lane asof), and previous-runs values at nominal lead 1 whose issue time is unverified (lane research, never promotes). Plan v2.1 P3.8.';

revoke all on public.v_hit_ladders   from public, anon, authenticated;
revoke all on public.v_hit_forecasts from public, anon, authenticated;
grant select on public.v_hit_ladders   to service_role;
grant select on public.v_hit_forecasts to service_role;
