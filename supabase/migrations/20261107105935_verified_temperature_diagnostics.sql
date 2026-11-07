-- Overlay current complete verified point evidence on cached diagnostic rows.
-- Preserve every cached bucket grade, probability, timestamp and public column.
-- The existing public cache wrappers retain their grants and owner-read behavior.

do $install$
begin
 if to_regclass('public.mv_city_hit_history') is null or to_regclass('public.mv_prediction_hindsight') is null then
  raise notice 'Verified temperature diagnostics await the optional page cache installation';
  return;
 end if;
 execute $view$create or replace view public.v_city_hit_history as
select
 h.city_key as city_key,
 h.display_name as display_name,
 h.unit as unit,
 h.for_date as for_date,
 h.ladder_bands as ladder_bands,
 h.model_bands as model_bands,
 w.observed_max_c as observed_max_c,
 h.forecast_max_c as forecast_max_c,
 round(h.forecast_max_c-w.observed_max_c,2) as error_c,
 h.sigma_c as sigma_c,
 h.confidence as confidence,
 h.regime_label as regime_label,
 h.winner as winner,
 h.model_call as model_call,
 h.model_call_prob as model_call_prob,
 h.model_prob_on_winner as model_prob_on_winner,
 h.model_hit as model_hit,
 h.brier_model as brier_model,
 h.brier_uniform as brier_uniform,
 h.called_at as called_at,
 h.hours_before_day as hours_before_day,
 h.head_to_head as head_to_head,
 h.bands_scored as bands_scored,
 h.market_call as market_call,
 h.market_call_price as market_call_price,
 h.market_prob_on_winner as market_prob_on_winner,
 h.market_hit as market_hit,
 h.brier_model_common as brier_model_common,
 h.brier_market as brier_market,
 h.brier_uniform_common as brier_uniform_common,
 h.centre_c as centre_c,
 round(h.centre_c-w.observed_max_c,2) as centre_error_c
from public.mv_city_hit_history h
left join public.v_verified_weather_outcomes w on w.city_key=h.city_key and w.for_date=h.for_date
 and w.parser_version='weather-outcomes-v6' and w.unit='C';$view$;
 execute $view$create or replace view public.v_prediction_hindsight as
select
 h.city_key as city_key,
 h.for_date as for_date,
 h.predicted_band as predicted_band,
 h.predicted_pct as predicted_pct,
 h.actual_band as actual_band,
 w.observed_max_c as observed_max_c,
 h.forecast_max_c as forecast_max_c,
 round(w.observed_max_c-h.forecast_max_c,1) as forecast_error_c,
 h.hit as hit,
 h.stated_sigma_c as stated_sigma_c,
 h.regime_label as regime_label,
 h.outcome_source as outcome_source,
 h.called_when as called_when,
 h.call_order as call_order,
 h.after_peak as after_peak,
 h.called_at as called_at,
 h.market_band as market_band,
 h.market_hit as market_hit,
 h.scheduled_local as scheduled_local,
 h.engine_version as engine_version,
 h.prob_on_winner as prob_on_winner
from public.mv_prediction_hindsight h
left join public.v_verified_weather_outcomes w on w.city_key=h.city_key and w.for_date=h.for_date
 and w.parser_version='weather-outcomes-v6' and w.unit='C';$view$;
 execute $comment$comment on view public.v_city_hit_history is 'Cached original bucket grades; temperature actuals/errors use current verified complete v6 points. Missing or disputed point truth is NULL; stored facts and bucket results are unchanged.';$comment$;
 execute $comment$comment on view public.v_prediction_hindsight is 'Cached original calls and venue bucket grades with current verified complete v6 temperature diagnostics. Forecast-error sign is observed minus forecast; missing point truth is NULL.';$comment$;
end $install$;
