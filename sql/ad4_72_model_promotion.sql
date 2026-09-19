-- ===========================================================================
-- ad4_72_model_promotion.sql - A FIT IS A CHALLENGER, NOT A FORECAST
--
-- WHAT WAS WRONG WITH THE OLD BAR
-- -------------------------------
-- derived_weather_model.beats_persistence was the only thing standing between
-- a fitted model and a traded price. It asks: on held-out days of this
-- model's own training window, is it closer to the observed maximum than
-- yesterday-equals-today?
--
-- That is a fair question and it is not the right one. Three gaps:
--
--   THE BENCHMARK IS WRONG. Nothing on this desk prices off persistence. It
--   prices off the public forecast, and the public forecast is very good. A
--   model can beat persistence comfortably and still be worse than the number
--   it would replace - measured on this repo's own cities, persistence MAE
--   runs 1.2 to 2.5 C while NWS runs well under that.
--
--   THE DAYS ARE WRONG. Held-out days sit inside the training window: the fit
--   chose its features knowing the season, and the days on either side are
--   strongly autocorrelated with them. A forward day is one nobody had seen
--   when the prediction was made. Only forward days test what the desk does.
--
--   THE LEADS ARE POOLED. A model sharp at lead 0 and useless on Friday
--   averages to "fine". derived_model_forecast already carries lead_days and
--   v_model_forecast_skill already refuses to pool them; the promotion state
--   is per city AND per lead for the same reason.
--
-- THE RULE
-- --------
-- All five must hold, for one city and one lead, on the SAME forward days:
--
--   1 at least 30 settled forward city-days
--   2 MAE at least 0.10 C better than persistence
--   3 MAE at least 0.10 C better than the public forecast that was available
--     at prediction time - which is the nws_max_c stored on the prediction
--     row, not a forecast fetched afterwards
--   4 a paired moving-block bootstrap over dates whose 90% interval for the
--     improvement stays above zero
--   5 no stale-input, missing-anchor or version-attribution failure
--
-- 0.10 C is not arbitrary. Polymarket's buckets are 1 C wide and the desk's
-- own material-gain floor for a feature is 0.05 C; a centre that moves by
-- less than a tenth of a bucket cannot change which bucket a day lands in,
-- so an improvement below it is real and useless.
--
-- WHY A BLOCK BOOTSTRAP. Weather is autocorrelated over days. Resampling
-- single days would treat one warm spell the model happened to call well as
-- thirty independent wins, and hand back an interval far too narrow. Moving
-- blocks of consecutive dates keep the runs intact. PAIRED, because the two
-- models are scored on the same day: what is resampled is the per-day
-- DIFFERENCE, which removes the day's own difficulty from the comparison.
--
-- WHAT "STALE" MEANS, precisely, because it is the state people guess at:
--   the fit is older than PROMOTION_FIT_MAX_AGE_DAYS, or
--   no forward prediction has been written for this city inside
--   PROMOTION_PREDICTION_MAX_AGE_DAYS, or
--   the scored rows cannot be attributed to a fit (no model_version).
-- A stale model is not rejected - nothing has been proved against it - but it
-- cannot be promoted either, because what would be promoted is unknown.
--
-- The table itself lives in ad4_00_preflight.sql: ad4_25's
-- v_model_disagreement reads it and cannot install after this file.
--
-- RUN ORDER: after ad4_25_model_forecast.sql. Re-runnable.
-- ===========================================================================

-- --------------------------------------------------------------------------
-- 1. WHICH FIT A PREDICTION CAME FROM.
--
-- Rule 5 cannot be checked without this. A promotion is a statement about a
-- specific set of coefficients, and coefficients are refitted weekly - so a
-- row of forward predictions that cannot name the fit behind it might have
-- come from any of them, and promoting on it promotes nothing in particular.
--
-- Both tables gain the column. derived_weather_model records the version it
-- IS; derived_model_forecast records the version it was PREDICTED BY, copied
-- at prediction time so a later refit cannot rewrite history.
-- --------------------------------------------------------------------------
-- The columns themselves are added by the files that own their tables -
-- ad4_21 for derived_weather_model, ad4_25 for derived_model_forecast - so
-- that re-running either one cannot drop what this file depends on. Both are
-- asserted here rather than created, because an install that skipped them
-- would fail at promotion time with a message about a missing column rather
-- than about a missing file.
do $ad4$
begin
  if not exists (select 1 from information_schema.columns
                  where table_name = 'derived_weather_model' and column_name = 'model_version') then
    raise notice 'ad4_72: derived_weather_model.model_version is missing - run sql/ad4_21_weather_features.sql again.';
  end if;
  if not exists (select 1 from information_schema.columns
                  where table_name = 'derived_model_forecast' and column_name = 'model_version') then
    raise notice 'ad4_72: derived_model_forecast.model_version is missing - run sql/ad4_25_model_forecast.sql again.';
  end if;
end
$ad4$;

comment on column derived_weather_model.model_version is
  'Label of this exact set of coefficients. A promotion is a statement about one fit, and fits are refitted weekly.';
comment on column derived_model_forecast.model_version is
  'The fit this prediction was made by, copied at prediction time. A refit must never be able to rewrite which coefficients produced a past number.';


-- --------------------------------------------------------------------------
-- 2. THE STATE, READABLE.
--
-- One row per city and lead, with the sentence that explains it. A state on
-- its own ("shadow") is the thing everyone then asks a question about, so the
-- answer travels with it.
-- --------------------------------------------------------------------------
create or replace view v_model_promotion as
select
  p.city_key,
  c.display_name,
  p.lead_days,
  p.target,
  p.state,
  p.n_days,
  p.model_mae_c,
  p.public_mae_c,
  p.persistence_mae_c,
  p.gain_vs_public_c,
  p.gain_vs_persistence_c,
  p.boot_lo_c,
  p.boot_hi_c,
  p.model_version,
  p.first_day,
  p.last_day,
  p.reasons,
  p.computed_at,
  case p.state
    when 'promoted' then format(
      'Promoted at lead %s: %s C better than the public forecast and %s C better than '
      'persistence across %s settled forward days, with the 90%% interval for the '
      'improvement from %s to %s C - above zero.',
      p.lead_days, p.gain_vs_public_c, p.gain_vs_persistence_c, p.n_days,
      p.boot_lo_c, p.boot_hi_c)
    when 'rejected' then format(
      'Rejected at lead %s on %s settled forward days: it does not beat what it has to '
      'beat. The model''s own error is %s C against %s C for the public forecast.',
      p.lead_days, p.n_days, p.model_mae_c, p.public_mae_c)
    when 'stale' then
      'Nothing recent enough to judge: either the fit or its forward predictions stopped arriving.'
    else format(
      'In shadow at lead %s: %s settled forward day(s) so far, and the rule needs 30. It is '
      'being scored and it cannot move a price.', p.lead_days, coalesce(p.n_days, 0))
  end                                                                as note
from derived_model_promotion p
left join cities c on c.city_key = p.city_key;

comment on view v_model_promotion is
  'Whether each city and lead may move a price, and why. shadow means the evidence is still accumulating; promoted means it beat both persistence and the public forecast on forward days by a margin whose bootstrap interval stays above zero; rejected means it had the days and did not; stale means there is nothing current to judge.';


-- --------------------------------------------------------------------------
-- 3. WHAT THE PRICING ENGINE READS.
--
-- Deliberately one narrow view rather than the table: everything that is not
-- a promotion is invisible here, so a caller cannot accidentally read a
-- shadow row and use it. scripts/probability_engine.py reads THIS.
-- --------------------------------------------------------------------------
create or replace view v_model_promoted as
select p.city_key, p.lead_days, p.target, p.model_version,
       -- the model's own measured FORWARD error, which is the width a price
       -- built on its centre has to be published with. Using the public
       -- forecast's mae_c around the model's centre would describe neither
       -- distribution.
       p.model_mae_c,
       p.gain_vs_public_c, p.n_days, p.computed_at
from derived_model_promotion p
where p.state = 'promoted';

comment on view v_model_promoted is
  'The city and lead combinations a fitted model is allowed to price. Empty is the correct default: nothing has earned the right to move a number until it has beaten the public forecast on forward days.';

grant select on v_model_promotion to anon, authenticated, service_role;
grant select on v_model_promoted  to anon, authenticated, service_role;
