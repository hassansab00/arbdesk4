-- ===========================================================================
-- THE WIDTH AROUND THE CORRECTED CENTRE (plan v2.3 P3.9 part 3), SHADOW
--
-- P3.9 replaced the day-ahead centre with the station-corrected combination
-- and kept the engine's width, which was fitted to the raw forecast's errors.
-- docs/P39_SERVED_WIDTH_2026-09-27.md (#228) scored widths fitted to the
-- combination's own out-of-sample errors on the venue's ladders, day-ahead.
-- On the 9 dates when the served width was at today's level (17-25 Sep, 416
-- city-days), a per-city width shrunk to the pool gained +0.138 log loss per
-- city-day, 90% by date block [+0.105, +0.171], over the width served. Those
-- dates came before the design, so it runs in shadow first.
--
-- derived_station_width  per (city, lead): the width scripts/
--     station_correction.py fits each night. Rule 11: the pool is the prior;
--     it is bounded; a city needs a minimum sample to leave the pool; it
--     steps at most 10% a night from the width in force before that night
--     (prev_sigma_c, P5.14); and it carries a version. The table holds the
--     latest fit; the repository mirror keeps the history.
-- derived_corrected_forecast.width_c, width_version  the width for each open
--     day, stored beside the centre it describes.
-- band_probabilities.station_width_c  on every price where a stored width
--     existed, that width, whether or not it priced (sigma_c says what
--     priced). The nightly forward score compares the two widths on markets
--     that settle after the design froze.
-- settings.station_width_pricing  starts OFF. While it is on, the engine
--     prices from the width only where the centre is the combination, and
--     only at lead <= max_lead_days (1: what the study and the score
--     cover). It is turned on once the forward score's lower bound is above
--     zero.
--
-- Additive and re-runnable. Service role only.
-- ===========================================================================

create table if not exists public.derived_station_width (
  city_key        text        not null,
  lead_days       int         not null check (lead_days between 1 and 3),
  sigma_c         numeric     not null check (sigma_c > 0),
  fitted_sigma_c  numeric     not null check (fitted_sigma_c > 0),
  pooled_sigma_c  numeric     not null check (pooled_sigma_c > 0),
  mae_c           numeric     check (mae_c is null or mae_c >= 0),
  pooled_mae_c    numeric     not null check (pooled_mae_c >= 0),
  n               int         not null check (n >= 0),
  n_pooled        int         not null check (n_pooled > 0),
  version         text        not null,
  as_of           date        not null,
  prev_sigma_c    numeric,
  prev_as_of      date,
  computed_at     timestamptz not null default now(),
  primary key (city_key, lead_days),
  constraint derived_station_width_anchor_is_earlier check (prev_as_of is null or prev_as_of < as_of)
);

comment on table public.derived_station_width is
  'Plan v2.3 P3.9 part 3: per city and lead, the width (sigma, C) of the station-corrected combination, fitted nightly to its own out-of-sample errors: 1.2533 x the city''s mean absolute error shrunk to the lead''s pool (K 10), the pool below 5 days, bounded 0.5-3.0 C, stepped at most 10% a night from prev_sigma_c. fitted_sigma_c is the value before the step. The latest fit; the repository mirror keeps the history. Shadow until settings.station_width_pricing is on.';

alter table public.derived_station_width enable row level security;
revoke all on public.derived_station_width from public, anon, authenticated;
grant select, insert, update on public.derived_station_width to service_role;

alter table public.derived_corrected_forecast add column if not exists width_c numeric;
alter table public.derived_corrected_forecast add column if not exists width_version text;

comment on column public.derived_corrected_forecast.width_c is
  'Plan v2.3 P3.9 part 3: the width (sigma, C) derived_station_width holds for this city at this row''s lead, from the same night''s fit. null when no width was fitted.';
comment on column public.derived_corrected_forecast.width_version is
  'The version of width_c (station-width:<as_of>:<hash>); set exactly when width_c is.';

do $$
begin
  if not exists (select 1 from pg_constraint
                  where conname = 'derived_corrected_forecast_width_has_version'
                    and conrelid = 'public.derived_corrected_forecast'::regclass) then
    alter table public.derived_corrected_forecast
      add constraint derived_corrected_forecast_width_has_version
      -- "is not null" spelled out: a check passes on null, and width_c > 0 is null
      -- when width_c is
      check ((width_c is null and width_version is null)
             or (width_c is not null and width_c > 0 and width_version is not null));
  end if;
end $$;

alter table if exists public.band_probabilities add column if not exists station_width_c numeric;

do $$
begin
  if to_regclass('public.band_probabilities') is not null then
    comment on column public.band_probabilities.station_width_c is
      'Plan v2.3 P3.9 part 3: the stored station width (derived_corrected_forecast.width_c) for this price''s city-day, whether or not it priced; sigma_c is what priced. null where the centre was not the station-corrected combination or no width was stored.';
  end if;
end $$;

do $$
begin
  if to_regclass('public.settings') is null then return; end if;
  insert into public.settings (key, value)
  values ('station_width_pricing', jsonb_build_object(
    'enabled', false,
    'max_lead_days', 1,
    'why', 'Plan v2.3 P3.9 part 3: the width fitted to the station-corrected combination''s own errors. Study 17-25 Sep, 416 city-days, day-ahead, venue ladders: +0.138 log loss per city-day [+0.105, +0.171] over the width served (docs/P39_SERVED_WIDTH_2026-09-27.md). Those dates precede the design: on only once the nightly forward score''s lower bound is above zero.'))
  on conflict (key) do nothing;
end $$;

do $$
begin
  if to_regclass('public.data_freshness_spec') is not null then
    insert into public.data_freshness_spec (table_name, ts_column, fresh_hours, layer, plain_english)
    values ('derived_station_width', 'computed_at', 30, 'model',
            'How wide the station-corrected forecast''s distribution is per city and lead, fitted nightly to its own errors, bounded and stepped.')
    on conflict (table_name) do update set
      ts_column     = excluded.ts_column,
      fresh_hours   = excluded.fresh_hours,
      layer         = excluded.layer,
      plain_english = excluded.plain_english;
  end if;
end $$;
