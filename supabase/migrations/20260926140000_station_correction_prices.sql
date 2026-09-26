-- ===========================================================================
-- THE STATION-CORRECTED COMBINATION PRICES DAYS AHEAD (plan v2.2 P3.9)
--
-- Hassan, 26 Sep: "if all checks out, push to live after testing diligently".
-- The check: replayed on 582 settled city-days of 13-25 Sep (day-ahead, the
-- venue's own ladders, the engine's own sigma, ONLY the centre changed), the
-- corrected combination put the most probability on the winning bucket 37.1%
-- of the time against the engine's 30.2% (+6.9 pts, 95% by date bootstrap
-- [+2.3, +11.1]); log loss 1.847 -> 1.657 (gain 95% [0.132, 0.257]); Brier
-- 0.811 -> 0.763; centre error 1.12 -> 0.82 C. C cities 32.0% -> 39.6%, F
-- cities 24.2% -> 28.8%.
--
-- probability_engine reads derived_corrected_forecast for markets at lead 1
-- or more while this switch is on, and records the fit's version on every
-- price (reasons and forecast_version). Setting enabled to false returns
-- every price to the public-forecast path on the next run.
--
-- IT STARTS OFF. derived_corrected_forecast is filled by the first nightly
-- fit after #190 (27 Sep ~05:15Z). The switch is turned on only after that
-- run's rows are checked: counts, a cell's bias recomputed in SQL, and the
-- logged walk-forward score in line with the replay above.
-- ===========================================================================
do $$ begin
  if to_regclass('public.settings') is null then return; end if;
  insert into public.settings (key, value)
  values ('station_correction_pricing', jsonb_build_object(
    'enabled', false,
    'min_lead_days', 1,
    'max_age_hours', 36,
    'why', 'Plan v2.2 P3.9: replay 13-25 Sep, 582 city-days, day-ahead: right bucket 30.2% -> 37.1% (+6.9 pts, 95% [+2.3, +11.1]); log loss 1.847 -> 1.657. Same-day prices keep the live path until a same-day replay.'))
  on conflict (key) do nothing;
end $$;
