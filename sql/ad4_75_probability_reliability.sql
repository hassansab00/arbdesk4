-- How often a band the model called N% actually happened.
--
-- WHY THIS IS NOT scripts/calibration.py. That script FITS a map - ladder-level
-- temperature scaling, trained on the earlier 70% of settlement dates and
-- validated on the later 30% - and the probability engine applies it to every
-- band before anything is priced. It answers "what should the probability be".
-- This view answers a different question, at the point of decision: "the number
-- on this card says 34%; what has 34% actually meant?" It never writes, it
-- never feeds the engine, and it cannot be the reason a price changes without
-- a human looking at it.
--
-- IT MEASURES raw_prob, NOT THE FROZEN model_prob. fact_band_outcome freezes
-- the probability the desk actually showed, which is the CALIBRATED one - and
-- the map has been swapped and switched off repeatedly. Measured 2026-09-19,
-- the 3,573 settled bands with a matching pricing row split 1,595 priced under
-- some map and 1,978 priced raw. Pooling those is pooling two different
-- scales, and the desk today prices with no map at all, so the scale that
-- matters is the raw one. band_probabilities records raw_prob beside
-- calibrated_prob on every row, so this is a recorded fact and not a
-- reconstruction: nothing here un-applies anything.
--
-- THE ADJUSTMENT IS THE MOST CONSERVATIVE ONE THE DATA SUPPORTS. For each
-- decile it takes the Wilson 95% interval on the realised rate and moves the
-- stated probability only as far as the NEAREST BOUND of that interval. If the
-- stated mean already sits inside the interval, the data cannot distinguish it
-- from the truth and nothing moves. That rule has no tuning constant in it,
-- it never overshoots the measurement, and it relaxes toward the full measured
-- gap as n grows - which is the behaviour you want from something allowed to
-- change what a trade looks like.
--
-- Measured 2026-09-19 on 3,573 settled bands, 21 settlement dates:
--
--     stated      n     mean     realised    gap
--     0-10%    2388    2.97%       3.64%   +0.67pp
--     10-20%    649   14.63%      15.72%   +1.09pp
--     20-30%    335   24.44%      21.19%   -3.24pp
--     30-40%    126   34.58%      27.78%   -6.81pp
--     40-50%     52   43.68%      34.62%   -9.06pp
--     50-60%     12   53.00%      41.67%  -11.33pp
--
-- Monotone, and pointing the way physics would predict - a model that is too
-- sure of the middle of its own distribution - and NOT ONE BUCKET's gap clears
-- its own 95% interval yet. So at the time of writing this view applies
-- nothing, and says so. That is the finding, not a failure of the view: the
-- desk buys bands in the 20-50% region, which is exactly where the suggested
-- overstatement is 3 to 9 points, and "suggestive but not yet significant" is
-- the honest description of a -6.81pp gap on 126 observations.
--
-- THE ROWS ARE NOT INDEPENDENT. A ladder has about eleven mutually exclusive
-- bands and exactly one winner by construction, so the true intervals are
-- wider than binomial ones. The Wilson interval here is therefore OPTIMISTIC,
-- which is the safe direction for a rule that only ever narrows an adjustment.

create or replace view public.v_probability_reliability as
with priced as (
  -- The probability the model produced, beside what actually happened. Joined
  -- on the exact pricing instant: fact_band_outcome.priced_at is copied from
  -- band_probabilities.computed_at when the outcome is frozen, so this pairs a
  -- settlement with the row that priced it rather than with a later reprice.
  select p.raw_prob, o.settled_yes
    from public.v_fact_band_outcome_clean o
    join public.cities c
      on c.city_key = o.city_key and coalesce(c.status, 'active') = 'active'
    join public.band_probabilities p
      on p.band_id = o.band_id and p.computed_at = o.priced_at
   where o.settled_yes is not null
     and p.raw_prob is not null
     and p.raw_prob >= 0 and p.raw_prob <= 1
),
bucketed as (
  select least(9, greatest(0, floor(raw_prob * 10)::int)) as bucket,
         raw_prob, settled_yes
    from priced
),
agg as (
  select bucket,
         count(*)::numeric                                        as n,
         sum(case when settled_yes then 1 else 0 end)::numeric     as wins,
         avg(raw_prob)::numeric                                    as mean_stated
    from bucketed
   group by bucket
),
wilson as (
  -- Wilson score interval, z = 1.96. Chosen over the normal approximation
  -- because the low deciles hold rates near 3% where the normal interval can
  -- run below zero and would hand back an impossible probability.
  select a.*,
         (a.wins / a.n)                                           as realised,
         ((a.wins + 1.9208) / (a.n + 3.8416))                     as centre,
         (1.96 / (a.n + 3.8416))
           * sqrt(a.wins * (a.n - a.wins) / a.n + 0.9604)         as halfwidth
    from agg a
),
bounds as (
  select w.*,
         greatest(0.0, w.centre - w.halfwidth) as wilson_lo,
         least(1.0,    w.centre + w.halfwidth) as wilson_hi
    from wilson w
)
select
  b.bucket,
  (b.bucket / 10.0)::numeric                                      as bucket_lo,
  ((b.bucket + 1) / 10.0)::numeric                                as bucket_hi,
  b.n::bigint                                                     as n,
  round(b.mean_stated, 6)                                         as mean_stated,
  round(b.realised, 6)                                            as realised,
  round(b.realised - b.mean_stated, 6)                            as gap,
  round(b.wilson_lo, 6)                                           as wilson_lo,
  round(b.wilson_hi, 6)                                           as wilson_hi,
  -- The nearest point of the interval to what the model said. Equal to
  -- mean_stated whenever the model's figure is already inside it.
  round(least(b.wilson_hi, greatest(b.wilson_lo, b.mean_stated)), 6) as adjusted,
  -- What to add to any probability falling in this decile. Zero unless the
  -- measurement both clears the sample floor AND excludes the stated value.
  round(
    case when b.n >= 30
          and least(b.wilson_hi, greatest(b.wilson_lo, b.mean_stated)) <> b.mean_stated
         then least(b.wilson_hi, greatest(b.wilson_lo, b.mean_stated)) - b.mean_stated
         else 0 end, 6)                                           as shift,
  (b.n >= 30
     and least(b.wilson_hi, greatest(b.wilson_lo, b.mean_stated)) <> b.mean_stated)
                                                                  as applies,
  -- 30 band-rows is a floor against a freak decile, not a claim that 30 is
  -- enough: the Wilson rule is what actually decides, and it self-gates,
  -- because a thin bucket has an interval too wide to exclude anything.
  case
    when b.n < 30 then b.n || ' settled bands — too few to say anything'
    when least(b.wilson_hi, greatest(b.wilson_lo, b.mean_stated)) = b.mean_stated
      then 'stated ' || round(100 * b.mean_stated, 1) || '% sits inside the 95% interval ['
           || round(100 * b.wilson_lo, 1) || '%, ' || round(100 * b.wilson_hi, 1)
           || '%] on ' || b.n || ' bands — measured gap '
           || round(100 * (b.realised - b.mean_stated), 1) || 'pp is not yet distinguishable from zero'
    else 'stated ' || round(100 * b.mean_stated, 1) || '% is outside the 95% interval ['
         || round(100 * b.wilson_lo, 1) || '%, ' || round(100 * b.wilson_hi, 1)
         || '%] on ' || b.n || ' bands'
  end                                                             as note
from bounds b
order by b.bucket;

comment on view public.v_probability_reliability is
  'Realised vs stated band probability by decile, on raw_prob, for active '
  'cities. `shift` is the most conservative adjustment the data supports and '
  'is 0 whenever the stated value sits inside the Wilson 95% interval. Read '
  'by the opportunities desk; never written back into pricing.';

grant select on public.v_probability_reliability to anon, authenticated;
