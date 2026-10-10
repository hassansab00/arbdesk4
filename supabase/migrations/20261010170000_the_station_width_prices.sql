-- THE WIDTH AROUND THE CORRECTED CENTRE PRICES (plan v2.3 P3.9 part 3).
--
-- Hassan, 10 Oct: "YES TURN ON WIDT". The width fitted nightly to the
-- station-corrected centre's own errors (derived_station_width, recorded
-- beside every price as band_probabilities.station_width_c since #230) has
-- been shadow since 28 Sep. 20260927190000 seeded settings.station_width_pricing
-- OFF, to be turned on "once the nightly forward score's lower bound is above
-- zero". scripts/station_width_score.py (P3.9_width_score) re-prices each
-- confirmed market's last day-ahead ladder, as served, with the stored width
-- around the SAME centre, so its score is what pricing with the width does.
-- Its run of 10 Oct 05:03Z (ingest_log), 29 Sep-9 Oct, 11 dates, 507
-- markets, verdict "ready":
--
--   log-loss gain per city-day  +0.1171, 90% [0.0749, 0.156]  (C 397: +0.1177, F 110: +0.1151)
--   Brier gain                  +0.0372, 90% [0.0262, 0.048]
--   CRPS gain                   +0.0411 C, 90% [0.0322, 0.05]
--   top bucket                  41.4% with the width, 40.6% served
--   80% interval coverage       78.7% with the width, 88.8% served
--   mean width                  1.0058 C stored, 1.5282 C served
--
-- For this width only, this reverses the WXPredict build's D4 of 5 Oct (no
-- engine change while the build runs): Hassan's call.
--
-- What changes: `enabled` becomes true, with who and why written into the row.
-- `max_lead_days` stays 1, so only day-ahead prices change (what the study
-- and the score cover). probability_engine._station_width_for then prices
-- from the stored width where the centre is the station-corrected one, adds
-- `station_width:...` to the reasons, and forecast_provenance names
-- `+station-width:...` in priced_from. The model registry
-- (20261004210000) records the night's fit as served from then on.
--
-- Once only: a row that already carries `enabled_at` is left alone, so a
-- re-run never turns the switch back on after someone has turned it off.
-- settings comes from sql/ad4_00_preflight.sql, which the database contracts
-- never apply, hence the guard. tests/database/station-width.cjs holds it.

do $$
begin
  if to_regclass('public.settings') is null then
    return;
  end if;
  update public.settings
     set value = value || jsonb_build_object(
           'enabled', true,
           'enabled_at', '2026-10-10',
           'enabled_by', 'Hassan',
           'enabled_why', 'Hassan, 10 Oct: "YES TURN ON WIDT". P3.9_width_score 10 Oct 05:03Z, '
             || '29 Sep-9 Oct, 11 dates, 507 markets, verdict ready: log-loss gain +0.1171 per city-day, '
             || '90% [0.0749, 0.156]; Brier +0.0372 [0.0262, 0.048]; CRPS +0.0411 C [0.0322, 0.05]; '
             || 'top bucket 41.4% vs 40.6% served; 80% coverage 78.7% vs 88.8% served. Day-ahead only '
             || '(max_lead_days 1). Reverses the WXPredict build''s D4 of 5 Oct for this width only.'),
         updated_at = now()
   where key = 'station_width_pricing'
     and not (value ? 'enabled_at');
end $$;
