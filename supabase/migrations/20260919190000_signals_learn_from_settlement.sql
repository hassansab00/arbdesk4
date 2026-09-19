-- ===========================================================================
-- THE LEARNING LOOP WAS OPEN AT THE FAR END
--
-- WHAT WAS WRONG
-- --------------
-- Measured 2026-09-19:
--
--   4,761 signals
--   2,217 of them on a band the venue has since settled
--   1,963 rows in fact_signal_outcome
--       0 of those rows with settled_yes
--
-- bank_signals() writes the fill and the P&L and never the band's actual
-- outcome, so nothing could measure which strategy, forecast version or
-- calibration made a CORRECT CALL - only which made money, which is a
-- different question and a much noisier one. A correct call that was never
-- filled looks identical to no signal at all.
--
-- Worse, the 1,963 rows are the wrong 1,963. bank_signals keys off a CLOSED
-- TRADE, so it banks a signal whose trade closed and skips one whose band
-- settled - and only 353 rows are in both sets. 1,864 signals on settled
-- bands have never been banked at all.
--
-- WHY THE EXISTING ROWS ARE NOT REWRITTEN
-- ---------------------------------------
-- fact_signal_outcome carries proprietary_fact_immutable: every UPDATE and
-- DELETE is refused, which is what makes these rows admissible evidence. A
-- migration that rewrote 1,963 frozen rows to add a column would be exactly
-- the edit that guard exists to prevent.
--
-- So the columns are added for rows written from now on, and the OUTCOME for
-- every row - old and new - is served by a view that joins the objective band
-- result. The frozen evidence is untouched and the loop closes for the whole
-- history immediately rather than only for signals fired after today.
--
-- SETTLED_YES IS THE BAND'S RESULT. SIGNAL_CORRECT IS THE CALL.
-- They are deliberately two columns. A NO signal on a band that lost is a
-- CORRECT call on a band whose settled_yes is false, and collapsing them into
-- one number would score every NO signal backwards.
--
-- THREE TIMESTAMPS, ALL DIFFERENT. fired_at is when the desk decided.
-- settled_at is when the venue's evidence says the outcome was confirmed.
-- captured_at is when this row was frozen. Measuring how long a call took to
-- be proved right needs the first two, and neither is the third.
-- ===========================================================================
begin;

alter table public.fact_signal_outcome
  add column if not exists signal_correct      boolean;
alter table public.fact_signal_outcome
  add column if not exists settled_at          timestamptz;
alter table public.fact_signal_outcome
  add column if not exists outcome_source      text;
alter table public.fact_signal_outcome
  add column if not exists forecast_version    uuid;
alter table public.fact_signal_outcome
  add column if not exists calibration_version uuid;
alter table public.fact_signal_outcome
  add column if not exists cost_version        uuid;

comment on column public.fact_signal_outcome.settled_yes is
  'The BAND''s objective result from venue-confirmed evidence - did the day land in this bucket. Never a partially observed running maximum, which is a number that is still moving.';
comment on column public.fact_signal_outcome.signal_correct is
  'Whether the CALL was right, which is not the same question. A NO signal on a band that lost is correct and its settled_yes is false.';
comment on column public.fact_signal_outcome.settled_at is
  'When the venue''s evidence confirmed the outcome. fired_at is when the desk decided and captured_at is when this row was frozen; all three are different moments.';
comment on column public.fact_signal_outcome.outcome_source is
  'Where settled_yes came from, so a row whose outcome was joined rather than frozen with it says so.';


-- --------------------------------------------------------------------------
-- THE OUTCOME OF EVERY BANKED SIGNAL, frozen or joined.
--
-- A row written before this migration has no settled_yes and never can have -
-- the guard refuses the update. The band's outcome is still known, in
-- fact_band_outcome, keyed by the same band. Joining it here closes the loop
-- for all 1,963 without touching one byte of frozen evidence, and
-- outcome_source says which of the two a reader is looking at.
-- --------------------------------------------------------------------------
create or replace view v_signal_outcome as
select
  f.signal_id,
  f.strategy_id,
  f.band_id,
  f.city_key,
  f.for_date,
  f.side,
  f.action,
  f.reason,
  f.severity,
  f.status,
  f.filled,
  f.price_at_fire,
  f.prob_at_fire,
  f.edge_at_fire,
  f.fill_price,
  f.shares,
  f.gross_pnl,
  f.net_pnl,
  f.slippage_c,
  f.forecast_version,
  f.calibration_version,
  f.cost_version,

  -- ---- the three moments ----------------------------------------------
  f.fired_at                                       as decided_at,
  coalesce(f.settled_at, b.captured_at)            as settled_at,
  f.captured_at                                    as banked_at,
  case when f.fired_at is not null and coalesce(f.settled_at, b.captured_at) is not null
       then round(extract(epoch from (coalesce(f.settled_at, b.captured_at) - f.fired_at))
                  / 3600.0, 1) end                 as hours_to_settlement,

  -- ---- the band's result, and the desk's call --------------------------
  coalesce(f.settled_yes, b.settled_yes)           as settled_yes,
  coalesce(
    f.signal_correct,
    case
      when coalesce(f.settled_yes, b.settled_yes) is null then null
      when f.action is distinct from 'ENTER' then null   -- an exit is a different question
      when f.side = 'YES' then      coalesce(f.settled_yes, b.settled_yes)
      when f.side = 'NO'  then not  coalesce(f.settled_yes, b.settled_yes)
    end)                                           as signal_correct,

  coalesce(
    f.outcome_source,
    case when f.settled_yes is not null then 'frozen with the signal'
         when b.settled_yes is not null then 'joined from the band outcome'
         else 'not settled yet' end)               as outcome_source,
  b.observed_max_c,
  b.band_lo,
  b.band_hi
from fact_signal_outcome f
left join fact_band_outcome b on b.band_id = f.band_id;

comment on view v_signal_outcome is
  'Every banked signal with the band''s objective outcome and whether the call was right, for rows that froze it and for the 1,963 written before the column existed - which can never be updated, because the row is evidence.';


-- --------------------------------------------------------------------------
-- WHICH STRATEGY IS ACTUALLY RIGHT, and separately, which is profitable.
--
-- They are not the same and the gap between them is where the money goes: a
-- strategy can be right more often than not and lose, if what it is right
-- about is already in the price.
-- --------------------------------------------------------------------------
create or replace view v_signal_scorecard as
select
  o.strategy_id,
  o.side,
  count(*) filter (where o.signal_correct is not null)             as calls_scored,
  count(*) filter (where o.signal_correct)                         as calls_correct,
  round(100.0 * count(*) filter (where o.signal_correct)
        / nullif(count(*) filter (where o.signal_correct is not null), 0), 1) as hit_rate_pct,
  round(avg(o.edge_at_fire) filter (where o.signal_correct is not null)::numeric, 2)
                                                                   as mean_edge_at_fire_pp,
  count(*) filter (where o.filled)                                 as filled,
  round(sum(o.net_pnl) filter (where o.filled)::numeric, 2)        as realised_net_pnl,
  min(o.decided_at)                                                as first_call,
  max(o.decided_at)                                                as last_call
from v_signal_outcome o
group by o.strategy_id, o.side;

comment on view v_signal_scorecard is
  'Per strategy and side: how often the call was right, and separately what it made. Being right and making money are different measurements and a strategy can do one without the other.';


-- Versions get their own view rather than another grouping column: the day
-- versions start arriving, grouping the main scorecard by them would split
-- every strategy's history into a null half and a non-null half and hide the
-- overall hit rate behind the change.
create or replace view v_signal_scorecard_by_version as
select
  o.strategy_id,
  o.forecast_version,
  o.calibration_version,
  count(*) filter (where o.signal_correct is not null)             as calls_scored,
  count(*) filter (where o.signal_correct)                         as calls_correct,
  round(100.0 * count(*) filter (where o.signal_correct)
        / nullif(count(*) filter (where o.signal_correct is not null), 0), 1) as hit_rate_pct,
  round(sum(o.net_pnl) filter (where o.filled)::numeric, 2)        as realised_net_pnl
from v_signal_outcome o
where o.forecast_version is not null or o.calibration_version is not null
group by o.strategy_id, o.forecast_version, o.calibration_version;

comment on view v_signal_scorecard_by_version is
  'The same question asked of a model version rather than a strategy: did this forecast or this calibration make correct calls. Empty until signals start carrying versions, which is what the decision snapshot is for.';

grant select on v_signal_outcome             to anon, authenticated, service_role;
grant select on v_signal_scorecard           to anon, authenticated, service_role;
grant select on v_signal_scorecard_by_version to anon, authenticated, service_role;

commit;
