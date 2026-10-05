-- ===========================================================================
-- THE ENGINE'S STATION-CORRECTED PATH ON THE DAY ITSELF, IN SHADOW
-- (sd_corr:v1, 5 Oct 2026)
--
-- docs/SD_CORR_PREREG.md pre-registers a forward test of `sd_corr`: at each
-- same-day checkpoint, the station-corrected centre and the station width live
-- at the decision (derived_corrected_forecast.combined_c and width_c), cut by
-- the served floor. scripts/variant_shadow.py writes it beside da_floor:v1.
-- Nothing here prices or trades.
--
-- 1. variant_shadow_checkpoints names each variant's own inputs: the corrected
--    row's centre, width, versions, computed_at, lead and source count for
--    sd_corr, the day-ahead call for da_floor. The day-ahead columns become
--    nullable, and one check holds each variant to its own inputs and none of
--    the other's. Rows already written are da_floor's and satisfy it.
-- 2. v_prediction_contract reads the variant's own centre and width, and its
--    provenance names the corrected row. Every row it returned before is
--    unchanged: the new keys are null on da_floor rows and stripped.
-- 3. The registry gains sd_corr:v1 as shadow and blind from its first row, as
--    da_floor: the page shows no past call or score before its first look.
--
-- Re-runnable. Read as anon after applying: v_prediction_lineup only.
-- ===========================================================================

alter table public.variant_shadow_checkpoints add column if not exists corrected_centre_c numeric;
alter table public.variant_shadow_checkpoints add column if not exists corrected_width_c numeric;
alter table public.variant_shadow_checkpoints add column if not exists corrected_version text;
alter table public.variant_shadow_checkpoints add column if not exists corrected_width_version text;
alter table public.variant_shadow_checkpoints add column if not exists corrected_computed_at timestamptz;
alter table public.variant_shadow_checkpoints add column if not exists corrected_lead_days integer;
alter table public.variant_shadow_checkpoints add column if not exists corrected_n_sources integer;

comment on column public.variant_shadow_checkpoints.corrected_centre_c is
  'sd_corr: derived_corrected_forecast.combined_c live at the decision (P3.9; lead 0 takes lead 1''s corrections).';
comment on column public.variant_shadow_checkpoints.corrected_width_c is
  'sd_corr: derived_corrected_forecast.width_c live at the decision (P3.9 part 3; lead 0 takes lead 1''s width).';
comment on column public.variant_shadow_checkpoints.corrected_version is
  'sd_corr: the station-correction fit the centre came from.';
comment on column public.variant_shadow_checkpoints.corrected_width_version is
  'sd_corr: the station-width fit the width came from.';
comment on column public.variant_shadow_checkpoints.corrected_computed_at is
  'sd_corr: when the corrected row was computed; never after the decision, never older than the engine''s max_age_hours.';
comment on column public.variant_shadow_checkpoints.corrected_lead_days is
  'sd_corr: the corrected row''s lead at its computation (1 when the newest runs were fetched before the city''s day began).';
comment on column public.variant_shadow_checkpoints.corrected_n_sources is
  'sd_corr: the corrected sources in the combination.';

alter table public.variant_shadow_checkpoints alter column day_ahead_centre_c drop not null;
alter table public.variant_shadow_checkpoints alter column day_ahead_sigma_c drop not null;
alter table public.variant_shadow_checkpoints alter column day_ahead_priced_at drop not null;

-- Each variant's own inputs and none of the other's. Every comparison is
-- guarded by its not-null test and the whole is coalesced, because a check
-- that evaluates to null passes.
do $$
begin
  if not exists (select 1 from pg_constraint where conname = 'variant_shadow_inputs_of_the_variant') then
    alter table public.variant_shadow_checkpoints add constraint variant_shadow_inputs_of_the_variant check (
      coalesce(case variant
        when 'da_floor' then
              day_ahead_centre_c is not null and day_ahead_sigma_c is not null and day_ahead_priced_at is not null
          and corrected_centre_c is null and corrected_width_c is null and corrected_version is null
          and corrected_width_version is null and corrected_computed_at is null
        when 'sd_corr' then
              corrected_centre_c is not null
          and corrected_width_c is not null and corrected_width_c > 0
          and corrected_version is not null and corrected_version like 'station-correction:%'
          and corrected_width_version is not null and corrected_width_version like 'station-width:%'
          and corrected_computed_at is not null and corrected_computed_at <= decided_at
          and day_ahead_centre_c is null and day_ahead_sigma_c is null and day_ahead_priced_at is null
        else true end, false));
  end if;
end $$;

comment on table public.variant_shadow_checkpoints is
  'Engine variants recorded beside each same-day tick checkpoint, never priced or traded. da_floor:v1 = the day-ahead call''s centre and width (the last band_probabilities pricing before local midnight) cut by the served floor with the engine''s q (docs/P11_DA_FLOOR_PREREG.md). sd_corr:v1 = the station-corrected centre and the station width live at the decision (derived_corrected_forecast) cut by the same floor and q (docs/SD_CORR_PREREG.md). scripts/variant_shadow.py writes both.';

-- The contract: as part 1 built it (20261004190000) and part 2 made it run as
-- owner (20261004200000), with the variant's own centre, width and provenance.
create or replace view public.v_prediction_contract with (security_invoker = false) as
select p.checkpoint_id::text                                  as prediction_id,
       'prediction_checkpoints'::text                         as recorded_in,
       'engine'::text                                         as model_family,
       coalesce(p.priced_from,
                p.model_path || coalesce(':' || p.forecast_model, '')) as artifact_version,
       p.engine_version                                       as code_version,
       'served'::text                                         as serving_role,
       p.city_key,
       coalesce(p.station, c.icao)                            as station,
       case when p.station is not null then 'recorded' else 'cities_now' end as station_source,
       p.target_date,
       p.checkpoint,
       p.decided_at                                           as as_of,
       jsonb_strip_nulls(jsonb_build_object(
         'forecast_issued_at', p.forecast_issued_at,
         'forecast_model',     p.forecast_model,
         'reading_at',         p.reading_at,
         'reading_source',     p.reading_source,
         'floor_c',            p.running_max_c))                 as input_provenance,
       p.raw_forecast_c,
       p.centre_c                                             as priced_centre_c,
       p.sigma_c                                              as uncertainty_c,
       'sigma'::text                                          as uncertainty_kind,
       p.probs,
       p.top_band_id,
       p.top_prob,
       case when not coalesce(p.inputs_ok, true) then coalesce(p.block_reason, 'inputs_not_ok')
            else p.model_path end                             as fallback_state
  from public.prediction_checkpoints p
  left join public.cities c on c.city_key = p.city_key
union all
select s.checkpoint_id::text, 's10_shadow_checkpoints', 's10',
       s.model_version, s.contract, 'shadow',
       s.city_key, coalesce(e.station, c.icao),
       case when e.station is not null then 'served_call' else 'cities_now' end,
       s.target_date, s.checkpoint, s.decided_at,
       jsonb_strip_nulls(coalesce(s.inputs, '{}'::jsonb) || jsonb_build_object('floor_c', s.running_max_c)),
       null::numeric,
       s.median_c,
       round((s.q90_c - s.q10_c) / 2.5631, 4),
       'q10_q90_as_sigma',
       s.probs, s.top_band_id, s.top_prob,
       null::text
  from public.s10_shadow_checkpoints s
  left join public.cities c on c.city_key = s.city_key
  -- S10 rows keep no station of their own: the served call's, recorded at
  -- the same city, date and checkpoint, and decided no later than the S10
  -- row - the nearest before it. A call recorded afterwards cannot lend a
  -- past row its station (review of #304).
  left join lateral (
    select p.station from public.prediction_checkpoints p
     where p.city_key = s.city_key and p.target_date = s.target_date
       and p.checkpoint = s.checkpoint and p.decided_at <= s.decided_at
     order by p.decided_at desc limit 1) e on true
union all
select v.shadow_id::text, 'variant_shadow_checkpoints', 'engine_variant',
       v.variant_version, v.engine_version, 'shadow',
       v.city_key, coalesce(v.station, c.icao),
       case when v.station is not null then 'recorded' else 'cities_now' end,
       v.target_date, v.checkpoint, v.decided_at,
       jsonb_strip_nulls(jsonb_build_object(
         'day_ahead_priced_at', v.day_ahead_priced_at,
         'day_ahead_lead_days', v.day_ahead_lead_days,
         'corrected_version',       v.corrected_version,
         'corrected_width_version', v.corrected_width_version,
         'corrected_computed_at',   v.corrected_computed_at,
         'corrected_lead_days',     v.corrected_lead_days,
         'corrected_n_sources',     v.corrected_n_sources,
         'floor_c',             v.floor_c,
         'q_down',              v.q_down,
         'q_up',                v.q_up,
         'served_calibrated',   v.served_calibrated)),
       null::numeric,
       coalesce(v.day_ahead_centre_c, v.corrected_centre_c),
       coalesce(v.day_ahead_sigma_c, v.corrected_width_c),
       'sigma',
       v.probs, v.top_band_id, v.top_prob,
       null::text
  from public.variant_shadow_checkpoints v
  left join public.cities c on c.city_key = v.city_key;

comment on view public.v_prediction_contract is
  'P2.2: every recorded call in one shape - the engine''s served checkpoints, S10''s shadow ladders and the engine variants - with family, artifact and code version, serving role, station, target date, as-of time, input provenance, raw forecast, priced centre, uncertainty, ladder, top and fallback state (docs/P22_PREDICTION_CONTRACT.md).';

revoke all on public.v_prediction_contract from public, anon, authenticated;
grant select on public.v_prediction_contract to service_role;

-- Blind from its first row, as da_floor (20261004200000).
insert into public.model_registry (family, version, horizon, state, decided_by, evidence, rollback_to, note, blind)
select 'engine_variant', 'sd_corr:v1', 'same day', 'shadow', 'docs/SD_CORR_PREREG.md',
       'the pre-registration reads only counts before 20 target dates are scored (about 26 Oct)',
       null, 'blinded until the first look', true
 where not exists (select 1 from public.model_registry r
                    where r.family = 'engine_variant' and r.version = 'sd_corr:v1' and r.blind);
