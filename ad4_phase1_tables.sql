-- AD4 Phase 1 - skill measurement table. Run in the Supabase SQL editor.
-- DERIVED: recomputed daily, keeps history via computed_at. Never frozen.

create table if not exists derived_forecast_skill (
  city_key             text not null references cities(city_key),
  computed_at          timestamptz not null default now(),
  lead_days            int not null,
  n_days               int,
  mae_c                numeric,   -- mean absolute error, degrees C
  bias_c               numeric,   -- mean SIGNED error = the station bias term
  p90_abs_err_c        numeric,
  mae_bands            numeric,   -- MAE expressed in that city's band widths
  pct_within_one_band  numeric,
  band_width_c         numeric,
  primary key (city_key, computed_at, lead_days)
);

comment on column derived_forecast_skill.bias_c is
  'Mean signed error. Non-zero means the grid forecast systematically misses this station - the correctable, proprietary part.';
comment on column derived_forecast_skill.mae_bands is
  'MAE / band width. Under 1 favours concentration strategies; over 2 favours intraday and arbitrage.';

create index if not exists idx_skill_latest on derived_forecast_skill (city_key, lead_days, computed_at desc);
