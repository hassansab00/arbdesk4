-- FIVE NIGHTLY FITS WHOSE OUTPUT REACHES NO PRICE STOP (WXPredict build, wave A.3).
--
-- docs/WXPREDICT_BUILD.md, section 3.6 ("A.1 result"), measured 5 Oct from the
-- jobs API (7 nights of pipeline_daily) and the live database. Five steps of
-- pipeline_daily refit things no price reads, about 366 s a night on the
-- medians (432 s on 5 Oct):
--
--   model_promotion       the price reads only promoted rows (v_model_promoted):
--                         0 promoted of 392; none can be while
--                         TRAINS_ONLY_ON_ADVANCE_INFORMATION is False
--   calibration           the price applies the map only when `applies` is
--                         true; it has been false since 19 Sep (ingest_log)
--   P5.8_strategy_learn   every loader returns its prior while
--                         settings.strategy_learning is off (it is)
--   trajectory            the price reads only applied cells: 0 of 1,176
--   hit_tournament        shadow only; no price or page reads derived_hit_*
--
-- pipeline_daily.yml switches the five steps off (`if: false`, each with its
-- reason). Their last output stays in the database and the mirror; nothing
-- is deleted from them. This migration does what stopping them needs:
--
-- 1. They are no longer expected to log. 20260930001000: "A step switched off
--    on purpose must have its rows removed here, or every run reads missing."
--    These are configuration rows whose source is the migrations
--    (mirror_to_repo.NOT_MIRRORED); the seed that wrote them stays in git, and
--    restarting a step puts its row back.
-- 2. The freshness spec stops calling their tables stale: no limit, and the
--    description says the rows are the last fit (sql/ad4_39_freshness.sql
--    carries the same values). v_data_freshness writes each limit into its own
--    definition, so it is rebuilt from that file live after this migration.
-- 3. v_calibration_status gains 'stopped' (sql/ad4_78_calibration_status.sql):
--    without it the Predictive page would read "Stale" in red over a step
--    stopped on purpose. It is known from clock_expected_jobs, the record of
--    what a dispatched run owes, and it is never shown while the stored map
--    still claims `applies`. The view stays owner-rights, as before.
-- 4. and 5. STOPPING A FITTER DOES NOT STOP WHAT IT LAST WROTE (Codex on #316).
--    Two of the five write a switch the price reads on its own, with no
--    setting between them and the engine:
--      calibration  settings.calibration_map.applies (probability_engine
--                   ._calibration_map applies the stored map while it is true)
--      trajectory   derived_trajectory.applied (v_trajectory_applied ->
--                   v_city_trajectory_now, read with trajectory_applied=true)
--    Both were off when this was written (5 Oct 20:20Z: applies false, 0 of
--    1,176 cells applied), but a night that runs before this lands could turn
--    one on and nothing would ever turn it off. So whatever is on is switched
--    off here, in the same transaction, and the row says so: the map keeps
--    every field and records `stopped` (by, at, applies_before); each cell's
--    reason is prefixed. Nothing is deleted. A restarted step decides afresh
--    on its next fit (calibration.py rewrites the map; trajectory.py
--    overwrites every cell with upsert_replace). The other three have no
--    such path, read live 5 Oct 20:22Z: v_model_promoted has 0 rows and
--    nothing can be promoted while TRAINS_ONLY_ON_ADVANCE_INFORMATION is
--    False; settings.strategy_learning is {"enabled": false} and no script
--    writes it; nothing that prices reads derived_hit_*.
--
-- Re-runnable. tests/database/stopped-fits.cjs holds it.

-- 1. -------------------------------------------------------------------------
delete from public.clock_expected_jobs
 where file = 'pipeline_daily.yml'
   and job in ('model_promotion', 'calibration', 'P5.8_strategy_learn',
               'trajectory', 'hit_tournament');

-- 2. -------------------------------------------------------------------------
do $$
begin
  if to_regclass('public.data_freshness_spec') is not null then
    update public.data_freshness_spec
       set fresh_hours   = null,
           plain_english = 'Stopped (WXPredict build, wave A.3): no longer refitted nightly, so these are its last rows. '
                           || plain_english
     where table_name in ('derived_hit_recipe', 'derived_hit_tournament', 'derived_hit_summary',
                          'derived_trajectory', 'derived_model_promotion', 'strategy_params')
       and plain_english not like 'Stopped (WXPredict build, wave A.3)%';
  end if;
end $$;

-- 3. -------------------------------------------------------------------------
-- Verbatim from sql/ad4_78_calibration_status.sql.
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

-- 4. -------------------------------------------------------------------------
-- settings is created by sql/ad4_00_preflight.sql; the view above reads it, so
-- it is required here too (the paper contracts' fixture carries it).
update public.settings
   set value = value || jsonb_build_object(
         'applies', false,
         'stopped', jsonb_build_object(
           'by', 'WXPredict build, wave A.3 (migration 20261005190000): the nightly refit was switched off',
           'at', now(),
           'applies_before', value -> 'applies')),
       updated_at = now()
 where key = 'calibration_map'
   and coalesce(value -> 'applies', 'false'::jsonb) not in ('false'::jsonb, 'null'::jsonb);

-- 5. -------------------------------------------------------------------------
-- derived_trajectory is created by sql/ad4_86_trajectory.sql, which the paper
-- contracts never apply.
do $$
begin
  if to_regclass('public.derived_trajectory') is not null then
    update public.derived_trajectory
       set applied = false,
           reason  = 'Stopped (WXPredict build, wave A.3): applied when its nightly fit was switched off, so no longer priced. '
                     || reason
     where applied;
  end if;
end $$;
