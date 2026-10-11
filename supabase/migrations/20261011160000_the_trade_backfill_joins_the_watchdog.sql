-- ===========================================================================
-- THE TRADE BACKFILL JOINS THE WATCHDOG (WXPredict build R46; 11 Oct).
--
-- scripts/ingest_trades.py --backfill (R46, #365) reads the trades of each
-- market from before it was discovered, as a step of pipeline_intraday.yml,
-- and logs P0.4_trade_backfill. It was left out of the watchdog until it had
-- run a day, so that expecting it would not report its earlier dispatches
-- missing (20261003170000's rule). Its four runs (ingest_log), all 'ok', no
-- errors, nothing pending:
--
--   10 Oct 20:39Z   94 markets   14,182 trades   58.1 s   (the first run's 60 h)
--   11 Oct 02:38Z    0 markets        0           1.5 s
--   11 Oct 08:39Z   47 markets    8,320          37.4 s
--   11 Oct 14:39Z   47 markets        0          36.6 s   (the same 47, deduplicated)
--
-- and the four intraday jobs that carried them took 186-234 s, 4 billed
-- minutes each (Actions jobs API, each job rounded up).
--
-- 1. clock_expected_jobs: pipeline_intraday.yml owes a P0.4_trade_backfill
--    row within 45 minutes, as its other jobs. Every intraday dispatch of the
--    24 h the watchdog counts (from 20:36Z 10 Oct) logged one.
--
-- 2. job_sla: every 6 hours, like the other intraday jobs.
--
-- 3. job_sla.switch_key: the backfill obeys P0.4_trade_history's switch on
--    the Workflows page, not one of its own (ingest_trades.switched_on): with
--    P0.4 off it writes nothing and logs 'skipped'. v_job_last_ok read each
--    job's mode under its own name, so a paused P0.4 would have read the
--    backfill overdue. A row may now name the switch it follows; a row
--    without one reads its own, as before. v_job_last_ok's other 64 rows were
--    proved unchanged before this was written (one REPEATABLE READ snapshot,
--    EXCEPT ALL both ways, 11 Oct).
--
-- Idempotent. Service role only, as before.
-- ===========================================================================

alter table public.job_sla add column if not exists switch_key text;

comment on column public.job_sla.switch_key is
  'R46 (11 Oct): the Workflows-page switch (settings.workflow_schedules key) this job obeys, when not its own name: P0.4_trade_backfill follows P0.4_trade_history. Null reads the job''s own.';

insert into public.clock_expected_jobs (file, job, within_minutes, note) values
  ('pipeline_intraday.yml', 'P0.4_trade_backfill', 45, 'ingest_trades.py --backfill; follows P0.4_trade_history''s switch')
on conflict (file, job) do nothing;

insert into public.job_sla (job, cadence_minutes, cadence, scheduled_by, switch_key) values
  ('P0.4_trade_backfill', 360, '02/08/14/20 UTC', 'pipeline_intraday.yml, dispatched by the clock', 'P0.4_trade_history')
on conflict (job) do update set cadence_minutes = excluded.cadence_minutes, cadence = excluded.cadence,
                                scheduled_by = excluded.scheduled_by, switch_key = excluded.switch_key;

create or replace view public.v_job_last_ok as
-- ingest_log is read twice in all, not twice per job (Codex on #357: 128
-- correlated scans took 109.5 ms on 9 Oct's 6,708 rows; aggregated, 29.0 ms).
-- A job switched 'off' or 'manual' on the Workflows page logs only 'skipped'
-- rows (should_run()), by choice: it is never overdue (Codex on #357). The
-- switch is the job's own unless job_sla names the one it follows (R46).
with ok as (
  select job, max(logged_at) as last_ok_at
    from public.ingest_log where status = 'ok' group by job
), last as (
  select distinct on (job) job, logged_at, status
    from public.ingest_log order by job, logged_at desc
), gate as (
  select value as schedules from public.settings where key = 'workflow_schedules'
)
select s.job, s.cadence, s.scheduled_by, s.max_age_minutes,
       ok.last_ok_at,
       round(extract(epoch from now() - ok.last_ok_at) / 60)::int as age_minutes,
       last.logged_at as last_run_at, last.status as last_status,
       (coalesce(gate.schedules -> coalesce(s.switch_key, s.job) ->> 'mode', 'auto') not in ('off', 'manual')
        and (ok.last_ok_at is null or now() - ok.last_ok_at > make_interval(mins => s.max_age_minutes))) as overdue,
       coalesce(gate.schedules -> coalesce(s.switch_key, s.job) ->> 'mode', 'auto') as mode
  from public.job_sla s
  left join ok on ok.job = s.job
  left join last on last.job = s.job
  left join gate on true;

comment on view public.v_job_last_ok is
  'Plan v2 P6.3 (9 Oct): each job in job_sla, its last ok in ingest_log, its last run and status, and whether it is overdue (no ok for longer than max_age_minutes). A job switched off or manual on the Workflows page (its own switch, or job_sla.switch_key''s from R46) is never overdue. run_health_watchdog() fails on an overdue job.';

revoke all on public.v_job_last_ok from public, anon, authenticated;
grant select on public.v_job_last_ok to service_role;

notify pgrst, 'reload schema';
