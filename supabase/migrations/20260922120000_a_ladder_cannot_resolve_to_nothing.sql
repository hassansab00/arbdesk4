-- ===========================================================================
-- ELEVEN BANDS, NONE OF THEM WON
--
-- A daily-high ladder is mutually exclusive and exhaustive: the open tails
-- mean every possible maximum lands in exactly one band. So a market-day whose
-- every band is settled_yes = false is not an outcome. It is a gap wearing an
-- outcome's clothes.
--
-- Measured 2026-09-22 on fact_band_outcome: 410 of 1,106 market-days, 37% of
-- the table, every one of them resolution_date 2026-08-26 to 09-05 and frozen
-- between 09-04 and 09-08.
--
--     market-days with exactly one winner      696   avg 10.96 bands frozen
--     market-days with NO winner recorded      410   avg 10.49 bands frozen
--     market-days with two or more winners       0
--
-- HOW IT HAPPENED. scripts/databank.py froze each band the moment the venue
-- confirmed it, and common.upsert() is ignore-duplicates - so whatever landed
-- first is the record for ever. The venue confirms a band that CANNOT win
-- before it confirms the one that did: a band strictly below the running
-- maximum is decidable hours before the day ends. A run landing in that gap
-- froze the losers, marked them done, and never came back for the winner. The
-- half-band difference in the averages above is the winner, missing.
--
-- databank.py now freezes a MARKET-DAY whole or not at all - every band
-- confirmed, exactly one winner - so this cannot recur. That fix cannot repair
-- the 410: v_venue_band_resolution is a live view over venue data that has
-- since been pruned (it holds 2.8 to 4.3 rows per market-day today, against
-- the 11 that were frozen), so what the venue said is no longer recoverable.
--
-- AND IT MUST NOT BE COMPUTED. The desk settles on the VENUE's record, never
-- on its own observed maximum - that separation is the whole point of
-- v_venue_band_resolution. Filling these in from observed_max_c would be
-- inventing settlement evidence and calling it history.
--
-- So they are NAMED instead. An absence that reads as a fact is the defect;
-- an absence that says so is just an absence. Nothing is deleted.
--
-- WHY v_verified_fact_band_outcome DOES NOT ALREADY CATCH THIS. It checks each
-- band against the venue ROW BY ROW (`f.settled_yes is not distinct from
-- br.settled_yes`) and has no ladder-level check at all, so eleven agreeing
-- falses pass it one at a time. It also re-joins the pruned venue views, which
-- is why it now admits almost nothing. Coherence is a property of the LADDER
-- and has to be asked of the ladder.
-- ===========================================================================

create or replace view public.v_band_outcome_coherence
with (security_invoker = true)
as
select
  f.city_key,
  f.for_date,
  count(*)::int                                    as bands_frozen,
  count(*) filter (where f.settled_yes)::int       as winners,
  min(f.captured_at)                               as first_frozen_at,
  max(f.captured_at)                               as last_frozen_at,
  (count(*) filter (where f.settled_yes) = 1)      as coherent,
  case
    when count(*) filter (where f.settled_yes) = 1 then 'one winner'
    when count(*) filter (where f.settled_yes) = 0 then
      'NO WINNER RECORDED - a mutually exclusive ladder cannot resolve this way. '
      'Frozen before the venue confirmed the winning band; see the migration.'
    else format('%s WINNERS - two bands cannot both contain one maximum',
                count(*) filter (where f.settled_yes))
  end                                              as verdict
from public.fact_band_outcome f
group by f.city_key, f.for_date;

comment on view public.v_band_outcome_coherence is
  'One row per settled market-day, saying whether its frozen ladder resolves to exactly one winner. A day with no winner is a freeze that happened before the venue confirmed the winner, not a day on which nothing won - 410 of 1,106 as of 2026-09-22, all of them 08-26 to 09-05.';

-- WHAT EVERY CONSUMER SHOULD READ. Same columns as fact_band_outcome, minus
-- the market-days whose ladder does not resolve. Calibration, forecast skill,
-- the strategy mark and every backtest are measuring against settlement
-- evidence, and a day where nothing won drags every one of them toward
-- "the desk is wrong" for a reason that has nothing to do with the desk.
create or replace view public.v_coherent_band_outcome
with (security_invoker = true)
as
select f.*
from public.fact_band_outcome f
join public.v_band_outcome_coherence c
  on c.city_key = f.city_key and c.for_date = f.for_date
where c.coherent;

comment on view public.v_coherent_band_outcome is
  'fact_band_outcome restricted to market-days whose ladder resolves to exactly one winner. The honest evidence base: 696 of 1,106 market-days as of 2026-09-22.';

grant select on public.v_band_outcome_coherence to anon, authenticated, service_role;
grant select on public.v_coherent_band_outcome  to anon, authenticated, service_role;


-- --------------------------------------------------------------------------
-- AND "VERIFIED" HAS TO MEAN VERIFIED.
--
-- v_verified_fact_band_outcome is the view scripts/calibration.py fits the
-- haircut on, sql/ad4_45_calibration_feedback reads, and the Predictive page
-- displays. It checked each band against the venue ROW BY ROW and had no
-- ladder-level check, so eleven agreeing falses passed it one at a time.
--
-- Measured 2026-09-22, before this change:
--
--     rows the view admitted                    3,288
--     of those, on a ladder that resolved       1,844
--     of those, on a ladder that did NOT        1,444   (44%)
--
-- Forty-four per cent of the evidence behind the calibration haircut was
-- market-days where every band lost. That is not a neutral sample: it is
-- every band a loss, so it drags the fitted map toward "the model is far too
-- confident" for a reason that has nothing to do with the model. The haircut
-- adjusts every price the desk shows.
--
-- Same columns, same name, fewer rows - so every existing reader gets the fix
-- without knowing about it, and `create or replace` is safe because nothing
-- is added or reordered.
create or replace view public.v_verified_fact_band_outcome
with (security_invoker = true)
as
select f.*
from public.fact_band_outcome f
join public.bands b on b.band_id = f.band_id
join public.v_venue_band_resolution br on br.band_id = f.band_id
join public.v_venue_market_resolution mr on mr.market_id = b.market_id
join public.v_band_outcome_coherence c
  on c.city_key = f.city_key and c.for_date = f.for_date
where br.resolution_state = 'confirmed'
  and mr.resolution_state = 'confirmed'
  and c.coherent
  and f.settled_yes is not distinct from br.settled_yes;

comment on view public.v_verified_fact_band_outcome is
  'Band outcomes the venue confirms band by band AND whose market-day ladder resolves to exactly one winner. Both checks are needed: the row-by-row one cannot see that a whole ladder came back empty.';
