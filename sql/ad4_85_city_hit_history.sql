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

-- ===========================================================================
-- REBUILT 2026-09-22: THE CALL AS IT STOOD BEFORE THE DAY BEGAN, FOR EVERY CITY.
--
-- Two things were wrong with the first version, and the second was worse.
--
-- 1. IT SHOWED ALMOST NOTHING. A city-day counted only when the market had
--    priced the winning band too, and the venue quotes few international
--    ladders: 98 of 394 scoreable days, 22 of 49 cities. The rest read "No
--    settled days for this city yet" beside a settled history of their own.
--
-- 2. WHAT IT DID SHOW WAS NOT A PREDICTION. It scored fact_band_outcome's
--    model_prob and market_price, which databank.py freezes from the LATEST
--    probability and the LATEST edge before settlement. Measured on the 493
--    winning bands that carry one: that probability was computed a median
--    25 hours after the city's local midnight on the day itself - after the
--    day was over - and the tenth percentile is 4 pm local on the day. By then
--    the engine has the running maximum; "we called it" was mostly the
--    engine reading the thermometer back, and the market's "call" the same.
--
-- THE CALL NOW IS THE LAST PROBABILITY COMPUTED BEFORE THE CITY'S LOCAL DAY
-- BEGAN - from band_probabilities, which keeps every pricing and is never
-- pruned - so it is a forecast in the only sense that matters: nothing that
-- happened on the day could have informed it. called_at and
-- hours_before_day say exactly when it was made.
--
-- THE MODEL IS SCORED ON ITS OWN, FOR EVERY CITY: the whole settled ladder,
-- its pre-day probabilities renormalised over the bands it priced, and a band
-- it did not price counted as zero - if the winner is one of those, that is a
-- miss, which is what it was. The uniform guess over the same ladder is the
-- floor it has to beat.
--
-- THE MARKET IS COMPARED ONLY WHERE THE COMPARISON IS FAIR, as the original
-- rule intended: its YES price at the same cutoff, from edges, on the bands
-- BOTH sides priced before the day, each renormalised over that shared set,
-- and only when that set contains the winner. edges keeps fourteen days, so
-- head-to-head exists for recent days; older days carry the model's record
-- with the market columns empty and head_to_head false, rather than a price
-- taken after the fact.
--
-- The winner and the observed maximum still come from fact_band_outcome,
-- frozen after settlement against final station authority. Retired cities
-- stay: this is the record, not the desk.
-- ===========================================================================

-- The column set changed, so these are replaced, not appended to.
drop view if exists v_city_hit_summary;
drop view if exists v_city_hit_history;

create view v_city_hit_history as
with ladder as (
  select o.city_key, o.for_date, o.band_id,
         o.band_lo, o.band_hi, o.open_low, o.open_high,
         o.settled_yes, o.observed_max_c,
         -- THE VENUE'S OWN LABEL: "22°C", "70-71°F", "18°C or below". Built
         -- from the edges this read "22-23" for the single-degree bucket 22°C
         -- and "70-72" for 70-71°F, because a band is half-open [lo, hi): a
         -- Celsius bucket is ONE temperature, a Fahrenheit one two. The
         -- computed form below is only a fallback, and it is half-open too.
         coalesce(cb.band_label,
           case
             when o.open_low  then '<= ' || trim(to_char(o.band_hi - 1, 'FM999990.#'))
             when o.open_high then '>= ' || trim(to_char(o.band_lo, 'FM999990.#'))
             when o.band_hi - o.band_lo = 1 then trim(to_char(o.band_lo, 'FM999990.#'))
             else trim(to_char(o.band_lo, 'FM999990.#')) || '-' || trim(to_char(o.band_hi - 1, 'FM999990.#'))
           end) as band_label,
         -- The city's own midnight at the start of the day being called.
         (o.for_date::timestamp at time zone coalesce(c.timezone, 'UTC')) as day_starts_at
    from v_fact_band_outcome_clean o
    left join cities c on c.city_key = o.city_key
    left join v_canonical_bands cb on cb.band_id = o.band_id
   where o.observed_max_c is not null
),
settled as (
  select city_key, for_date
    from ladder
   group by city_key, for_date
  having bool_or(settled_yes)
),
priced as (
  select l.*,
         p.prob          as p_pre,
         p.forecast_max_c, p.sigma_c, p.confidence, p.regime_label,
         p.computed_at   as priced_at,
         e.market_price  as market_pre
    from ladder l
    join settled d on d.city_key = l.city_key and d.for_date = l.for_date
    -- The desk's probability as it stood before the day began: the one it
    -- would have traded on (calibrated where calibration existed).
    left join lateral (
      select coalesce(bp.calibrated_prob, bp.raw_prob) as prob,
             bp.forecast_max_c, bp.sigma_c, bp.confidence, bp.regime_label, bp.computed_at
        from band_probabilities bp
       where bp.band_id = l.band_id
         and bp.computed_at < l.day_starts_at
       order by bp.computed_at desc, bp.prob_id desc
       limit 1
    ) p on true
    -- The market's YES price at the same cutoff.
    left join lateral (
      select x.market_price
        from edges x
       where x.band_id = l.band_id
         and x.side = 'YES'
         and x.computed_at < l.day_starts_at
       order by x.computed_at desc
       limit 1
    ) e on true
),
model_day as (
  select city_key, for_date,
         count(*)        as ladder_bands,
         count(p_pre)    as model_bands,
         sum(p_pre)      as model_mass
    from priced
   group by city_key, for_date
  having count(p_pre) > 0 and sum(p_pre) > 0
),
model_scored as (
  select p.*, d.ladder_bands, d.model_bands,
         p.p_pre / d.model_mass as p_model
    from priced p
    join model_day d on d.city_key = p.city_key and d.for_date = p.for_date
),
model_picks as (
  select m.city_key, m.for_date,
         max(m.ladder_bands)                                         as ladder_bands,
         max(m.model_bands)                                          as model_bands,
         max(m.observed_max_c)                                       as observed_max_c,
         max(m.forecast_max_c)                                       as forecast_max_c,
         max(m.sigma_c)                                              as sigma_c,
         max(m.confidence)                                           as confidence,
         max(m.regime_label)                                         as regime_label,
         max(m.priced_at)                                            as called_at,
         max(m.day_starts_at)                                        as day_starts_at,
         max(m.band_label) filter (where m.settled_yes)              as winner,
         -- The most probable band; a tie goes to the lower one.
         (array_agg(m.band_label order by m.p_model desc nulls last,
                                          m.band_lo asc nulls first))[1] as model_call,
         max(m.p_model)                                              as model_call_prob,
         coalesce(max(m.p_model) filter (where m.settled_yes), 0)    as model_prob_on_winner,
         -- Multiclass Brier over the whole settled ladder; an unpriced band is 0.
         sum(power(coalesce(m.p_model, 0) - (case when m.settled_yes then 1 else 0 end), 2)) as brier_model,
         sum(power(1.0 / m.ladder_bands   - (case when m.settled_yes then 1 else 0 end), 2)) as brier_uniform
    from model_scored m
   group by m.city_key, m.for_date
),
-- Head to head: both sides, over the bands BOTH priced before the day,
-- renormalised over that same set, and only when it holds the winner.
common as (
  select * from priced where p_pre is not null and market_pre is not null
),
common_day as (
  select city_key, for_date,
         sum(p_pre)            as model_mass,
         sum(market_pre)       as market_mass,
         count(*)              as n_common,
         bool_or(settled_yes)  as winner_priced_by_both
    from common
   group by city_key, for_date
),
h2h as (
  select c.city_key, c.for_date, t.n_common,
         (array_agg(c.band_label order by c.market_pre desc nulls last,
                                          c.band_lo asc nulls first))[1]  as market_call,
         max(c.market_pre / t.market_mass)                                 as market_call_price,
         max(c.market_pre / t.market_mass) filter (where c.settled_yes)   as market_prob_on_winner,
         sum(power(c.p_pre / t.model_mass       - (case when c.settled_yes then 1 else 0 end), 2)) as brier_model_common,
         sum(power(c.market_pre / t.market_mass - (case when c.settled_yes then 1 else 0 end), 2)) as brier_market,
         sum(power(1.0 / t.n_common             - (case when c.settled_yes then 1 else 0 end), 2)) as brier_uniform_common
    from common c
    join common_day t on t.city_key = c.city_key and t.for_date = c.for_date
   where t.winner_priced_by_both and t.model_mass > 0 and t.market_mass > 0
   group by c.city_key, c.for_date, t.n_common
)
select
  m.city_key,
  c.display_name,
  c.unit,
  m.for_date,
  m.ladder_bands,
  m.model_bands,
  m.observed_max_c,
  m.forecast_max_c,
  round((m.forecast_max_c - m.observed_max_c)::numeric, 2)                  as error_c,
  m.sigma_c,
  m.confidence,
  m.regime_label,
  m.winner,
  m.model_call,
  round(m.model_call_prob::numeric, 4)                                      as model_call_prob,
  round(m.model_prob_on_winner::numeric, 4)                                 as model_prob_on_winner,
  (m.model_call = m.winner)                                                 as model_hit,
  round(m.brier_model::numeric, 4)                                          as brier_model,
  round(m.brier_uniform::numeric, 4)                                        as brier_uniform,
  m.called_at,
  round((extract(epoch from (m.day_starts_at - m.called_at)) / 3600.0)::numeric, 1) as hours_before_day,
  (h.city_key is not null)                                                  as head_to_head,
  h.n_common                                                                as bands_scored,
  h.market_call,
  round(h.market_call_price::numeric, 4)                                    as market_call_price,
  round(h.market_prob_on_winner::numeric, 4)                                as market_prob_on_winner,
  (h.market_call = m.winner)                                                as market_hit,
  round(h.brier_model_common::numeric, 4)                                   as brier_model_common,
  round(h.brier_market::numeric, 4)                                         as brier_market,
  round(h.brier_uniform_common::numeric, 4)                                 as brier_uniform_common
from model_picks m
left join h2h h on h.city_key = m.city_key and h.for_date = m.for_date
left join cities c on c.city_key = m.city_key
where m.winner is not null
order by m.for_date desc, m.city_key;

comment on view v_city_hit_history is
  'One row per settled city-day the desk priced BEFORE the day began: the band it called then, the band that paid, and a multiclass Brier over the whole settled ladder beside a uniform guess. The market is compared only on days both sides priced the winner before the day (head_to_head), over the bands both priced. The call is the last probability computed before the city''s local midnight - never the post-settlement value fact_band_outcome freezes.';


-- --------------------------------------------------------------------------
-- The same thing summarised per city, which is what a picker needs beside
-- each name.
-- --------------------------------------------------------------------------
create view v_city_hit_summary as
select
  h.city_key,
  h.display_name,
  h.unit,
  count(*)                                                        as days,
  count(*) filter (where h.model_hit)                             as model_hits,
  round(avg(case when h.model_hit then 1.0 else 0 end), 4)        as model_hit_rate,
  round(avg(h.model_prob_on_winner), 4)                           as avg_model_prob_on_winner,
  round(avg(h.brier_model), 4)                                    as brier_model,
  round(avg(h.brier_uniform), 4)                                  as brier_uniform,
  round(avg(abs(h.error_c)), 3)                                   as mae_c,
  round(avg(h.error_c), 3)                                        as bias_c,
  round(avg(h.hours_before_day), 1)                               as avg_hours_before_day,
  min(h.for_date)                                                 as first_day,
  max(h.for_date)                                                 as last_day,
  -- Head to head, on the days it is fair.
  count(*) filter (where h.head_to_head)                          as h2h_days,
  count(*) filter (where h.head_to_head and h.model_hit)          as h2h_model_hits,
  count(*) filter (where h.head_to_head and h.market_hit)         as market_hits,
  round(avg(case when h.market_hit then 1.0 else 0 end)
          filter (where h.head_to_head), 4)                       as market_hit_rate,
  round(avg(h.brier_model_common) filter (where h.head_to_head), 4) as brier_model_h2h,
  round(avg(h.brier_market)       filter (where h.head_to_head), 4) as brier_market,
  count(*) filter (where h.head_to_head
                     and h.brier_model_common < h.brier_market)   as days_we_beat_the_market,
  -- A VERDICT IN WORDS, on the two questions that decide whether a city is
  -- worth trading: are we better than a coin, and are we better than the
  -- price we would have to pay?
  case
    when count(*) < 5                                              then 'too few settled days to judge'
    when avg(h.brier_model) >= avg(h.brier_uniform)                then 'no better than guessing'
    when count(*) filter (where h.head_to_head) < 5                then 'better than guessing; too few days the market also priced to compare'
    when avg(h.brier_model_common) filter (where h.head_to_head)
      <= avg(h.brier_market) filter (where h.head_to_head)         then 'sharper than the market here'
    else                                                                'informative, but the market is sharper'
  end                                                             as verdict
from v_city_hit_history h
group by h.city_key, h.display_name, h.unit
order by days desc, h.city_key;

comment on view v_city_hit_summary is
  'Per city: how often the band called before the day began was the band that paid, the Brier of that call against a uniform guess, and - on the days both sides priced the winner before the day - the market beside it. Five settled days is the floor below which it says so rather than pretending.';

do $ad4$
declare r text;
begin
  foreach r in array array['anon', 'authenticated', 'service_role'] loop
    if exists (select 1 from pg_roles where rolname = r) then
      execute format('grant select on v_city_hit_history to %I', r);
      execute format('grant select on v_city_hit_summary to %I', r);
    end if;
  end loop;
end
$ad4$;
