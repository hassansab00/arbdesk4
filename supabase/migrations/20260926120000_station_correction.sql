-- ===========================================================================
-- EACH FORECAST SOURCE'S ERROR AT THE STATION, AND THEIR CORRECTED COMBINATION
-- (plan v2.2 P3.9)
--
-- The public models are the starting point; what AD4 learns is how wrong each
-- one is at the settlement station. Measured 26 Sep before building (scratch
-- experiment, fitted on 2-19 Sep, scored on 317 city-days of 20-26 Sep): the
-- raw public forecast issued before 08:00 local had MAE 1.390 C; with a shrunk
-- city bias (about what the engine does) 1.237; the seven models each with
-- its own shrunk station bias, equal-weighted and averaged with that, 1.068.
-- Gain over the city-bias line 0.168 C (95% by date bootstrap 0.126-0.230),
-- same whole degree +5.3 points (+2.7 to +7.9). Seven test dates: a reason to
-- build and keep measuring, not proof.
--
-- derived_station_correction  one row per (city, source, lead): the learned
--     bias (station max - source max), the pooled bias it is shrunk toward,
--     its sample, and the fit's version. The latest fit; history is mirrored
--     to the repository nightly.
-- derived_corrected_forecast  one row per (city, target day): the
--     equal-weight combination of the corrected sources for the open days,
--     its spread across sources, and the version that made it.
--
-- SHADOW. Nothing prices from either table. scripts/station_correction.py
-- scores itself walk-forward every night (logged), and the engine reads the
-- combination only after it has beaten the current input on later dates
-- (P3.4's gate, P7.3's replay).
-- ===========================================================================

create table if not exists public.derived_station_correction (
  city_key       text        not null,
  source         text        not null,
  lead_days      int         not null,
  bias_c         numeric     not null,
  pooled_bias_c  numeric     not null,
  n              int         not null check (n >= 0),
  n_pooled       int         not null check (n_pooled >= 0),
  version        text        not null,
  as_of          date        not null,
  computed_at    timestamptz not null default now(),
  primary key (city_key, source, lead_days)
);

comment on table public.derived_station_correction is
  'Plan v2.2 P3.9: per city, forecast source and lead, the learned station bias (station max minus source max), shrunk toward the source''s pooled bias, bounded, and stepped at most MAX_STEP_C per night. The latest fit; the repository mirror keeps its history. Shadow: nothing prices from it yet.';

create table if not exists public.derived_corrected_forecast (
  city_key       text        not null,
  for_date       date        not null,
  lead_days      int         not null,
  combined_c     numeric     not null,
  spread_c       numeric,
  n_sources      int         not null check (n_sources > 0),
  sources        jsonb       not null,
  version        text        not null,
  computed_at    timestamptz not null default now(),
  primary key (city_key, for_date)
);

comment on table public.derived_corrected_forecast is
  'Plan v2.2 P3.9: per open city-day, the equal-weight combination of every source after its station correction, the spread across the corrected sources, and the fit version. Shadow until it beats the engine''s current input on later dates.';

alter table public.derived_station_correction enable row level security;
alter table public.derived_corrected_forecast enable row level security;
revoke all on public.derived_station_correction from public, anon, authenticated;
revoke all on public.derived_corrected_forecast from public, anon, authenticated;
grant select, insert, update on public.derived_station_correction to service_role;
grant select, insert, update on public.derived_corrected_forecast to service_role;
