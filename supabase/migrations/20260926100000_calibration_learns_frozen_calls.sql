-- ===========================================================================
-- CALIBRATION LEARNS ONLY FROM PROBABILITIES FROZEN BEFORE THE DAY
--
-- scripts/calibration.py fitted its temperature on
-- v_verified_fact_band_outcome.model_prob, which databank freezes from the
-- LATEST pricing before settlement. Measured 26 Sep over the last 30 days of
-- fact_band_outcome: of 673 city-days, 0 were priced before the local day
-- began, 338 in its afternoon and 335 after it had ended - by then the engine
-- holds the running maximum. A calibration learned there describes the
-- engine reading the thermometer, and it would have been applied to the
-- forecasts the desk trades on as soon as the gate (30 settlement dates)
-- opened.
--
-- v_calibration_evidence is the same verified outcome for every band, beside
-- the RAW probability of the last pricing computed before the city's local day
-- began: the uncalibrated number the map is applied to, at the moment it is a
-- forecast. Bands never priced before their day are absent, not guessed.
--
-- Service role only: it feeds the fitter, not a page.
-- ===========================================================================

do $$
begin
  if to_regclass('public.v_verified_fact_band_outcome') is null
     or to_regclass('public.band_probabilities') is null then
    raise notice 'v_verified_fact_band_outcome or band_probabilities is not installed: v_calibration_evidence skipped';
    return;
  end if;

  create or replace view public.v_calibration_evidence as
  with l as (
    select f.band_id, f.city_key, f.for_date, f.settled_yes,
           (f.for_date::timestamp at time zone coalesce(c.timezone, 'UTC')) as day_starts_at
      from public.v_verified_fact_band_outcome f
      left join public.cities c on c.city_key = f.city_key
  )
  select l.band_id,
         l.city_key,
         l.for_date,
         p.raw_prob        as prob,
         p.computed_at     as priced_at,
         l.day_starts_at,
         l.settled_yes
    from l
    join lateral (
      select bp.raw_prob, bp.computed_at
        from public.band_probabilities bp
       where bp.band_id = l.band_id
         and bp.computed_at < l.day_starts_at
       order by bp.computed_at desc, bp.prob_id desc
       limit 1
    ) p on true;

  comment on view public.v_calibration_evidence is
    'Per verified settled band: the raw probability of the last pricing computed before the city''s local day began, and whether the band won. What scripts/calibration.py learns from (evidence_scope frozen_day_ahead_v1); nothing priced on the day itself is in it.';

  revoke all on public.v_calibration_evidence from public, anon, authenticated;
  grant select on public.v_calibration_evidence to service_role;
end $$;
