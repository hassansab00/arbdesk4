-- ===========================================================================
-- THE HONEST STATION MODEL (plan v2.2 P2.9 part 2)
--
-- scripts/station_mos.py learns, per city and lead, how the station's maximum
-- departs from the seven public models' mean, from the honest training record
-- (data/training/previous_runs: only what the forecasts said beforehand). The
-- evidence before building, walk-forward Nov 2025-Sep 2026, lead 1, 48 cities,
-- 15,652 city-days: P3.9 as coded MAE 0.938 C, this model 0.918, their mean
-- 0.902 (better than P3.9 by 0.036 [0.031, 0.041], same whole degree +1.1 pts
-- [+0.5, +1.6], 11 of 11 months).
--
-- derived_mos_coefficients  per (city, lead): the coefficients in the
--     features' own units, the training days, whether the city used the pooled
--     fit, the fraction of last night's step taken (Rule 11), the version.
-- derived_mos_forecast      per open (city, day): the model's maximum, the
--     models' mean it corrects, P3.9's combination, their blend, the inputs,
--     both versions.
-- settings.station_mos_pricing  starts OFF. The engine prices days ahead from
--     the blend only once it is on, after the first nightly rows are checked.
--
-- Shadow tables: service role only. Re-runnable.
-- ===========================================================================

create table if not exists public.derived_mos_coefficients (
  city_key       text        not null,
  lead_days      int         not null,
  coef           jsonb       not null,
  n              int         not null check (n >= 0),
  pooled         boolean     not null,
  step_fraction  numeric     not null check (step_fraction > 0 and step_fraction <= 1),
  version        text        not null,
  as_of          date        not null,
  computed_at    timestamptz not null default now(),
  primary key (city_key, lead_days)
);

comment on table public.derived_mos_coefficients is
  'Plan v2.2 P2.9: per city and lead, the honest station model''s coefficients (station max minus the seven models'' mean, on inputs known beforehand), in the features'' own units; pooled when the city has fewer than MIN_CITY_DAYS; step_fraction < 1 when Rule 11''s nightly step limit held it back. The latest fit; the repository mirror keeps the history.';

create table if not exists public.derived_mos_forecast (
  city_key       text        not null,
  for_date       date        not null,
  lead_days      int         not null,
  mos_c          numeric     not null,
  base_c         numeric     not null,
  p39_c          numeric,
  blend_c        numeric,
  inputs         jsonb       not null,
  p39_version    text,
  version        text        not null,
  computed_at    timestamptz not null default now(),
  primary key (city_key, for_date)
);

comment on table public.derived_mos_forecast is
  'Plan v2.2 P2.9: per open city-day, the honest station model''s maximum (mos_c), the seven models'' mean it corrects (base_c), P3.9''s station-corrected combination (p39_c) and their mean (blend_c), with the inputs and both versions. Shadow until settings.station_mos_pricing is on.';

alter table public.derived_mos_coefficients enable row level security;
alter table public.derived_mos_forecast enable row level security;
revoke all on public.derived_mos_coefficients from public, anon, authenticated;
revoke all on public.derived_mos_forecast from public, anon, authenticated;
grant select, insert, update on public.derived_mos_coefficients to service_role;
grant select, insert, update on public.derived_mos_forecast to service_role;

do $$ begin
  if to_regclass('public.settings') is null then return; end if;
  insert into public.settings (key, value)
  values ('station_mos_pricing', jsonb_build_object(
    'enabled', false,
    'min_lead_days', 1,
    'max_age_hours', 36,
    'why', 'Plan v2.2 P2.9: walk-forward Nov 2025-Sep 2026, lead 1, 15,652 city-days: the mean of P3.9 and the honest station model beat P3.9 alone by 0.036 C MAE [0.031, 0.041], same whole degree +1.1 pts [+0.5, +1.6], 11 of 11 months. Lead 2: +0.027 C [0.022, 0.031], +0.6 pts [+0.2, +1.1], 10 of 11 months.'))
  on conflict (key) do nothing;
end $$;
