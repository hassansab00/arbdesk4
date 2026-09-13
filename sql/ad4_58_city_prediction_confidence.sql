-- ===========================================================================
-- ad4_58_city_prediction_confidence.sql
--
-- WHAT DO WE THINK THIS CITY WILL HIT, AND HOW MUCH IS THAT WORTH?
--
-- Safe to run any time. Creates one read-only view, changes no data.
--
-- Everything here already existed and had never been put in one row. The
-- prediction lives in v_prediction_ladder, the measured accuracy lives in
-- derived_forecast_skill, and reading one without the other is how a desk
-- talks itself into a position: a 26% bucket sounds like a view until you
-- see the forecast is typically 1.8 C out at that lead, which is most of a
-- bucket.
--
--
-- THE STATED AND THE MEASURED ARE DELIBERATELY BOTH HERE
--
-- sigma_c is what the model CLAIMS its uncertainty is. p90_abs_err_c is what
-- the archive says the error actually was, at this city and this lead. They
-- are not the same number and the difference is the point:
--
--   sigma wide, measured error small   the model is underconfident, and
--                                      every edge computed from it is
--                                      understated
--   sigma narrow, measured error large the model is OVERCONFIDENT, and every
--                                      edge is overstated - the desk sizes UP
--                                      on exactly the trades it should size
--                                      down. This is the failure mode
--                                      sql/ad4_45 exists to correct.
--
-- So `honesty` compares them and says which one is happening, rather than
-- averaging them into a single reassuring figure.
--
--
-- WHAT IT DOES NOT DO
--
-- It does not invent a confidence for a lead the archive has never scored.
-- derived_forecast_skill starts at lead_days = 1, so a same-day row has a
-- prediction and no measured accuracy, and says exactly that instead of
-- borrowing tomorrow's number. Four of the 54 cities have no skill rows at
-- all yet; they come back with the prediction and a null skill.
--
--
-- "MOST LIKELY" IS THE MODAL CLOSED BUCKET, NOT THE LARGEST NUMBER
--
-- The first version of this view took max(calibrated_prob) across every
-- bucket, and the ladder's end buckets are OPEN-ENDED - "89F or below" runs
-- to minus infinity, "108F or higher" to plus infinity. An infinitely wide
-- bucket collects more mass than a 2F one without being more likely, so on
-- Austin at lead 1 (centre 95.7F, sigma 4.2C) it reported
--
--     most likely: 89F or below, 21.3%
--
-- while every real bucket sat near 10% and the desk's own centre was six
-- buckets higher. The bucket was not most likely, it was widest. Compare
-- like for like: modal_band ignores open-ended buckets, and the open tails
-- are still published as tail_low_pct / tail_high_pct because "21% chance
-- this lands below the whole board" is worth knowing, just not as a mode.
--
-- centre_band is published beside it - the bucket the forecast itself falls
-- in. When the two disagree the distribution is skewed, and that is a fact
-- about the model worth seeing rather than a number to pick between.
--
--
-- UNITS FOLLOW THE CITY, BECAUSE THE MARKET DOES
--
-- Twelve cities settle in Fahrenheit and their band labels are already in
-- Fahrenheit. Publishing only expected_max_c put "34.4" next to "94-95F",
-- which reads as a disagreement and is the same number. expected_max_display
-- and display_unit carry the city's own scale.
-- ===========================================================================

do $ad4$
begin
  if to_regclass('public.v_prediction_ladder') is null then
    raise exception 'ad4_58 needs v_prediction_ladder - run sql/ad4_31_predictive.sql first';
  end if;
  if to_regclass('public.derived_forecast_skill') is null then
    raise exception 'ad4_58 needs derived_forecast_skill - run sql/ad4_phase2.sql first';
  end if;
end $ad4$;

-- Dropped rather than replaced: `create or replace view` cannot rename or
-- reorder columns, and this view's shape changed once already (most_likely_*
-- became modal_*). Re-running must not fail on a database carrying the older
-- shape. Nothing else reads it, so the drop is safe.
drop view if exists v_city_prediction_confidence;

create view v_city_prediction_confidence as
with skill as (
  -- derived_forecast_skill is a time series - one row per recompute. Only
  -- the newest measurement for each city and lead is the current answer.
  select distinct on (city_key, lead_days)
         city_key, lead_days, n_days, mae_c, bias_c, p90_abs_err_c,
         pct_within_one_band, band_width_c
    from derived_forecast_skill
   order by city_key, lead_days, computed_at desc
),
top_band as (
  -- The bucket the desk thinks is most likely, and what it is charging.
  --
  -- side = 'YES' IS LOAD-BEARING. v_prediction_ladder carries one row per
  -- SIDE, so every band appears twice - 2,244 rows over 1,122 real bands.
  -- calibrated_prob is the same on both (it is the chance the band hits, not
  -- the chance a side pays), but market_price is NOT: the YES rows average
  -- 0.09 and the NO rows 0.83, being the two halves of the same book. Taking
  -- whichever row sorted first put a NO price beside a YES probability and
  -- made Austin read "most likely 89F or below at 21%, market 99.9%" - which
  -- is not a disagreement, it is the market agreeing, quoted from the other
  -- side of the ticket.
  select distinct on (city_key, for_date)
         city_key, for_date, band_id, band_label, band_lo, band_hi,
         calibrated_prob, forecast_max_c, sigma_c, confidence, regime_label,
         market_price, edge_net_pp, tradeable, block_reason
    from v_prediction_ladder
   where calibrated_prob is not null
     and for_date >= current_date
     and side = 'YES'
     -- closed buckets only: see "MOST LIKELY" above
     and band_lo is not null and band_hi is not null
   -- calibrated_prob desc alone is not deterministic - Austin's 98-99F and
   -- 100-101F were both 15.7% and the "most likely" flipped between runs.
   order by city_key, for_date, calibrated_prob desc, band_lo
),
spread as (
  -- How concentrated the whole ladder is, not just its top rung. A 26% top
  -- bucket in a three-bucket market is a view; the same 26% spread over
  -- twelve buckets is noise wearing a number.
  --
  -- The open tails are counted here rather than dropped: "21% chance this
  -- lands below the entire board" is a real statement about the day, it just
  -- is not a mode.
  select city_key, for_date,
         count(*)::int                       as bands_priced,
         round(sum(calibrated_prob), 3)      as prob_mass,
         count(*) filter (where tradeable)::int as tradeable_bands,
         round(100 * sum(calibrated_prob) filter (where band_lo is null), 1)
                                             as tail_low_pct,
         round(100 * sum(calibrated_prob) filter (where band_hi is null), 1)
                                             as tail_high_pct
    from v_prediction_ladder
   where calibrated_prob is not null and for_date >= current_date
     and side = 'YES'          -- as above: one row per band, not per ticket
   group by city_key, for_date
),
centre as (
  -- The bucket the desk's own forecast lands in. Where this and the modal
  -- bucket disagree, the distribution is skewed.
  -- The band labels are already in the city's own scale, so the centre has
  -- to be converted into that scale before it can be compared to band_lo /
  -- band_hi. Comparing 34.4 (C) against a 94-96 (F) bucket is how the first
  -- pass concluded the centre fell outside its own most likely band on 27 of
  -- 55 rows - it did not; 34.4 C is 93.9 F.
  select distinct on (l.city_key, l.for_date)
         l.city_key, l.for_date, l.band_label as centre_band
    from v_prediction_ladder l
    join cities ct on ct.city_key = l.city_key
   where l.for_date >= current_date and l.side = 'YES'
     and l.band_lo is not null and l.band_hi is not null
     and l.forecast_max_c is not null
     and case when ct.unit = 'F' then l.forecast_max_c * 9.0 / 5.0 + 32
              else l.forecast_max_c end >= l.band_lo
     and case when ct.unit = 'F' then l.forecast_max_c * 9.0 / 5.0 + 32
              else l.forecast_max_c end <  l.band_hi
   order by l.city_key, l.for_date, l.band_lo
)
select
  t.city_key,
  c.display_name,
  t.for_date,
  (t.for_date - current_date)::int                     as lead_days,
  round(t.forecast_max_c, 1)                           as expected_max_c,
  -- In the city's own scale, which is the scale its bands and its market are
  -- quoted in. A US city reading "34.4" beside "94-95F" looks wrong and is
  -- the same temperature.
  round(case when c.unit = 'F' then t.forecast_max_c * 9.0 / 5.0 + 32
             else t.forecast_max_c end, 1)             as expected_max_display,
  coalesce(c.unit, 'C')                                as display_unit,
  t.band_label                                         as modal_band,
  ce.centre_band,
  (ce.centre_band is distinct from t.band_label)       as skewed,
  t.band_lo, t.band_hi,
  round(100 * t.calibrated_prob, 1)                    as modal_pct,
  round(100 * t.market_price, 1)                       as market_pct,
  round(t.edge_net_pp, 1)                              as edge_net_pp,
  t.tradeable, t.block_reason,
  round(t.sigma_c, 2)                                  as stated_sigma_c,
  t.confidence, t.regime_label,
  s.n_days                                             as skill_n_days,
  round(s.mae_c, 2)                                    as measured_mae_c,
  round(s.bias_c, 2)                                   as measured_bias_c,
  round(s.p90_abs_err_c, 2)                            as measured_p90_err_c,
  s.pct_within_one_band,
  sp.bands_priced, sp.tradeable_bands,
  sp.tail_low_pct, sp.tail_high_pct,
  -- Stated versus measured, in a sentence. Nulls stay null rather than
  -- becoming a cheerful default.
  case
    when s.n_days is null then
      format('No measured accuracy at %s day(s) ahead yet - prediction only.',
             (t.for_date - current_date))
    when t.sigma_c is null then
      format('Typically %s C out at this lead over %s day(s); the model states no sigma.',
             round(s.mae_c, 1), s.n_days)
    when s.p90_abs_err_c > 2.0 * t.sigma_c then
      format('OVERCONFIDENT: states +/-%s C, but 1 day in 10 misses by %s C or more (%s day(s) measured). Edges from this are overstated.',
             round(t.sigma_c, 1), round(s.p90_abs_err_c, 1), s.n_days)
    when s.p90_abs_err_c < 0.75 * t.sigma_c then
      format('Cautious: states +/-%s C, measured worst tenth is %s C over %s day(s). Edges from this are understated.',
             round(t.sigma_c, 1), round(s.p90_abs_err_c, 1), s.n_days)
    else
      format('States +/-%s C, measured %s C typical and %s C in the worst tenth over %s day(s) - consistent.',
             round(t.sigma_c, 1), round(s.mae_c, 1), round(s.p90_abs_err_c, 1), s.n_days)
  end                                                  as honesty
from top_band t
left join skill s
       on s.city_key = t.city_key
      and s.lead_days = (t.for_date - current_date)
left join spread sp
       on sp.city_key = t.city_key and sp.for_date = t.for_date
left join centre ce
       on ce.city_key = t.city_key and ce.for_date = t.for_date
left join cities c on c.city_key = t.city_key
order by t.city_key, t.for_date;

comment on view v_city_prediction_confidence is
  'Per city and day: the temperature the desk expects, the bucket it thinks most likely and at what probability, what the market charges for that bucket, and - separately - how accurate this city has actually been at this lead. Stated sigma and measured error are both present on purpose: the gap between them is whether an edge is real. Never invents a confidence for a lead the archive has not scored.';

do $ad4$
declare r text;
begin
  foreach r in array array['anon', 'authenticated', 'service_role'] loop
    if exists (select 1 from pg_roles where rolname = r) then
      execute format('grant select on v_city_prediction_confidence to %I', r);
    end if;
  end loop;
end $ad4$;

do $ad4$
declare v_rows int; v_scored int; v_cities int;
begin
  select count(*), count(*) filter (where skill_n_days is not null),
         count(distinct city_key)
    into v_rows, v_scored, v_cities
    from v_city_prediction_confidence;
  raise notice 'ad4_58: % forward row(s) across % city/cities, % carrying measured accuracy',
               v_rows, v_cities, v_scored;
  raise notice 'ad4_58: select * from v_city_prediction_confidence where lead_days between 1 and 3;';
end $ad4$;
