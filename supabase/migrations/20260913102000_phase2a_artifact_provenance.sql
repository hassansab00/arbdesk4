-- Phase 2A hardening: prevent legacy outcome-derived artifacts from leaking
-- through the verified evidence boundary. Existing rows stay untouched;
-- consumers only accept rows explicitly stamped with this evidence scope.
begin;

alter table if exists public.derived_forecast_skill
  add column if not exists evidence_scope text;
alter table if exists public.derived_forecast_skill_model
  add column if not exists evidence_scope text;
alter table if exists public.derived_calibration_adjustment
  add column if not exists evidence_scope text;

-- evidence_id is already the primary key. The earlier three-column unique
-- index added no integrity and only increased insert/storage cost.
alter table if exists public.weather_resolution_evidence
  drop constraint if exists weather_resolution_evidence_city_key_for_date_evidence_id_key;

do $ad4$
begin
  if to_regclass('public.data_freshness_spec') is not null then
    insert into public.data_freshness_spec
      (table_name, ts_column, fresh_hours, layer, plain_english)
    values
      ('weather_resolution_evidence', 'captured_at', null, 'databank',
       'Versioned final station-authority evidence for city-day maximums.')
    on conflict (table_name) do update set
      ts_column = excluded.ts_column,
      fresh_hours = excluded.fresh_hours,
      layer = excluded.layer,
      plain_english = excluded.plain_english;
  end if;
end
$ad4$;

create or replace function public.refresh_calibration_adjustment(
  p_min_days int default 30,
  p_lookback int default 365,
  p_floor numeric default 0.75,
  p_ceiling numeric default 2.5
) returns jsonb
language plpgsql security definer
set search_path = public, pg_temp
as $ad4$
declare
  v_rows int := 0;
  v_applied int := 0;
begin
  if to_regclass('public.derived_calibration_adjustment') is null then
    return jsonb_build_object('ok', false,
      'error', 'derived_calibration_adjustment is not installed');
  end if;

  with z as (
    select distinct on (b.city_key, b.for_date)
      b.city_key,
      b.for_date,
      (b.observed_max_c - b.forecast_max_c) / nullif(b.sigma_c, 0) as z
    from public.v_verified_fact_band_outcome b
    where b.observed_max_c is not null
      and b.forecast_max_c is not null
      and b.sigma_c is not null and b.sigma_c > 0
      and b.for_date >= current_date - p_lookback
    order by b.city_key, b.for_date, b.captured_at desc
  ), agg as (
    select city_key, count(*)::int as n_days,
           stddev_samp(z) as z_sd, avg(z) as z_mean
    from z where z is not null group by city_key
  ), decided as (
    select a.*,
      case
        when a.n_days < p_min_days or a.z_sd is null then 1.0
        when a.z_sd < 1.0 and a.n_days < 60 then 1.0
        when a.z_sd between 0.9 and 1.1 then 1.0
        else least(p_ceiling, greatest(p_floor, a.z_sd))
      end as mult,
      case
        when a.n_days < p_min_days
          then format('%s verified day(s); needs %s.', a.n_days, p_min_days)
        when a.z_sd is null then 'Not enough variation to measure a spread.'
        when a.z_sd < 1.0 and a.n_days < 60
          then 'Narrowing is not applied with fewer than 60 verified days.'
        when a.z_sd between 0.9 and 1.1
          then 'Honest within measurement error; no correction applied.'
        when a.z_sd > 1.15
          then format('Stated sigma was %s%% too narrow over %s verified day(s).',
                      round((a.z_sd - 1) * 100), a.n_days)
        when a.z_sd < 0.85
          then format('Stated sigma was %s%% too wide over %s verified day(s).',
                      round((1 - a.z_sd) * 100), a.n_days)
        else 'Honest within measurement error; no correction applied.'
      end as why
    from agg a
  )
  insert into public.derived_calibration_adjustment
    (city_key, n_days, z_sd, z_mean, sigma_multiplier, applied,
     evidence_scope, reason, computed_at)
  select city_key, n_days, round(z_sd, 3), round(z_mean, 3),
         round(mult, 3), mult <> 1.0, 'verified_outcomes_v1', why, now()
  from decided
  on conflict (city_key) do update set
    n_days = excluded.n_days,
    z_sd = excluded.z_sd,
    z_mean = excluded.z_mean,
    sigma_multiplier = excluded.sigma_multiplier,
    applied = excluded.applied,
    evidence_scope = excluded.evidence_scope,
    reason = excluded.reason,
    computed_at = excluded.computed_at;

  get diagnostics v_rows = row_count;
  select count(*) into v_applied
  from public.derived_calibration_adjustment
  where applied and evidence_scope = 'verified_outcomes_v1';

  return jsonb_build_object(
    'ok', true, 'cities', v_rows, 'applied', v_applied,
    'min_days', p_min_days, 'evidence_scope', 'verified_outcomes_v1');
end
$ad4$;

revoke all on function public.refresh_calibration_adjustment(int, int, numeric, numeric)
  from public, anon, authenticated;
grant execute on function public.refresh_calibration_adjustment(int, int, numeric, numeric)
  to service_role;

-- Keep diagnostic views on the same boundary as the engine. This is dynamic
-- because migration-contract fixtures intentionally install only the core
-- relations; production has the full analytical layer.
do $ad4$
begin
  if to_regclass('public.derived_forecast_skill_model') is not null
     and to_regclass('public.derived_forecast_skill') is not null then
    execute $view$
      create or replace view public.v_forecast_model_skill
      with (security_invoker = true)
      as
      with per_model as (
        select distinct on (city_key, model, lead_days)
          city_key, model, lead_days, n_days, mae_c, bias_c,
          mae_bands, pct_within_one_band, computed_at
        from public.derived_forecast_skill_model
        where evidence_scope = 'verified_outcomes_v1'
        order by city_key, model, lead_days, computed_at desc
      ), pooled as (
        select distinct on (city_key, lead_days)
          city_key, lead_days, mae_c as pooled_mae_c, n_days as pooled_n_days
        from public.derived_forecast_skill
        where evidence_scope = 'verified_outcomes_v1'
        order by city_key, lead_days, computed_at desc
      )
      select m.city_key, m.model, m.lead_days, m.n_days, m.mae_c,
             m.bias_c, m.mae_bands, m.pct_within_one_band,
             p.pooled_mae_c, p.pooled_n_days,
             round(m.mae_c - p.pooled_mae_c, 3) as vs_pooled_c,
             m.computed_at
      from per_model m
      left join pooled p using (city_key, lead_days)
    $view$;
    grant select on public.v_forecast_model_skill to anon, authenticated, service_role;
  end if;

  if to_regclass('public.cities') is not null
     and to_regclass('public.derived_forecast_skill') is not null
     and to_regclass('public.derived_calibration_adjustment') is not null then
    execute $view$
      create or replace view public.v_sigma_inputs
      with (security_invoker = true)
      as
      select c.city_key, sk.mae_c, sk.n_days as skill_days,
             round(sk.mae_c * 1.2533, 3) as base_sigma_c,
             coalesce(ca.sigma_multiplier, 1.0) as calibration_mult,
             ca.n_days as calibration_days, ca.z_sd,
             coalesce(ca.reason,
               'No verified outcomes yet - sigma uses no learned calibration.') as calibration_note,
             round(sk.mae_c * 1.2533 * coalesce(ca.sigma_multiplier, 1.0), 3)
               as sigma_after_calibration,
             'regime (per day) x forecast divergence (per day)' as further_multipliers
      from public.cities c
      left join (
        select distinct on (city_key) city_key, mae_c, n_days
        from public.derived_forecast_skill
        where evidence_scope = 'verified_outcomes_v1'
        order by city_key, computed_at desc
      ) sk on sk.city_key = c.city_key
      left join public.derived_calibration_adjustment ca
        on ca.city_key = c.city_key
       and ca.evidence_scope = 'verified_outcomes_v1'
      where coalesce(c.status, 'active') = 'active'
    $view$;
    grant select on public.v_sigma_inputs to anon, authenticated, service_role;
  end if;
end
$ad4$;

commit;
