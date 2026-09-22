-- ===========================================================================
-- A BANKED MAXIMUM HAS TO SAY WHICH THERMOMETER READ IT.
--
-- fact_band_outcome.observed_max_c had one column and two possible sources,
-- and no way to tell them apart:
--
--   the authority   scripts/weather_outcomes.py fetches the exact NOAA page
--                   each market's rules name, honours hourly_only, and keeps
--                   a payload hash. It covers about four city-days in five.
--   the station     our own IEM feed's routine-report maximum. It agrees with
--                   the venue's declared winner 90.6% of the roster-wide, and
--                   it covers every day we collected.
--
-- Until now only the first was banked, so one day in five carried no observed
-- maximum at all. Measured against the venue's own winners, the BANKED record
-- agreed 52.1% of the time while the reader that produced it scored 76.2% -
-- the gap was almost entirely rows with nothing in them, not rows with the
-- wrong thing in them.
--
-- So the fallback is switched on and this column says which answered. Without
-- it the two are one number, and they are not one number: a row sourced
-- 'station:KDAL' carries a different uncertainty from 'authority:KDAL', and
-- anything calibrating on this table needs to know which it is holding.
--
-- fact_forecast_outcome deliberately does NOT get the fallback. Its error_c
-- is what the model fitter trains against, and a target measured by two
-- different thermometers is not a target.
--
-- Idempotent: one nullable text column.
-- ===========================================================================

alter table public.fact_band_outcome
  add column if not exists obs_source text;

comment on column public.fact_band_outcome.obs_source is
  'Which thermometer produced observed_max_c: "authority:<station>" is the NOAA page this market''s rules name, "station:<icao>" is our own routine-report maximum. Null on rows banked before the two were told apart.';
