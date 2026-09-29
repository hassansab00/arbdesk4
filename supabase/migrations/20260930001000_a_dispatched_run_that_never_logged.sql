-- ===========================================================================
-- A DISPATCHED RUN THAT NEVER LOGGED IS A FAILURE (audit repair 2, 29 Sep).
--
-- run_health_watchdog() counted one thing as a failed job: an ingest_log row
-- with status 'error'. A job that dies before its own log_run writes nothing,
-- so it could not be counted. Measured 27 Sep 21:44Z - 29 Sep 21:44Z (Actions
-- run list; ingest_log): 19 production runs failed, ingest_log held 0 'error'
-- rows, and all 8 watchdog runs reported failed_jobs_24h 0.
--
--   pipeline_intraday  12 of 12 runs died in paper_exits (a Decimal met a
--                      float); paper_exits logged nothing after 25 Sep 12:38Z
--   tick               4 runs lost the trade step to a 401 before it logged;
--                      1 run failed in 3 s with no steps
--   archive            the honest record hit its step timeout, no row
--   weather_model      the weekly refit crashed, no row since 21 Sep
--
-- The clock already records what it dispatched: every P6.1_clock row lists the
-- workflow files it started that hour (public.clock_tick). What was missing is
-- what each of those runs must leave behind. So:
--
--   public.clock_expected_jobs  per workflow file, the ingest_log jobs a run
--                               writes every time, and how long after the
--                               dispatch the row may take (the workflow's
--                               timeout-minutes + 15 min for the queue; the
--                               slowest arrival measured 21-29 Sep is far
--                               inside each window)
--   public.v_run_arrivals       each dispatch of the last 7 days x each job it
--                               owes: arrived (with its status), waiting (still
--                               inside its window) or missing
--   run_health_watchdog()       counts the missing ones as failures, and
--                               reports partial / attention runs per job (a
--                               check, not a failure: several jobs report
--                               'partial' on a normal night)
--
-- A job logged by two workflows in the same hour (paper_settlement: the tick
-- and the intraday pipeline) can hide the other's miss; the window is short
-- enough that only the same hour overlaps. A step switched off on purpose
-- must have its rows removed here, or every run reads missing:
-- vars.PAPER_TRADES_ENABLED = 'false' skips six intraday steps, the jobs
-- research_capture, paper_exits, signal_engine, paper_plans, paper_worker and
-- paper_settlement (pipeline_intraday.yml).
--
-- Idempotent. Nothing here writes to ingest_log except the watchdog itself.
-- ===========================================================================

create table if not exists public.clock_expected_jobs (
  file            text not null references public.clock_schedule (file),
  job             text not null,
  within_minutes  int  not null check (within_minutes between 5 and 180),
  note            text,
  primary key (file, job)
);

comment on table public.clock_expected_jobs is
  'Audit repair 2 (29 Sep): the ingest_log job(s) each workflow the clock dispatches writes on every run, and how many minutes after the dispatch the row may arrive. v_run_arrivals and run_health_watchdog() read it; tests/test_run_arrivals.py checks every job is written by a script its workflow runs.';

alter table public.clock_expected_jobs enable row level security;
revoke all on public.clock_expected_jobs from public, anon, authenticated;
grant select on public.clock_expected_jobs to service_role;

insert into public.clock_expected_jobs (file, job, within_minutes, note) values
  ('tick.yml',                 'tick',                    15,  'tick.py'),
  ('tick.yml',                 'P0.4_trade_history',      15,  'ingest_trades.py, beside the tick'),
  ('pipeline_intraday.yml',    'weather_model_forecast',  45,  'weather_model.py --predict-only'),
  ('pipeline_intraday.yml',    'probability_engine',      45,  null),
  ('pipeline_intraday.yml',    'edge_engine',             45,  null),
  ('pipeline_intraday.yml',    'research_capture',        45,  'a paper step: skipped when vars.PAPER_TRADES_ENABLED is false'),
  ('pipeline_intraday.yml',    'paper_exits',             45,  'a paper step: skipped when vars.PAPER_TRADES_ENABLED is false'),
  ('pipeline_intraday.yml',    'signal_engine',           45,  'a paper step: skipped when vars.PAPER_TRADES_ENABLED is false'),
  ('pipeline_intraday.yml',    'paper_plans',             45,  'a paper step: skipped when vars.PAPER_TRADES_ENABLED is false'),
  ('pipeline_intraday.yml',    'paper_worker',            45,  'a paper step: skipped when vars.PAPER_TRADES_ENABLED is false'),
  ('pipeline_intraday.yml',    'paper_settlement',        45,  'a paper step: skipped when vars.PAPER_TRADES_ENABLED is false; the tick logs it too, in the same hour'),
  ('pipeline_daily.yml',       'forecast_postprocess',   105,  null),
  ('pipeline_daily.yml',       'weather_outcomes',       105,  null),
  ('pipeline_daily.yml',       'measure_skill',          105,  null),
  ('pipeline_daily.yml',       'model_promotion',        105,  null),
  ('pipeline_daily.yml',       'databank',               105,  null),
  ('pipeline_daily.yml',       'calibration',            105,  null),
  ('pipeline_daily.yml',       'P5.8_strategy_learn',    105,  null),
  ('pipeline_daily.yml',       'trajectory',             105,  null),
  ('pipeline_daily.yml',       'capacity',               105,  null),
  ('pipeline_daily.yml',       'P3.9_station_correction',105,  null),
  ('pipeline_daily.yml',       'P2.9_station_mos',       105,  null),
  ('pipeline_daily.yml',       'P3.9_width_score',       105,  null),
  ('pipeline_daily.yml',       'engine_replay',          105,  null),
  ('pipeline_daily.yml',       'hit_tournament',         105,  null),
  ('pipeline_daily.yml',       'strategy_lifecycle',     105,  null),
  ('pipeline_daily.yml',       'meta_allocator',         105,  null),
  ('archive_observations.yml', 'archive_observations',    60,  'archive_observations.py logs archive_<dataset>'),
  ('archive_observations.yml', 'mirror_to_repo',          60,  null),
  ('archive_observations.yml', 'restore_verdicts',        60,  null),
  ('archive_observations.yml', 'P2.9_honest_record',      60,  null),
  ('archive_observations.yml', 'P2.10_ensemble_record',   60,  null),
  ('forecasts.yml',            'ingest_forecasts',        40,  'forecast_backfill_job.py runs ingest_forecasts.py'),
  ('observations.yml',         'ingest_observations',     75,  null),
  ('paper_trade_log.yml',      'export_paper_trades',     30,  null),
  ('weather_model.yml',        'backfill_wind_direction', 45,  null),
  ('weather_model.yml',        'weather_model',           45,  'the weekly refit')
on conflict (file, job) do update set within_minutes = excluded.within_minutes, note = excluded.note;

create or replace view public.v_run_arrivals as
with dispatched as (
  select l.logged_at as dispatched_at, x ->> 'file' as file
    from public.ingest_log l
   cross join lateral jsonb_array_elements(coalesce(l.detail -> 'dispatched', '[]'::jsonb)) x
   where l.job = 'P6.1_clock'
     and l.logged_at > now() - interval '7 days'
     and not coalesce((l.detail ->> 'dry_run')::boolean, false)
)
select d.dispatched_at,
       d.file,
       e.job,
       d.dispatched_at + make_interval(mins => e.within_minutes)       as due_by,
       a.logged_at                                                     as arrived_at,
       a.status,
       case when a.logged_at is not null then 'arrived'
            when now() < d.dispatched_at + make_interval(mins => e.within_minutes) then 'waiting'
            else 'missing' end                                         as state
  from dispatched d
  join public.clock_expected_jobs e on e.file = d.file
  left join lateral (
    select i.logged_at, i.status
      from public.ingest_log i
     where i.job = e.job
       and i.logged_at >= d.dispatched_at
       and i.logged_at < d.dispatched_at + make_interval(mins => e.within_minutes)
     order by i.logged_at
     limit 1
  ) a on true;

comment on view public.v_run_arrivals is
  'Audit repair 2 (29 Sep): every workflow the clock dispatched in the last 7 days (P6.1_clock rows, dry runs left out) x every job clock_expected_jobs says it writes: arrived (with the first row''s status), waiting (inside its window) or missing. A run that crashed, timed out or never started reads missing. run_health_watchdog() counts the missing ones.';

revoke all on public.v_run_arrivals from public, anon, authenticated;
grant select on public.v_run_arrivals to service_role;

-- The watchdog: the same checks as sql/ad4_91_database_jobs.sql had, plus the
-- missed runs (a failure) and the partial / attention runs per job (a check).
create or replace function public.run_health_watchdog()
returns jsonb language plpgsql security definer set search_path = public, pg_temp as $$
declare
  v_gate jsonb := should_run('P4.1_health_watchdog', 'schedule');
  v_failures text[] := '{}';
  v_checks jsonb := '{}';
  v_book timestamptz; v_fc timestamptz; v_age numeric;
  v_err int; v_jobs text; v_anom int;
  v_missed int; v_missed_jobs text; v_not_ok jsonb;
  v_cities int; v_vol numeric; v_trades bigint;
  v_summary text; v_status text; v_detail jsonb;
begin
  if not coalesce((v_gate ->> 'run')::boolean, true) then
    return v_gate;
  end if;

  select max(observed_at) into v_book from book_snapshots;
  if v_book is null then
    v_failures := v_failures || 'stale_book: book_snapshots returned no rows at all'::text;
  else
    v_age := extract(epoch from now() - v_book) / 60;
    v_checks := v_checks || jsonb_build_object('book_age_min', round(v_age));
    if v_age > 150 then v_failures := v_failures || format('stale_book: %smin old', round(v_age)); end if;
  end if;

  select max(run_at) into v_fc from weather_forecasts;
  if v_fc is null then
    v_failures := v_failures || 'stale_forecast: weather_forecasts returned no rows at all'::text;
  else
    v_age := extract(epoch from now() - v_fc) / 3600;
    v_checks := v_checks || jsonb_build_object('forecast_age_h', round(v_age, 1));
    if v_age > 12 then v_failures := v_failures || format('stale_forecast: %sh old', round(v_age, 1)); end if;
  end if;

  select count(*), string_agg(distinct job, ', ') into v_err, v_jobs from ingest_log
   where status = 'error' and job <> 'P4.1_health_watchdog' and logged_at > now() - interval '24 hours';
  v_checks := v_checks || jsonb_build_object('failed_jobs_24h', v_err);
  if v_err > 0 then v_failures := v_failures || format('%s failed ingest job(s) in 24h: %s', v_err, v_jobs); end if;

  -- A RUN THAT NEVER LOGGED (audit repair 2): a crash before log_run, a step
  -- timeout, a runner that never started. Counted from what the clock
  -- dispatched, not from what the jobs chose to say about themselves.
  if to_regclass('public.v_run_arrivals') is not null then
    select coalesce(sum(n), 0), string_agg(format('%s x%s', job, n), ', ' order by n desc, job)
      into v_missed, v_missed_jobs
      from (select job, count(*) as n from public.v_run_arrivals
             where state = 'missing' and due_by > now() - interval '24 hours'
             group by job) m;
    v_checks := v_checks || jsonb_build_object('missed_runs_24h', v_missed);
    if v_missed > 0 then
      v_failures := v_failures || format('%s dispatched run(s) never logged their job: %s', v_missed, v_missed_jobs);
    end if;
  end if;

  -- Runs that logged 'partial' or 'attention': reported per job, not failed.
  select jsonb_object_agg(job, counts order by job) into v_not_ok
    from (select job, jsonb_build_object('partial', count(*) filter (where status = 'partial'),
                                         'attention', count(*) filter (where status = 'attention')) as counts
            from ingest_log
           where status in ('partial', 'attention') and job <> 'P4.1_health_watchdog'
             and logged_at > now() - interval '24 hours'
           group by job) p;
  v_checks := v_checks || jsonb_build_object('not_ok_24h', coalesce(v_not_ok, '{}'::jsonb));

  select count(*) into v_anom from anomalies where detected_at > now() - interval '24 hours';
  v_checks := v_checks || jsonb_build_object('anomalies_24h', v_anom);
  if v_anom > 0 then v_failures := v_failures || format('%s anomaly row(s) in 24h', v_anom); end if;

  select count(*), coalesce(sum(volume_usd), 0), coalesce(sum(n_trades), 0)
    into v_cities, v_vol, v_trades from v_city_volume;
  v_checks := v_checks || jsonb_build_object('volume_usd_24h', round(v_vol), 'trades_24h', v_trades,
                                             'cities_with_volume', v_cities);
  if v_cities = 0 then
    v_failures := v_failures || 'no_market_volume: v_city_volume returned no rows - trades_observed is empty or the trade ingest has stopped'::text;
  elsif v_vol = 0 then
    v_failures := v_failures || 'no_market_volume: $0 traded across all cities in the last 24h'::text;
  end if;

  -- A failed check is not a failed run: 'attention', never 'error' (the n8n rule).
  v_status := case when cardinality(v_failures) > 0 then 'attention' else 'ok' end;
  v_summary := case when cardinality(v_failures) > 0
    then format('AD4 P4.1: %s check(s) failing: %s. NOT emailed - email is off.',
                cardinality(v_failures), array_to_string(v_failures, '; '))
    else 'AD4 P4.1: all clear.' end;
  v_detail := jsonb_build_object('summary', v_summary, 'failures', to_jsonb(v_failures), 'checks', v_checks,
                                 'context_notes', jsonb_build_array(format('market volume 24h: $%s across %s cities, %s trades',
                                                                           round(v_vol), v_cities, v_trades)),
                                 'emailed', false, 'email_enabled', false, 'trigger', 'pg_cron');
  perform log_ingest('P4.1_health_watchdog', v_status, cardinality(v_failures), v_detail);
  return v_detail || jsonb_build_object('status', v_status);
end $$;

revoke all on function public.run_health_watchdog() from public, anon, authenticated;
grant execute on function public.run_health_watchdog() to service_role;
