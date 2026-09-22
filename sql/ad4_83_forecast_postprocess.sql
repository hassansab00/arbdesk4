-- ===========================================================================
-- ad4_83_forecast_postprocess.sql - WHERE THE CORRECTED DISTRIBUTION LIVES.
--
-- scripts/forecast_postprocess.py fits, per city and lead, the two numbers a
-- public point forecast needs before it is a probability:
--
--     centre = forecast - bias_c
--     sigma  = baseline_sigma_c * sigma_ratio
--
-- and this file is the table it writes and the views the engine and the UI
-- read back. No arithmetic happens here - the fit is Python because it is a
-- cross-validated optimisation over a shrinkage grid, and putting that in SQL
-- would be writing an optimiser in a language that has no business running
-- one.
--
-- WHY THE TABLE CARRIES ITS OWN BASELINE. `baseline_sigma_c` is mae * 1.2533
-- over the same days the fit saw - the width the desk publishes today. Storing
-- it beside the ratio means a row states both what it changed FROM and what it
-- changed TO, so "the correction moved this price" is answerable from one row
-- a month later. It also makes `sigma_ratio = 1.0` mean exactly one thing:
-- today's width was already right.
--
-- WHY `applied` IS A COLUMN AND NOT A FILTER. Every cell gets a row, fitted or
-- not. A cell that failed its held-out test is evidence - it says this city
-- and lead have nothing to correct yet - and deleting it would leave the UI
-- unable to distinguish "no gain found" from "never measured". The engine
-- reads v_forecast_postprocess_applied; a human reads the table.
--
-- RUN ORDER: after ad4_18_databank.sql (it fits on fact_forecast_outcome) and
-- alongside ad4_45_calibration_feedback.sql, which measures the same
-- over-dispersion from the other end. Re-runnable. Creates one table and two
-- views, and changes nothing until the fitter has run.
-- ===========================================================================

create table if not exists derived_forecast_postprocess (
  city_key          text        not null,
  lead_days         int         not null,

  -- What the fit saw.
  n_days            int         not null default 0,   -- settled days in THIS cell
  n_city_days       int         not null default 0,   -- the city's pool, across leads
  spread_days       int         not null default 0,   -- days with >1 model quoting
  first_day         date,
  last_day          date,
  evidence_scope    text,

  -- What it chose.
  bias_c            numeric     not null default 0,   -- subtract from the forecast
  sigma_ratio       numeric     not null default 1,   -- multiply the baseline width
  baseline_sigma_c  numeric,                          -- mae * 1.2533 over the same days
  k_cell            numeric,                          -- shrinkage toward the city
  k_city            numeric,                          -- shrinkage toward zero

  -- Whether it earned the right to be used.
  crps_fitted       numeric,
  crps_baseline     numeric,
  crps_gain         numeric,
  applied           boolean     not null default false,
  reason            text        not null default '',

  computed_at       timestamptz not null default now(),
  primary key (city_key, lead_days)
);

comment on table derived_forecast_postprocess is
  'Per city and lead: the bias to subtract from the public forecast and the factor to multiply its width by, fitted by empirical-Bayes shrinkage and kept only when it beats the uncorrected forecast on held-out day blocks by CRPS. Written by scripts/forecast_postprocess.py.';

comment on column derived_forecast_postprocess.bias_c is
  'Forecast minus observed, shrunk twice: the cell toward the city, the city toward zero. Positive means the forecast runs hot and the centre moves DOWN.';
comment on column derived_forecast_postprocess.sigma_ratio is
  'Multiplies baseline_sigma_c. Below 1 means the published distribution was too wide, which is what every one of 49 cities measured.';
comment on column derived_forecast_postprocess.applied is
  'False keeps the cell in shadow: the engine prices it exactly as it did before this table existed.';

create index if not exists dfp_applied on derived_forecast_postprocess (applied, city_key);
create index if not exists dfp_computed on derived_forecast_postprocess (computed_at desc);

-- Additive guard, so an older install gains the columns rather than failing.
do $$
declare r record;
begin
  for r in select * from (values
      ('spread_days','int not null default 0'),
      ('k_cell','numeric'),
      ('k_city','numeric'),
      ('evidence_scope','text'),
      ('crps_gain','numeric')
  ) as t(col, def) loop
    if not exists (select 1 from information_schema.columns
                   where table_schema='public'
                     and table_name='derived_forecast_postprocess'
                     and column_name=r.col) then
      execute format('alter table public.derived_forecast_postprocess add column %I %s',
                     r.col, r.def);
    end if;
  end loop;
end $$;


-- --------------------------------------------------------------------------
-- What the engine reads. One row per cell that passed, nothing else.
--
-- probability_engine.py joins on (city_key, lead_days) and, finding nothing,
-- prices exactly as it did before - which is why this view is the whole
-- integration surface and why an empty table is a safe state.
-- --------------------------------------------------------------------------
create or replace view v_forecast_postprocess_applied as
select city_key, lead_days, bias_c, sigma_ratio, baseline_sigma_c,
       n_days, n_city_days, crps_gain, computed_at
  from derived_forecast_postprocess
 where applied;

comment on view v_forecast_postprocess_applied is
  'The corrections the desk is entitled to use. Empty until scripts/forecast_postprocess.py finds a cell that beats the uncorrected forecast out of sample.';


-- --------------------------------------------------------------------------
-- What a human reads: is this working, and where is it not.
--
-- Ordered so the cells doing the most work come first - a large correction
-- with a large gain is the row worth understanding, and a large correction
-- with NO gain is the row worth distrusting.
-- --------------------------------------------------------------------------
create or replace view v_forecast_postprocess_health as
select
  p.city_key,
  p.lead_days,
  p.n_days,
  p.n_city_days,
  p.bias_c,
  p.sigma_ratio,
  p.baseline_sigma_c,
  round(p.baseline_sigma_c * p.sigma_ratio, 3)              as sigma_c,
  p.crps_baseline,
  p.crps_fitted,
  p.crps_gain,
  p.applied,
  case
    when not p.applied and p.n_days < 8               then 'waiting: too few settled days'
    when not p.applied                                then 'shadow: no held-out gain'
    when abs(p.bias_c) >= 1.0                         then 'applied: large station bias'
    when p.sigma_ratio <= 0.85                        then 'applied: distribution was too wide'
    when p.sigma_ratio >= 1.15                        then 'applied: distribution was too narrow'
    else                                                   'applied: small correction'
  end                                                        as verdict,
  p.reason,
  p.computed_at
from derived_forecast_postprocess p
order by p.applied desc, abs(coalesce(p.crps_gain, 0)) desc, p.city_key, p.lead_days;

comment on view v_forecast_postprocess_health is
  'Every fitted cell with a plain-language verdict. A row reading "shadow: no held-out gain" is a measurement, not a failure - it says this city and lead had nothing to correct.';
