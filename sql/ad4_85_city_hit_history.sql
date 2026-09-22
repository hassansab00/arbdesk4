-- ===========================================================================
-- ad4_85_city_hit_history.sql - DID THIS CITY'S PREDICTION LAND, AND HOW OFTEN.
--
-- The desk already measures forecast skill in degrees (derived_forecast_skill)
-- and probability honesty in aggregate (v_calibration). Neither answers the
-- question an operator actually asks about one city: on the days that have
-- settled, did the band we called turn out to be the band that paid, and how
-- did the market do on the same days?
--
-- WHY DEGREES ARE NOT ENOUGH. A mean absolute error of 1.0 C sounds good and
-- says nothing about money: on a 1 C ladder a 1 C error is a different bucket,
-- which is a total loss on the band that was called. Skill has to be read on
-- the ladder, in buckets, against what settled.
--
-- EVERYTHING HERE IS FROZEN EVIDENCE. fact_band_outcome is written by
-- databank.py only after a day has settled against final station authority,
-- and its rows are never updated - so this view cannot flatter itself by
-- re-scoring yesterday with today's knowledge.
--
-- THE MODEL AND THE MARKET ARE SCORED ON THE SAME BANDS. A city-day is only
-- counted when the winning band is present AND both sides priced it. Scoring
-- each side over whatever it happened to price is how the first cut of
-- docs/STRATEGY_EVIDENCE_2026-09-22.md flattered the market by selection: the
-- market quotes a median 6.8 of 11 bands, so filtering to ladders with a
-- winner silently keeps the ones it had priced.
--
-- RUN ORDER: after ad4_18_databank.sql. Views only - nothing to refresh.
-- ===========================================================================

-- --------------------------------------------------------------------------
-- One row per settled city-day: what we called, what the market called, what
-- actually happened.
-- --------------------------------------------------------------------------
create or replace view v_city_hit_history as
with scored as (
  select
    o.city_key, o.for_date, o.band_id,
    o.band_lo, o.band_hi, o.open_low, o.open_high,
    o.model_prob, o.market_price, o.settled_yes,
    o.observed_max_c, o.forecast_max_c, o.sigma_c, o.confidence, o.regime_label,
    -- A LABEL THE PAGE CAN PRINT. The bands are stored as edges, and an
    -- operator reads "82-83" faster than two numeric columns.
    case
      when o.open_low  then '<= ' || trim(to_char(o.band_hi, 'FM999990.#'))
      when o.open_high then '>= ' || trim(to_char(o.band_lo, 'FM999990.#'))
      else trim(to_char(o.band_lo, 'FM999990.#')) || '-' || trim(to_char(o.band_hi, 'FM999990.#'))
    end as band_label
  from fact_band_outcome o
  where o.observed_max_c is not null
),
-- Both sides, over the bands BOTH priced, renormalised over that same set.
common as (
  select * from scored
   where model_prob is not null and market_price is not null
),
totals as (
  select city_key, for_date,
         sum(model_prob)   as model_mass,
         sum(market_price) as market_mass,
         count(*)          as n_common,
         bool_or(settled_yes) as winner_priced_by_both
    from common group by city_key, for_date
),
norm as (
  select c.*,
         t.n_common, t.winner_priced_by_both,
         case when t.model_mass  > 0 then c.model_prob   / t.model_mass  end as p_model,
         case when t.market_mass > 0 then c.market_price / t.market_mass end as p_market
    from common c join totals t using (city_key, for_date)
   where t.winner_priced_by_both
),
picks as (
  select
    n.city_key, n.for_date, n.n_common,
    max(n.observed_max_c) as observed_max_c,
    max(n.forecast_max_c) as forecast_max_c,
    max(n.sigma_c)        as sigma_c,
    max(n.confidence)     as confidence,
    max(n.regime_label)   as regime_label,
    -- what each side called
    (array_agg(n.band_label order by n.p_model  desc nulls last))[1] as model_call,
    max(n.p_model)                                                   as model_call_prob,
    (array_agg(n.band_label order by n.p_market desc nulls last))[1] as market_call,
    max(n.p_market)                                                  as market_call_price,
    -- what actually paid
    max(n.band_label) filter (where n.settled_yes)  as winner,
    max(n.p_model)    filter (where n.settled_yes)  as model_prob_on_winner,
    max(n.p_market)   filter (where n.settled_yes)  as market_prob_on_winner,
    -- multiclass Brier over the shared bands: sum (p - outcome)^2
    sum(power(n.p_model  - (case when n.settled_yes then 1 else 0 end), 2)) as brier_model,
    sum(power(n.p_market - (case when n.settled_yes then 1 else 0 end), 2)) as brier_market,
    sum(power((1.0 / n.n_common) - (case when n.settled_yes then 1 else 0 end), 2)) as brier_uniform
  from norm n
  group by n.city_key, n.for_date, n.n_common
)
select
  p.city_key,
  c.display_name,
  c.unit,
  p.for_date,
  p.n_common                                            as bands_scored,
  p.observed_max_c,
  p.forecast_max_c,
  round((p.forecast_max_c - p.observed_max_c)::numeric, 2) as error_c,
  p.sigma_c,
  p.confidence,
  p.regime_label,
  p.winner,
  p.model_call,
  round(p.model_call_prob::numeric, 4)                  as model_call_prob,
  round(p.model_prob_on_winner::numeric, 4)             as model_prob_on_winner,
  p.market_call,
  round(p.market_call_price::numeric, 4)                as market_call_price,
  round(p.market_prob_on_winner::numeric, 4)            as market_prob_on_winner,
  (p.model_call  = p.winner)                            as model_hit,
  (p.market_call = p.winner)                            as market_hit,
  round(p.brier_model::numeric, 4)                      as brier_model,
  round(p.brier_market::numeric, 4)                     as brier_market,
  round(p.brier_uniform::numeric, 4)                    as brier_uniform
from picks p
left join cities c on c.city_key = p.city_key
where p.winner is not null
order by p.for_date desc, p.city_key;

comment on view v_city_hit_history is
  'One row per settled city-day: the band the model called, the band the market called, the band that actually paid, and a multiclass Brier for each side over the bands BOTH priced. Built from fact_band_outcome, which is frozen after settlement and never updated.';


-- --------------------------------------------------------------------------
-- The same thing summarised per city, which is what a picker needs beside
-- each name.
-- --------------------------------------------------------------------------
create or replace view v_city_hit_summary as
select
  h.city_key,
  h.display_name,
  h.unit,
  count(*)                                                   as days,
  count(*) filter (where h.model_hit)                        as model_hits,
  count(*) filter (where h.market_hit)                       as market_hits,
  round(avg(case when h.model_hit  then 1.0 else 0 end), 4)  as model_hit_rate,
  round(avg(case when h.market_hit then 1.0 else 0 end), 4)  as market_hit_rate,
  round(avg(h.model_prob_on_winner), 4)                      as avg_model_prob_on_winner,
  round(avg(h.market_prob_on_winner), 4)                     as avg_market_prob_on_winner,
  round(avg(h.brier_model), 4)                               as brier_model,
  round(avg(h.brier_market), 4)                              as brier_market,
  round(avg(h.brier_uniform), 4)                             as brier_uniform,
  round(avg(abs(h.error_c)), 3)                              as mae_c,
  round(avg(h.error_c), 3)                                   as bias_c,
  count(*) filter (where h.brier_model < h.brier_market)     as days_we_beat_the_market,
  min(h.for_date)                                            as first_day,
  max(h.for_date)                                            as last_day,
  -- A VERDICT IN WORDS, on the two questions that decide whether a city is
  -- worth trading: are we better than a coin, and are we better than the
  -- price we would have to pay?
  case
    when count(*) < 5                                        then 'too few settled days to judge'
    when avg(h.brier_model) <= avg(h.brier_market)           then 'sharper than the market here'
    when avg(h.brier_model) >= avg(h.brier_uniform)          then 'no better than guessing'
    else                                                          'informative, but the market is sharper'
  end                                                        as verdict
from v_city_hit_history h
group by h.city_key, h.display_name, h.unit
order by days desc, h.city_key;

comment on view v_city_hit_summary is
  'Per city: how often the called band was the band that paid, for us and for the market, with both Brier scores and a plain-language verdict. Five settled days is the floor below which it says so rather than pretending.';
