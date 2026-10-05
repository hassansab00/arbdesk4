-- ===========================================================================
-- ad4_78_calibration_status.sql - IS CALIBRATION PENDING, RUNNING OR FAILED?
--
-- "can we build a status and idicator in te predicitive section were it
-- states if calibration is pendin or runnin or failed or watever."
--
-- Nothing said. scripts/calibration.py runs every day, fits a map, writes a
-- rich payload to ingest_log and returns - and the only way to know any of it
-- was to read that table by hand. The Predictive page showed calibrated
-- numbers with no statement of whether a calibration was in force, why not,
-- or when that might change.
--
-- WHAT THE LATEST RUN ACTUALLY SAYS, 2026-09-20 09:19 UTC:
--
--     method                 temperature       T = 1.603
--     applies                FALSE
--     gate_unmet             8 settlement dates, needs 30
--     complete ladders       386   (needs 300 - this one is met)
--     validation brier       0.7268 before -> 0.7413 after
--
-- Two separate things, and the page has to say both. The map is WAITING on
-- evidence: 8 of the 30 distinct settlement dates the gate requires, so
-- roughly three more weeks at one a day. And on the evidence there IS, the
-- fit currently makes the validation Brier WORSE - 0.7268 to 0.7413 - so
-- reaching 30 dates is necessary and not sufficient. Reporting only the
-- countdown would promise something the measurement does not support.
--
-- STATE, NOT A COLOUR. Seven of them, because "red" cannot distinguish a job
-- that has never run from one that ran and correctly declined:
--
--   never_run            no calibration run is on record at all
--   failed               the last run raised - log_run wrote 'attention'
--   stale                it ran, but not since yesterday; the daily pipeline
--                        is the thing to look at, not the map
--   pending_evidence     fitted and withheld because a gate is unmet, which
--                        is the normal state of a young desk
--   fitted_not_applied   gates met, fit made, and it STILL does not apply -
--                        the validation gain was not there
--   applied              in force and pricing
--   stopped              switched off on purpose, and no map in force: the
--                        nightly refit no longer runs (WXPredict build, wave
--                        A.3) - pipeline_daily is no longer expected to log
--                        `calibration` (clock_expected_jobs, the record of
--                        what a dispatched run owes) - AND the stored map in
--                        settings.calibration_map does not claim `applies`.
--                        Both, because stopping the fitter does not stop the
--                        last map it wrote: probability_engine reads the
--                        stored map, not this job (Codex on #316). A stored
--                        map still claiming `applies` is reported by the
--                        states above, exactly as before the stop, never as
--                        "stopped". Without this state the page would read
--                        "stale" in red over a step stopped on purpose.
--
-- Reads ingest_log because that is where calibration.py records what it did.
-- No second opinion about whether a map applies: `applies` is the flag the
-- fitting run itself wrote. The one exception is 'stopped', which reads the
-- stored map as well, so a stopped refit is never shown over a map in force;
-- in that state `applies` is false, because 'stopped' requires it.
--
-- RUN ORDER: after ad4_00_preflight.sql (ingest_log, settings) and migration
-- 20260930001000 (clock_expected_jobs). Re-runnable.
-- ===========================================================================

create or replace view public.v_calibration_status as
with latest as (
  select l.status, l.logged_at, l.detail, l.rows_written
    from public.ingest_log l
   where l.job = 'calibration'
   order by l.logged_at desc
   limit 1
),
-- Stopped: not expected to run, and no stored map claiming `applies` (any
-- value but false or null counts as claiming it).
fitter as (
  select not exists (select 1 from public.clock_expected_jobs e
                      where e.file = 'pipeline_daily.yml' and e.job = 'calibration')
     and not exists (select 1 from public.settings s
                      where s.key = 'calibration_map'
                        and coalesce(s.value -> 'applies', 'false'::jsonb)
                            not in ('false'::jsonb, 'null'::jsonb))   as stopped
)
select
  l.logged_at                                            as last_run_at,
  l.status                                               as run_status,
  coalesce((l.detail ->> 'applies')::boolean, false)
    and not f.stopped                                    as applies,
  l.detail ->> 'method'                                  as method,
  (l.detail ->> 'settlement_dates')::integer             as settlement_dates,
  (l.detail ->> 'complete_ladders')::integer             as complete_ladders,
  l.detail -> 'gate_unmet'                               as gate_unmet,
  (l.detail ->> 'validation_brier_before')::numeric      as brier_before,
  (l.detail ->> 'validation_brier_after')::numeric       as brier_after,
  l.detail ->> 'note'                                    as note,
  l.detail ->> 'evidence_scope'                          as evidence_scope,
  -- The Brier the fit would have to beat. Stated separately because "waiting
  -- for 30 dates" and "the fit does not help yet" are different problems and
  -- only one of them is solved by waiting.
  case
    when (l.detail ->> 'validation_brier_after')::numeric
       < (l.detail ->> 'validation_brier_before')::numeric then true
    when l.detail ? 'validation_brier_after'                then false
  end                                                    as validation_improves,
  case
    when f.stopped                                                   then 'stopped'
    when l.status = 'attention'                                      then 'failed'
    when l.logged_at < now() - interval '36 hours'                   then 'stale'
    when coalesce((l.detail ->> 'applies')::boolean, false)          then 'applied'
    when jsonb_array_length(coalesce(l.detail -> 'gate_unmet','[]'::jsonb)) > 0
                                                                     then 'pending_evidence'
    else 'fitted_not_applied'
  end                                                    as state
from latest l
cross join fitter f
union all
-- A desk that has never calibrated must say so rather than return no rows,
-- because an empty result and a healthy one look identical to a page that
-- only renders what it receives.
select null, 'never_run', false, null, null, null, null, null, null,
       'No calibration run is on record.', null, null, 'never_run'
where not exists (select 1 from public.ingest_log where job = 'calibration');

comment on view public.v_calibration_status is
  'One row: what the newest calibration run did, whether its map is in force, and what it is waiting on. Distinguishes a job that never ran from one that ran and correctly declined to apply.';

grant select on public.v_calibration_status to anon, authenticated, service_role;
