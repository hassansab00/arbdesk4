-- ===========================================================================
-- ad4_45_calibration_feedback.sql - CLOSE THE LOOP THAT WAS LEFT OPEN.
--
-- WHAT ALREADY LEARNS
-- -------------------
-- The desk does learn, and it is worth being precise about how, because the
-- answer to "is there a learning mechanism" is neither yes nor no:
--
--   databank.py        freezes settled days into fact_* - the evidence
--   measure_skill.py   measures mae_c and bias_c per city, model and lead
--   probability_engine reads BOTH back on every run:
--                        centre_corrected = centre - bias_c
--                        sigma            = mae_c * 1.2533 * multipliers
--   weather_model.py   fits a per-city correction and only keeps it when it
--                      beats persistence on held-out days
--
-- So the CENTRE and the WIDTH both improve as evidence accumulates. That is a
-- real closed loop and it has been there.
--
-- WHAT DID NOT
-- ------------
-- Whether the stated probabilities turn out to be HONEST. v_calibration has
-- measured it all along - when the desk says 70%, does it happen 70% of the
-- time - the Analytics page draws it, and nothing anywhere reads the answer.
-- A model can have excellent mae_c and still be systematically overconfident:
-- the centre is right and the spread is too narrow. That is the expensive
-- kind of wrong, because every edge computed from a too-narrow distribution
-- is overstated, and the desk sizes up on exactly the trades it should not.
--
-- THE MEASUREMENT
-- ---------------
-- For every settled day the archive holds what was forecast, the sigma
-- claimed at the time, and what actually happened. The standardised error
--
--     z = (observed_max_c - forecast_max_c) / sigma_c
--
-- has standard deviation 1 if and only if sigma was right. sd(z) = 1.4 means
-- the true spread is 40% wider than claimed, and multiplying sigma by 1.4
-- makes it right. That is the whole method: no fitting, no parameters, one
-- number per city with its own sample size attached.
--
-- THE GUARDS, because a feedback loop with no brakes is how a desk talks
-- itself into a corner:
--
--   n < min_days        -> multiplier is exactly 1.0. Not "roughly" - a
--                          half-measured correction is worse than none.
--   clamped [0.75, 2.5] -> a wild sd from a handful of freak days cannot
--                          collapse or explode the book.
--   0.9 to 1.1 -> 1.0     -> a measurement of 0.96 is sampling error, not a
--                          finding. Applying it moves every price on every
--                          recompute for no reason.
--   never below 1 until  -> under 60 days a multiplier < 1 (claiming the desk
--   there is real data     is MORE certain than it said) is not applied at
--                          all. Widening on thin evidence is cautious;
--                          narrowing on thin evidence is how you get hurt.
--
-- RUN ORDER: after ad4_18_databank.sql. Re-runnable. Creates a table, a
-- function that fills it, and two views.
-- ===========================================================================

-- --------------------------------------------------------------------------
-- 1. Where the answer lives.
-- --------------------------------------------------------------------------
create table if not exists derived_calibration_adjustment (
  city_key         text primary key,
  n_days           int         not null,
  z_sd             numeric,             -- the raw measurement
  z_mean           numeric,             -- leftover bias, in sigmas
  sigma_multiplier numeric     not null default 1.0,   -- what is applied
  applied          boolean     not null default false, -- did it clear the bar
  evidence_scope   text,
  reason           text        not null,
  computed_at      timestamptz not null default now()
);

alter table derived_calibration_adjustment
  add column if not exists evidence_scope text;

comment on table derived_calibration_adjustment is
  'Per city: how wide the desk''s stated sigma actually turned out to be, measured as the standard deviation of (observed - forecast)/sigma over settled days. 1.0 means honest. Above 1 means overconfident, and the multiplier corrects it.';

create index if not exists dca_computed on derived_calibration_adjustment (computed_at desc);


-- --------------------------------------------------------------------------
-- 2. Measure it.
--
--    A function rather than a view because probability_engine.py reads this
--    on every run and must not pay for the aggregate each time - and because
--    the answer should only change when someone decides to recompute it, not
--    silently between two runs of the same engine.
-- --------------------------------------------------------------------------
create or replace function refresh_calibration_adjustment(
  p_min_days   int     default 30,
  p_lookback   int     default 365,
  p_floor      numeric default 0.75,
  p_ceiling    numeric default 2.5
) returns jsonb
language plpgsql security definer
set search_path = public, pg_temp
as $ad4$
declare
  v_rows int := 0;
  v_applied int := 0;
begin
  if to_regclass('public.v_verified_fact_band_outcome') is null then
    return jsonb_build_object('ok', false,
      'error', 'verified outcome projection does not exist - apply the Phase 2A outcome-truth migration');
  end if;

  with z as (
    -- One row per settled CITY-DAY, not per band: every band of a day shares
    -- one forecast and one sigma, so counting them all would multiply the
    -- sample size by nine and make thin evidence look decisive.
    select distinct on (b.city_key, b.for_date)
      b.city_key,
      b.for_date,
      (b.observed_max_c - b.forecast_max_c) / nullif(b.sigma_c, 0) as z
    from v_verified_fact_band_outcome b
    where b.observed_max_c is not null
      and b.forecast_max_c is not null
      and b.sigma_c is not null and b.sigma_c > 0
      and b.for_date >= current_date - p_lookback
    order by b.city_key, b.for_date, b.captured_at desc
  ),
  agg as (
    select city_key,
           count(*)::int          as n_days,
           stddev_samp(z)         as z_sd,
           avg(z)                 as z_mean
      from z
     where z is not null
     group by city_key
  ),
  decided as (
    select
      a.*,
      -- The bar, in the order it decides.
      case
        when a.n_days < p_min_days then 1.0
        when a.z_sd is null then 1.0
        -- Narrowing sigma says "the desk is MORE certain than it claimed".
        -- On under 60 days that is a claim thin evidence cannot support, and
        -- the cost of being wrong about it is asymmetric.
        when a.z_sd < 1.0 and a.n_days < 60 then 1.0
        -- Within measurement noise of honest, leave it alone. A multiplier of
        -- 0.96 is not a finding, it is the sampling error on a few hundred
        -- days - and applying it means every price moves for no reason on
        -- every recompute, which makes the desk look unstable and is
        -- impossible to reason about.
        when a.z_sd between 0.9 and 1.1 then 1.0
        else least(p_ceiling, greatest(p_floor, a.z_sd))
      end as mult,
      case
        when a.n_days < p_min_days
          then format('%s settled day(s) - needs %s before a correction is worth applying.', a.n_days, p_min_days)
        when a.z_sd is null
          then 'Not enough variation to measure a spread.'
        when a.z_sd < 1.0 and a.n_days < 60
          then format('Measured %s, which would narrow the distribution - not applied under 60 days. Widening on thin evidence is cautious; narrowing on it is not.', round(a.z_sd, 2))
        when a.z_sd between 0.9 and 1.1
          then format('Honest within measurement error over %s day(s) - measured %s, which is inside the noise band. Nothing applied.', a.n_days, round(a.z_sd, 2))
        when a.z_sd > 1.15
          then format('Stated sigma is %s%% too narrow over %s day(s) - the desk has been overconfident, and every edge computed from it was overstated by roughly that much.',
                      round((a.z_sd - 1) * 100), a.n_days)
        when a.z_sd < 0.85
          then format('Stated sigma is %s%% too wide over %s day(s) - the desk has been underconfident and has been filtering out real edges.',
                      round((1 - a.z_sd) * 100), a.n_days)
        else format('Honest within measurement error over %s day(s). Nothing to correct.', a.n_days)
      end as why
    from agg a
  )
  insert into derived_calibration_adjustment
        (city_key, n_days, z_sd, z_mean, sigma_multiplier, applied, evidence_scope, reason, computed_at)
  select city_key, n_days, round(z_sd, 3), round(z_mean, 3),
         round(mult, 3), (mult <> 1.0), 'verified_outcomes_v1', why, now()
    from decided
  on conflict (city_key) do update set
    n_days = excluded.n_days, z_sd = excluded.z_sd, z_mean = excluded.z_mean,
    sigma_multiplier = excluded.sigma_multiplier, applied = excluded.applied,
    evidence_scope = excluded.evidence_scope,
    reason = excluded.reason, computed_at = excluded.computed_at;

  get diagnostics v_rows = row_count;
  select count(*) into v_applied
    from derived_calibration_adjustment
   where applied and evidence_scope = 'verified_outcomes_v1';

  return jsonb_build_object('ok', true, 'cities', v_rows, 'applied', v_applied,
                            'min_days', p_min_days);
end;
$ad4$;

comment on function refresh_calibration_adjustment(int, int, numeric, numeric) is
  'Measures, per city, how wide the desk''s stated sigma actually was over settled days, and stores the multiplier that would make it honest. probability_engine.py applies it on the next run.';


-- --------------------------------------------------------------------------
-- 3. EVERY multiplier that goes into sigma, in one place.
--
--    sigma is a product of four things and they came from four files. When a
--    number looks wrong the first question is which factor moved, and until
--    now that could only be answered by reading three Python modules.
-- --------------------------------------------------------------------------
create or replace view v_sigma_inputs as
select
  c.city_key,
  sk.mae_c,
  sk.n_days                                        as skill_days,
  round(sk.mae_c * 1.2533, 3)                      as base_sigma_c,
  coalesce(ca.sigma_multiplier, 1.0)               as calibration_mult,
  ca.n_days                                        as calibration_days,
  ca.z_sd,
  coalesce(ca.reason, 'No settled outcomes yet - sigma is measured skill alone.')
                                                   as calibration_note,
  round(sk.mae_c * 1.2533 * coalesce(ca.sigma_multiplier, 1.0), 3)
                                                   as sigma_after_calibration,
  -- The regime and divergence multipliers are decided per city-DAY at pricing
  -- time, so they are named here rather than valued: this view is about the
  -- part that is a property of the city.
  'regime (per day, ad4_phase2) x forecast divergence (per day, ad4_16)'
                                                   as further_multipliers
from cities c
left join (
  select distinct on (city_key) city_key, mae_c, n_days
    from derived_forecast_skill
   where evidence_scope = 'verified_outcomes_v1'
   order by city_key, computed_at desc
) sk on sk.city_key = c.city_key
left join derived_calibration_adjustment ca
  on ca.city_key = c.city_key
 and ca.evidence_scope = 'verified_outcomes_v1'
where coalesce(c.status, 'active') = 'active';

comment on view v_sigma_inputs is
  'Every factor in the width of the desk''s distribution, per city: measured error, the calibration correction and why it was or was not applied, and which further per-day multipliers follow. sigma is a product of four things from four files; this is where to look when one of them moves.';


-- --------------------------------------------------------------------------
-- 4. Grants.
-- --------------------------------------------------------------------------
do $ad4$
declare o text; r text;
begin
  foreach o in array array['derived_calibration_adjustment', 'v_sigma_inputs'] loop
    foreach r in array array['anon', 'authenticated', 'service_role'] loop
      if exists (select 1 from pg_roles where rolname = r) then
        execute format('grant select on %I to %I', o, r);
      end if;
    end loop;
  end loop;
  if exists (select 1 from pg_roles where rolname = 'service_role') then
    execute 'grant all on derived_calibration_adjustment to service_role';
    execute 'grant execute on function refresh_calibration_adjustment(int, int, numeric, numeric) to service_role';
  end if;
  -- NOT anon: it is a full pass over the evidence table, and the browser has
  -- no reason to be able to start one. Same rule as refresh_feature_cache.
end
$ad4$;

select refresh_calibration_adjustment();

do $ad4$
declare v record;
begin
  select count(*) as n, count(*) filter (where applied) as ap,
         round(avg(sigma_multiplier), 2) as avg_mult
    into v from derived_calibration_adjustment;
  raise notice 'ad4_45: % city/cities measured, % with a correction applied (mean multiplier %).',
    coalesce(v.n, 0), coalesce(v.ap, 0), coalesce(v.avg_mult, 1.0);
  raise notice 'ad4_45: probability_engine.py applies it on its next run. Read v_sigma_inputs to see every factor in sigma per city.';
end
$ad4$;
