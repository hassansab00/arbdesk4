-- ===========================================================================
-- ONE PREDICTION CONTRACT AND A VERSION REGISTRY (P2.2 part 1, 4 Oct 2026)
--
-- The external plan's P2.2 asks for one versioned prediction contract (model
-- family, artifact version, serving role, station, target local date, as-of
-- time, input provenance, raw forecast, priced centre, uncertainty, the full
-- ladder, the predicted top and the fallback state), and a registry in which
-- a refit is a candidate until promoted. docs/P22_PREDICTION_CONTRACT.md maps
-- every field to its source. Read-only: nothing served, priced or traded
-- changes here.
--
--   prediction_checkpoints.priced_from     the full label the call was priced
--   prediction_checkpoints.raw_forecast_c  from, the forecast maximum before
--   prediction_checkpoints.station         correction, and the station
--   variant_shadow_checkpoints.station     (cities.icao) as it stood at the
--                                          decision. Nullable; the tick fills
--                                          them from this PR on. No history
--                                          of cities exists, so an older call's
--                                          station can only be today's, and
--                                          the contract says so
--                                          (station_source; Codex on #304).
--   model_registry      append-only events: a family and version moving to a
--                       state for a horizon, with evidence and a rollback.
--   v_model_registry    the latest state of each family, version and horizon.
--   v_prediction_contract  every recorded call - the engine's, S10's and the
--                       engine variants' - in one shape.
--
-- The service role's alone for now; the page reads them in part 2.
-- Re-runnable.
-- ===========================================================================

alter table public.prediction_checkpoints add column if not exists priced_from text;
alter table public.prediction_checkpoints add column if not exists raw_forecast_c numeric;
alter table public.prediction_checkpoints add column if not exists station text;
alter table public.variant_shadow_checkpoints add column if not exists station text;

comment on column public.prediction_checkpoints.priced_from is
  'The full label the engine priced this call from (probability_engine reasons priced_from:...), naming the station-correction, MOS and width versions behind the centre. From 4 Oct (P2.2); null before.';
comment on column public.prediction_checkpoints.raw_forecast_c is
  'The forecast maximum before any correction (the engine''s forecast_max_c). From 4 Oct (P2.2); null before.';
comment on column public.prediction_checkpoints.station is
  'The settlement station (cities.icao) as it stood when the call was made. From 4 Oct (P2.2); null before.';
comment on column public.variant_shadow_checkpoints.station is
  'The station of the served call this row sits beside, as recorded on it. From 4 Oct (P2.2); null before.';

create table if not exists public.model_registry (
  event_id     bigint generated always as identity primary key,
  family       text        not null,
  version      text        not null,
  horizon      text        not null,
  state        text        not null,
  decided_at   timestamptz not null default now(),
  decided_by   text        not null,
  evidence     text        not null,
  rollback_to  text,
  note         text,
  constraint model_registry_state check (state in
    ('captured', 'fitted', 'shadow', 'eligible', 'served', 'retired')),
  constraint model_registry_evidence_named check (length(trim(evidence)) > 0)
);

comment on table public.model_registry is
  'P2.2: every move of a predictor version between captured, fitted, shadow, eligible, served and retired, per horizon, with the evidence, the rollback target and who decided. Append-only; v_model_registry reads the latest state.';

do $$
begin
  drop trigger if exists model_registry_immutable on public.model_registry;
  create trigger model_registry_immutable before update or delete on public.model_registry
    for each row execute function arbdesk_private.immutable_record();
  drop trigger if exists model_registry_no_truncate on public.model_registry;
  create trigger model_registry_no_truncate before truncate on public.model_registry
    for each statement execute function arbdesk_private.immutable_record();
end $$;

alter table public.model_registry enable row level security;
revoke all on public.model_registry from public, anon, authenticated, service_role;
grant select, insert on public.model_registry to service_role;

create or replace view public.v_model_registry with (security_invoker = true) as
select distinct on (family, version, horizon)
       family, version, horizon, state, decided_at, decided_by, evidence, rollback_to, note, event_id
  from public.model_registry
 order by family, version, horizon, decided_at desc, event_id desc;

comment on view public.v_model_registry is
  'P2.2: the latest registry state of each predictor family, version and horizon.';

revoke all on public.v_model_registry from public, anon, authenticated;
grant select on public.v_model_registry to service_role;

-- What is true on 4 Oct, each measured (docs/P22_PREDICTION_CONTRACT.md).
insert into public.model_registry (family, version, horizon, state, decided_by, evidence, rollback_to, note)
select v.family, v.version, v.horizon, v.state, v.decided_by, v.evidence, v.rollback_to, v.note
  from (values
    ('engine', 'station-corrected forecast path', 'all checkpoints', 'served', 'rule:nightly refit (Rule 11); Hassan 4 Oct',
     'prediction_checkpoints: P3.9 station correction with the P2.9 MOS blend at lead >= 1, the bias-corrected forecast at lead 0, the observed floor through the P3.1 atom',
     null, 'The nightly refits serve without a candidate step, by Hassan''s decision of 4 Oct: they stay automatic, bounded by Rule 11 (docs/P22_PREDICTION_CONTRACT.md).'),
    ('s10', 'rd1:2026-09-25:f5372ebb05', 'same day', 'shadow', 'plan v2 P7.4',
     's10_shadow_checkpoints since 27 Sep; P1.1 (docs/P11_INTRADAY_DIAGNOSIS_2026-10-04.md): better than the served ladder from noon on',
     null, 'Serving it is Hassan''s decision (P7.6/P7.7).'),
    ('s10', 'rd3:2026-09-25:555719d4a1', 'same day', 'shadow', 'docs/CHALLENGER_C_PREREG.md',
     'accepted on the 1 Aug - 25 Sep holdout against rd1; forward shadow since 3 Oct, first look from 25 Oct',
     'rd1:2026-09-25:f5372ebb05', null),
    ('engine_variant', 'da_floor:v1', 'same day', 'shadow', 'docs/P11_DA_FLOOR_PREREG.md',
     'variant_shadow_checkpoints from 4 Oct; first look at 20 dates (about 25 Oct)',
     null, null),
    ('calibration', 'temperature:T=1.141', 'all checkpoints', 'fitted', 'settings.calibration_map',
     'applies = false: the fitted map is recorded on every price and applied to none (ingest_log calibration, 4 Oct)',
     null, null),
    ('station_width', 'W2 per-city width', 'day ahead', 'shadow', 'settings.station_width_pricing',
     'the switch is off; P3.9_width_score had 5 of the 7 dates it needs on 4 Oct',
     null, null),
    ('forecast_postprocess', 'P3.4 bias and width cells', 'per city and lead', 'served', 'rule:P3.4 gate; Hassan 4 Oct',
     'v_forecast_postprocess_applied: 1 cell (1 city, lead 4), applied since its gate passed on 3 Oct',
     null, 'Promoted by its own nightly gate, without a candidate step; nightly fits stay automatic (Hassan, 4 Oct).'),
    ('trajectory', 'P3.4 trajectory', 'same day', 'fitted', 'rule:P3.4 gate',
     'applied = 0: the gate is unmet (ingest_log trajectory, 4 Oct)',
     null, null),
    ('weather_model', 'per-city fits', 'per city and lead', 'shadow', 'rule:model_promotion',
     'model_promotion on 4 Oct: 0 promoted, 195 shadow, 189 stale',
     null, null)
  ) as v(family, version, horizon, state, decided_by, evidence, rollback_to, note)
 where not exists (select 1 from public.model_registry r
                    where r.family = v.family and r.version = v.version
                      and r.horizon = v.horizon and r.state = v.state);

create or replace view public.v_prediction_contract with (security_invoker = true) as
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
         'floor_c',             v.floor_c,
         'q_down',              v.q_down,
         'q_up',                v.q_up,
         'served_calibrated',   v.served_calibrated)),
       null::numeric,
       v.day_ahead_centre_c,
       v.day_ahead_sigma_c,
       'sigma',
       v.probs, v.top_band_id, v.top_prob,
       null::text
  from public.variant_shadow_checkpoints v
  left join public.cities c on c.city_key = v.city_key;

comment on view public.v_prediction_contract is
  'P2.2: every recorded call in one shape - the engine''s served checkpoints, S10''s shadow ladders and the engine variants - with family, artifact and code version, serving role, station, target date, as-of time, input provenance, raw forecast, priced centre, uncertainty, ladder, top and fallback state (docs/P22_PREDICTION_CONTRACT.md).';

revoke all on public.v_prediction_contract from public, anon, authenticated;
grant select on public.v_prediction_contract to service_role;
