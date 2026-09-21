-- Retire taipei, for the reason jinan was retired on 2026-09-19.
--
-- NOTHING IS DELETED. Every observation, market, band, outcome and forecast
-- taipei ever produced stays exactly where it is, and the cities row stays
-- too - only `status` moves. common.get_cities() filters on status='active',
-- so one word stops collection, pricing and the desk, and putting it back is
-- the same one word.
--
-- THE VENUE RESOLVES IT OFF A SOURCE THIS DESK HAS NEVER READ.
--
-- Polymarket names the resolution source in each market's own rules text:
--
--     dallas     https://www.weather.gov/wrh/timeseries?site=kdal   NOAA
--     london     https://www.weather.gov/wrh/timeseries?site=eglc   NOAA
--     shanghai   https://www.weather.gov/wrh/timeseries?site=zspd   NOAA
--     hong_kong  https://www.weather.gov.hk/en/cis/climat.htm       HKO
--     taipei     https://www.wunderground.com/.../tw/taipei/RCSS    Weather Underground
--     jinan      https://www.wunderground.com/.../cn/jinan/ZSJN     Weather Underground
--
-- The desk has a WRH collector (49 cities, 1,005 verified days) and an HKO
-- collector (1 city, 19 days). It has no Weather Underground collector, so
-- taipei and jinan are the two cities whose outcome can never be verified.
--
-- WHAT THAT COSTS, measured 2026-09-21. fact_forecast_outcome needs a
-- verified station maximum; without one there is no measurable forecast
-- skill; without skill the probability engine sets pricing_eligible=false
-- with pricing_block_reason='no_verified_skill'. taipei holds exactly ONE
-- verified day in its whole history - 2026-08-24 - so:
--
--     22 of taipei's bands blocked on every pricing run
--     11 of jinan's blocked beside them, already retired
--     33 of 1,089 - every blocked probability in the run, both cities
--
-- And none of that is for want of data. taipei's inputs are healthy: 859
-- forecast rows, 347 observations in the last fourteen days, 24 to 28
-- readings on every one of them. It is a fully instrumented city the desk is
-- structurally unable to price, and it was left active while jinan - same
-- defect, same source - was retired two days ago.
--
-- REVERSING IT IS ONE UPDATE once a Weather Underground collector exists,
-- and that collector would revive jinan in the same move.
update public.cities
   set status = 'retired',
       updated_at = now()
 where city_key = 'taipei'
   and status <> 'retired';
