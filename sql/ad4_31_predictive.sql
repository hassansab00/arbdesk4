-- ===========================================================================
-- ad4_31_predictive.sql - the Predictive section's data layer.
--
-- ONE QUESTION, ASKED BOTH WAYS. Forward: what does the desk expect, which
-- bucket does that land in, and what is the market charging for it. Backward:
-- when it expected that before, was it right - and did being right pay.
--
-- Those are the same numbers read in two directions, so they come from the
-- same views rather than from two calculations that can drift apart.
--
-- THE RULE THIS FILE OBEYS. A page load may not scan the archive (ad4_19,
-- ad4_28). Every view here is bounded by date at the point it reads a table:
-- fact_forecast_outcome is one row per city-day-model-lead and
-- weather_forecasts is filtered to a window, so none of these is O(history).
-- Measured at the bottom of the file; if any of them ever crosses 200 ms on
-- your data, cache it the way ad4_28 caches the climb profile.
--
-- Run order: after sql/ad4_18_databank.sql. Re-runnable.
-- ===========================================================================

do $ad4$
begin
  if to_regclass('public.fact_forecast_outcome') is null then
    raise exception 'ad4_31 needs the data bank - run sql/ad4_18_databank.sql first';
  end if;
end
$ad4$;


-- --------------------------------------------------------------------------
-- 1. THE CONVERGENCE FUNNEL. One row per (city, day, model, lead).
--
--    A forecast is not one number, it is a sequence: what each model said
--    seven days out, then six, then one. Averaging those together hides the
--    only thing worth knowing - whether the estimate walked toward the truth
--    as the day approached, or wandered. A model that is sharp at lead 1 and
--    useless at lead 5 looks identical to a mediocre one when the leads are
--    pooled, and they are not the same model to trade.
--
--    Past and future in one shape, deliberately: observed_max_c is null for a
--    day that has not happened, which is exactly how a page should draw it.
-- --------------------------------------------------------------------------
create or replace view v_forecast_convergence as
with latest as (
  -- The forecast STANDING at that lead, which is the one that could have been
  -- acted on. A later re-run of the same lead is hindsight.
  select distinct on (city_key, for_date, model, lead_days)
         city_key, for_date, model, lead_days, forecast_max_c, run_at
    from weather_forecasts
   where for_date >= current_date - 45
     and for_date <= current_date + 16
     and forecast_max_c is not null
   order by city_key, for_date, model, lead_days, run_at desc
),
observed as (
  select city_key, for_date, max(observed_max_c) as observed_max_c
    from fact_forecast_outcome
   where for_date >= current_date - 45
   group by 1, 2
)
select
  l.city_key,
  l.for_date,
  l.model,
  l.lead_days,
  l.forecast_max_c,
  l.run_at,
  o.observed_max_c,
  -- observed minus forecast: positive means the day came in hotter than said
  case when o.observed_max_c is not null
       then round(o.observed_max_c - l.forecast_max_c, 2) end as error_c,
  (l.for_date < current_date)                                  as is_past,
  (o.observed_max_c is not null)                               as is_settled
from latest l
left join observed o
  on o.city_key = l.city_key and o.for_date = l.for_date;

comment on view v_forecast_convergence is
  'What every model said at every lead, beside what happened. Past and future in one shape - observed_max_c is null for a day that has not happened. This is the series the Predictive page draws in three dimensions: lead on one axis, temperature on another, model on the third.';


-- --------------------------------------------------------------------------
-- 2. THE BUCKET LADDER, so a temperature can be read as a market.
--
--    A forecast of 22.4 C means nothing on its own. It means something the
--    moment you know the ladder is 21-22, 22-23, 23-24 - because then it is
--    a bucket, a price and an edge. This joins the two.
-- --------------------------------------------------------------------------
create or replace view v_prediction_ladder as
select
  m.city_key,
  m.resolution_date                                    as for_date,
  m.market_id,
  b.band_id,
  -- Position on the ladder, computed rather than read.
  --
  -- This used to select b.band_index, a column that EXISTS in the production
  -- database and is created by no SQL file in this repo - so this whole view
  -- failed to install on a fresh one, taking the Predictive page down with it.
  -- band_lo is always present and is the same ordering; the open low tail has
  -- a null floor and sorts first, which is where it belongs.
  (row_number() over (partition by m.market_id order by b.band_lo nulls first))::int - 1
                                                       as band_index,
  b.band_label,
  b.band_lo,
  b.band_hi,
  b.open_low,
  b.open_high,
  m.closed,
  m.settled_value,
  (m.winning_band_id = b.band_id)                      as won,
  p.raw_prob,
  p.calibrated_prob,
  coalesce(p.calibrated_prob, p.raw_prob)              as model_prob,
  p.forecast_max_c,
  p.sigma_c,
  p.confidence,
  p.regime_label,
  e.side,
  e.market_price,
  e.edge_pp,
  e.edge_net_pp,
  e.fillable_usd_5c                                    as depth_5c,
  e.tradeable,
  e.block_reason,
  e.computed_at                                        as edge_at
from markets m
join bands b            on b.market_id = m.market_id
left join v_latest_prob p on p.band_id = b.band_id
left join v_latest_edge e on e.band_id = b.band_id
where m.resolution_date >= current_date - 45
  and m.resolution_date <= current_date + 16;

comment on view v_prediction_ladder is
  'Every bucket on every near-dated market with the model probability, the market price and the edge beside it. A forecast in degrees becomes a forecast in dollars here.';


-- --------------------------------------------------------------------------
-- 3. DID WE NAIL IT. Per city, per lead.
--
--    Three numbers, and the third is the one that matters.
--
--    mae_c        how far off, on average
--    bias_c       WHICH WAY. A model 1.5 C hot every day is a fixable model;
--                 one that is 1.5 C off in random directions is not. Pooling
--                 them into "1.5 C error" throws that away.
--    hit_rate     how often the observed max landed in the same 1 C bucket
--                 the forecast pointed at - which is the only accuracy the
--                 market pays for. A 0.6 C error that crosses a boundary
--                 loses; a 0.9 C error that does not, wins.
-- --------------------------------------------------------------------------
create or replace view v_prediction_scorecard as
select
  city_key,
  model,
  lead_days,
  count(*)::int                                             as n_days,
  round(avg(abs_error_c), 3)                                as mae_c,
  round(avg(error_c), 3)                                    as bias_c,
  round(stddev_samp(error_c), 3)                            as error_sd_c,
  round(max(abs_error_c), 2)                                as worst_c,
  -- the bucket test: same floor(temperature) for forecast and outcome
  round(100.0 * count(*) filter (
          where floor(forecast_max_c) = floor(observed_max_c)
        )::numeric / nullif(count(*), 0), 1)                as hit_rate_pct,
  round(100.0 * count(*) filter (where abs_error_c <= 1.0)::numeric
        / nullif(count(*), 0), 1)                           as within_1c_pct,
  min(for_date)                                             as since,
  max(for_date)                                             as until
from fact_forecast_outcome
where for_date >= current_date - 365
group by city_key, model, lead_days
having count(*) >= 5
order by city_key, model, lead_days;

comment on view v_prediction_scorecard is
  'How wrong each model has been, per city, per lead. bias_c is separate from mae_c on purpose: a model that is consistently hot is fixable, one that is off in random directions is not, and pooling them hides which you have. hit_rate_pct is the only accuracy the market pays for - a 0.6C error across a bucket boundary loses and a 0.9C error inside one wins.';


-- --------------------------------------------------------------------------
-- 4. WHAT BEING RIGHT WAS WORTH. The bankroll curve, from the data bank.
--
--    Cumulative realised P&L, and the running win rate beside it. Separate
--    from the scorecard above because a desk can be right about the weather
--    and lose money: entry price, fees and fill size all sit between the two.
-- --------------------------------------------------------------------------
create or replace view v_bankroll_curve as
with settled as (
  select
    date_trunc('day', fired_at)::date        as day,
    strategy_id,
    coalesce(net_pnl, 0)                     as net_pnl,
    (coalesce(net_pnl, 0) > 0)               as won,
    filled
  from fact_signal_outcome
  where fired_at is not null
    and filled is true
),
by_day as (
  select day,
         sum(net_pnl)                                       as day_pnl,
         count(*)::int                                      as n_trades,
         count(*) filter (where won)::int                   as n_won
    from settled group by day
)
select
  day,
  day_pnl,
  n_trades,
  n_won,
  sum(day_pnl)  over (order by day)                         as cumulative_pnl,
  sum(n_trades) over (order by day)                         as cumulative_trades,
  sum(n_won)    over (order by day)                         as cumulative_won,
  round(100.0 * sum(n_won) over (order by day)::numeric
        / nullif(sum(n_trades) over (order by day), 0), 1)  as win_rate_pct
from by_day
order by day;

comment on view v_bankroll_curve is
  'Realised P&L per day and cumulative, with the running win rate. Only FILLED signals: a signal that never filled cost nothing and proved nothing.';


-- --------------------------------------------------------------------------
-- 5. WHAT THE EDGE WAS ACTUALLY WORTH, per bucket of claimed edge.
--
--    The question behind "can this scale": when the desk claimed 8 points of
--    edge, what did it get? If claimed and realised track, size can go up. If
--    realised is flat regardless of claimed, the edge estimate is noise and
--    size must not.
-- --------------------------------------------------------------------------
create or replace view v_edge_scaling as
with b as (
  select
    width_bucket(edge_net_pp, 0, 25, 5)          as edge_bucket,
    edge_net_pp,
    model_prob,
    market_price,
    settled_yes
  from fact_band_outcome
  where edge_net_pp is not null
    and market_price is not null
)
select
  edge_bucket,
  concat(((edge_bucket - 1) * 5)::text, '-', (edge_bucket * 5)::text, ' pp') as edge_band,
  count(*)::int                                            as n,
  round(avg(edge_net_pp), 2)                               as claimed_edge_pp,
  -- realised: what a dollar at that price actually returned, in points
  round(100.0 * avg(case when settled_yes then 1 - market_price
                         else -market_price end), 2)       as realised_pp,
  round(100.0 * avg(case when settled_yes then 1 else 0 end), 1) as settled_yes_pct,
  round(100.0 * avg(model_prob), 1)                        as mean_model_prob_pct
from b
where edge_bucket between 1 and 5
group by edge_bucket
order by edge_bucket;

comment on view v_edge_scaling is
  'Claimed edge against realised return, in points, bucketed. The scaling test: if realised tracks claimed, size can go up. If realised is flat whatever was claimed, the edge estimate is noise and size must not.';


-- --------------------------------------------------------------------------
-- 6. Grants. All read-only, all safe for the browser.
-- --------------------------------------------------------------------------
do $ad4$
declare r text; v text;
begin
  foreach v in array array['v_forecast_convergence', 'v_prediction_ladder',
                           'v_prediction_scorecard', 'v_bankroll_curve',
                           'v_edge_scaling'] loop
    foreach r in array array['anon', 'authenticated', 'service_role'] loop
      if exists (select 1 from pg_roles where rolname = r) then
        execute format('grant select on %I to %I', v, r);
      end if;
    end loop;
  end loop;
end
$ad4$;


do $ad4$
declare t0 timestamptz; ms numeric; v text; n bigint;
begin
  foreach v in array array['v_forecast_convergence', 'v_prediction_ladder',
                           'v_prediction_scorecard', 'v_bankroll_curve',
                           'v_edge_scaling'] loop
    t0 := clock_timestamp();
    execute format('select count(*) from %I', v) into n;
    ms := round(extract(epoch from (clock_timestamp() - t0)) * 1000);
    raise notice 'ad4_31: % - % row(s), % ms', v, n, ms;
    if ms > 200 then
      raise notice 'ad4_31:   ^ over 200 ms. A page load may not pay this - cache it the way ad4_28 caches the climb profile.';
    end if;
  end loop;
  raise notice 'ad4_31: these feed the Predictive page (/predictive).';
end
$ad4$;
