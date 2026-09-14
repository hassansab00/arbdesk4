-- Phase 2B hardening: cover the market foreign key used by retention and
-- integrity checks. The city foreign key is already covered by the leading
-- columns of weather_resolution_attempt_city_day_time.
begin;

create index if not exists weather_resolution_attempt_market
  on public.weather_resolution_attempts(market_id);

commit;
