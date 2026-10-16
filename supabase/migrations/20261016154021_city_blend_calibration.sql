-- Per-city/lead uncertainty for the centre station_mos actually predicts.
-- Existing rows and their permissions remain unchanged. No calibration is
-- applied by this migration; station_mos writes chronological validation
-- evidence, and the engine requires that city's passing fit and the existing
-- station-width pricing switch. The prior-night anchor makes reruns bounded.
alter table public.derived_mos_coefficients
  add column if not exists calibration jsonb,
  add column if not exists prev_calibration jsonb;

alter table public.derived_mos_forecast
  add column if not exists calibration jsonb;

comment on column public.derived_mos_coefficients.calibration is
  'City/lead blend-width fit and later-date recipe validation, with prior, sample sizes, bounds, nightly step and version. Final refit forward performance is measured by recorded checkpoints.';
comment on column public.derived_mos_coefficients.prev_calibration is
  'Calibration in force before this fit night; paired with prev_as_of, so repeating a night cannot take another step.';
comment on column public.derived_mos_forecast.calibration is
  'The corresponding city/lead blend-width fit. Applies only when later-date validation passes; absent or held evidence preserves the existing width.';

-- Deliberately expose only learning diagnostics, never coefficients/inputs.
create or replace view public.v_city_learning with (security_invoker = false) as
select m.city_key, m.lead_days, m.as_of, m.n as training_days, m.pooled,
       m.step_fraction, m.version, m.computed_at,
       m.calibration,
       w.sigma_c as combination_width_c, w.prev_sigma_c as previous_width_c,
       w.n as width_days, w.as_of as width_as_of
from public.derived_mos_coefficients m
left join public.derived_station_width w using (city_key, lead_days);
revoke all on public.v_city_learning from public, anon, authenticated;
grant select on public.v_city_learning to anon, authenticated, service_role;
