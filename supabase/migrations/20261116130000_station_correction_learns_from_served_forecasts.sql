-- ===========================================================================
-- THE STATION CORRECTION LEARNS FROM THE FORECASTS IT IS APPLIED TO, AND
-- WEIGHTS EACH MODEL BY ITS SKILL AT THE CITY (own_w:v1)
--
-- Hassan, 16 Nov 2026: "yes for improved station bias learner".
--
-- P3.9 learned each model's station bias on the previous-runs record and
-- averaged the corrected models equally. own_w:v1 (docs/OWN_W_PREREG.md,
-- tools/own_w_forward.py, shadow since 15 Nov) learns the bias of leads 0-2
-- from the served forecasts (open-meteo-models-current) and weights each model
-- by its skill at the city. Measured before building (mean absolute error at
-- the station, rows served 12 Oct - 14 Nov,
-- data/eval/model_audit_2026-11-15/station_weights_study.json): lead 0 0.752 ->
-- 0.709 C, lead 1 0.892 -> 0.850, lead 2 0.994 -> 0.963. On the long record
-- (lead 1, 1 Oct 2025 - 15 Nov 2026, 19,592 city-days), with the fit ending two
-- days before the target day as at the evening call: 0.910 -> 0.883 C.
--
-- scripts/station_correction.py reads settings.station_correction_learner.
-- While it names own_w:v1 and is enabled, leads 0-2 are fitted that way and
-- every cell carries its weight; lead 3 keeps the previous-runs fit at equal
-- weight. Setting enabled to false returns every lead to the previous-runs fit
-- at equal weight on the next nightly run (the weights are written null).
--
-- Rule 11 (in the script): a weight's prior is 1 (the equal weight in force
-- before tonight), bounds 0.5 to 2.0, at most 0.10 a night from the weight in
-- force, 5 city-days before a cell leaves its pool, and the learner, its priors
-- and every weight are in the version.
-- ===========================================================================
alter table public.derived_station_correction add column if not exists learner text;
alter table public.derived_station_correction add column if not exists rel_weight numeric;
alter table public.derived_station_correction add column if not exists pooled_rel_weight numeric;
alter table public.derived_station_correction add column if not exists prev_rel_weight numeric;

do $$ begin
  if not exists (select 1 from pg_constraint where conname = 'derived_station_correction_rel_weight_bounds') then
    alter table public.derived_station_correction add constraint derived_station_correction_rel_weight_bounds
      check ((rel_weight is null or rel_weight between 0.5 and 2.0)
         and (pooled_rel_weight is null or pooled_rel_weight between 0.5 and 2.0)
         and (prev_rel_weight is null or prev_rel_weight between 0.5 and 2.0));
  end if;
end $$;

comment on column public.derived_station_correction.learner is
  'Which learner fitted this cell: p39 (previous-runs record, equal weight) or own_w:v1 (the served forecasts, skill-weighted).';
comment on column public.derived_station_correction.rel_weight is
  'own_w:v1: the source''s skill weight at the city in the combination, 0.5 to 2.0, at most 0.10 a night from prev_rel_weight; null under p39 (equal weight).';
comment on column public.derived_station_correction.pooled_rel_weight is
  'own_w:v1: the source''s pooled skill weight at this lead, the prior a city with fewer than 5 days keeps.';
comment on column public.derived_station_correction.prev_rel_weight is
  'The weight in force before as_of (dated prev_as_of), the anchor a rerun steps from.';

do $$ begin
  if to_regclass('public.settings') is null then return; end if;
  insert into public.settings (key, value)
  values ('station_correction_learner', jsonb_build_object(
    'enabled', true,
    'learner', 'own_w:v1',
    'decided_by', 'Hassan, 16 Nov 2026',
    'why', 'Served-forecast bias with skill weights: MAE at the station lead 0 0.752 -> 0.709 C, lead 1 0.892 -> 0.850, lead 2 0.994 -> 0.963 (rows served 12 Oct - 14 Nov); long record lead 1 at the evening call 0.910 -> 0.883 C.'))
  on conflict (key) do nothing;
end $$;
