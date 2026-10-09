-- ===========================================================================
-- THE WATCHDOG KNOWS WHEN EACH JOB LAST WORKED (plan v2 P6.3; WXPredict build
-- 2.D, R14; 9 Oct).
--
-- run_health_watchdog() caught a dispatched run that never logged
-- (v_run_arrivals) and counted 'error' rows. It could not see:
--
--   a job that runs and never succeeds   P2.9_honest_record and
--                                        P2.10_ensemble_record logged
--                                        'partial' on all 7 nights 3-9 Oct
--   a job whose own scheduler stopped    n8n's P0.2/P0.3/P1.5/P0.5 and the
--                                        pg_cron jobs are not dispatched by
--                                        the clock, so v_run_arrivals has
--                                        nothing to miss
--
-- and it failed on every anomaly row, all 1,054 of which in the 7 days to
-- 9 Oct were edge_engine's 'implausible_edge' (the model and the market
-- disagree on a band): every one of its 28 runs that week was 'attention'.
--
--   public.job_sla        every scheduled job, its cadence, where it is
--                         scheduled, and the longest a job may go without an
--                         'ok': one cadence plus max(30 min, cadence / 12),
--                         the rule of the plan's own examples (the tick 90
--                         min; the databank and the archive 26 h)
--   public.v_job_last_ok  each of those jobs: its last 'ok', its last run and
--                         status, and whether it is overdue
--   run_health_watchdog() fails on an overdue job; an implausible-edge
--                         anomaly is a note, any other kind still fails
--
-- REPLAYED BEFORE IT WAS WRITTEN (9 Oct, the 28 watchdog runs of the 7 days to
-- 9 Oct 19:52Z, ingest_log): every job it would have flagged was a real fault -
-- discovery missing Zhengzhou's slugs to 8 Oct 15:21Z, the nightly records
-- never 'ok', P2.9_station_mos last 'ok' 5 Oct, archive_decisions and
-- archive_probabilities 7-8 Oct (the 504 #340 fixed), P5.8_strategy_learn 6-8
-- Oct, the weekly model refit before 5 Oct, one intraday run missed 5 Oct.
-- At 19:52Z it flags 3: the two nightly records (#354 tonight) and
-- P2.9_station_mos.
--
-- Email stays off (Hassan's standing rule): the summary is on the Workflows
-- page, which shows each job's last summary. Idempotent; writes nothing but
-- the watchdog's own row when it runs.
-- ===========================================================================

create table if not exists public.job_sla (
  job              text primary key,
  cadence_minutes  int  not null check (cadence_minutes between 30 and 10080),
  -- the rule, held by the table rather than by each row: one cadence plus
  -- max(30 min, cadence / 12)
  max_age_minutes  int  generated always as (cadence_minutes + greatest(30, cadence_minutes / 12)) stored,
  cadence          text not null,
  scheduled_by     text not null
);

comment on table public.job_sla is
  'Plan v2 P6.3 (9 Oct): every scheduled job and the longest it may go without an ok row in ingest_log: its cadence plus max(30 min, cadence / 12). v_job_last_ok and run_health_watchdog() read it; tests/test_job_sla.py checks every job is one a scheduled script writes.';

alter table public.job_sla enable row level security;
revoke all on public.job_sla from public, anon, authenticated;
grant select on public.job_sla to service_role;

insert into public.job_sla (job, cadence_minutes, cadence, scheduled_by) values
  ('tick', 60, 'hourly at :36', 'tick.yml, dispatched by the clock'),
  ('P0.4_trade_history', 60, 'hourly at :36', 'tick.yml, dispatched by the clock'),
  ('P4.7_confirm_recent', 60, 'hourly at :36', 'tick.yml, dispatched by the clock'),
  ('P1.2_nws_monitor', 120, 'even UTC hours', 'tick.yml (ingest_nws_monitor.py HOURS_UTC)'),
  ('P1.3_nws_forecast', 360, '03/09/15/21 UTC', 'tick.yml (ingest_nws.py HOURS_UTC)'),
  ('P1.4_nws_gridpoint', 360, '03/09/15/21 UTC', 'tick.yml (ingest_nws.py HOURS_UTC)'),
  ('P6.1_clock', 60, 'hourly at :36 / :39', 'pg_cron ad4_clock, ad4_clock_check'),
  ('P6.1_clock_check', 60, 'hourly at :36 / :39', 'pg_cron ad4_clock, ad4_clock_check'),
  ('refresh_page_cache', 30, 'at :12 and :42', 'pg_cron ad4_refresh_page_cache'),
  ('P2.2_paper_maintenance', 360, '03:31/09:31/15:31/21:31 UTC', 'pg_cron ad4_p2_2_paper_maintenance'),
  ('P0.3_book_volume_snapshot', 120, 'every 2 hours', 'n8n P0.3'),
  ('P0.2_market_discovery', 360, 'every 6 hours', 'n8n P0.2'),
  ('P1.5_open_meteo', 180, 'every 3 hours', 'n8n P1.5'),
  ('P0.5_refresh_rules_text', 1440, 'daily', 'n8n P0.5'),
  ('weather_model_forecast', 360, '02/08/14/20 UTC', 'pipeline_intraday.yml, dispatched by the clock'),
  ('probability_engine', 360, '02/08/14/20 UTC', 'pipeline_intraday.yml, dispatched by the clock'),
  ('edge_engine', 360, '02/08/14/20 UTC', 'pipeline_intraday.yml, dispatched by the clock'),
  ('research_capture', 360, '02/08/14/20 UTC', 'pipeline_intraday.yml, dispatched by the clock'),
  ('paper_exits', 360, '02/08/14/20 UTC', 'pipeline_intraday.yml, dispatched by the clock'),
  ('signal_engine', 360, '02/08/14/20 UTC', 'pipeline_intraday.yml, dispatched by the clock'),
  ('paper_plans', 360, '02/08/14/20 UTC', 'pipeline_intraday.yml, dispatched by the clock'),
  ('paper_worker', 360, '02/08/14/20 UTC', 'pipeline_intraday.yml, dispatched by the clock'),
  ('paper_settlement', 360, '02/08/14/20 UTC', 'pipeline_intraday.yml, dispatched by the clock'),
  ('venue_confirm_queue', 360, '02/08/14/20 UTC', 'pipeline_intraday.yml, dispatched by the clock'),
  ('databank_bands', 360, '02/08/14/20 UTC', 'pipeline_intraday.yml, dispatched by the clock'),
  ('capacity', 1440, 'daily at 04 UTC', 'pipeline_daily.yml, dispatched by the clock'),
  ('databank', 1440, 'daily at 04 UTC', 'pipeline_daily.yml, dispatched by the clock'),
  ('engine_replay', 1440, 'daily at 04 UTC', 'pipeline_daily.yml, dispatched by the clock'),
  ('forecast_postprocess', 1440, 'daily at 04 UTC', 'pipeline_daily.yml, dispatched by the clock'),
  ('ingest_forecasts', 1440, 'daily at 04 UTC', 'pipeline_daily.yml, dispatched by the clock'),
  ('measure_skill', 1440, 'daily at 04 UTC', 'pipeline_daily.yml, dispatched by the clock'),
  ('meta_allocator', 1440, 'daily at 04 UTC', 'pipeline_daily.yml, dispatched by the clock'),
  ('P2.9_station_mos', 1440, 'daily at 04 UTC', 'pipeline_daily.yml, dispatched by the clock'),
  ('P3.9_station_correction', 1440, 'daily at 04 UTC', 'pipeline_daily.yml, dispatched by the clock'),
  ('P3.9_width_score', 1440, 'daily at 04 UTC', 'pipeline_daily.yml, dispatched by the clock'),
  ('P5.8_strategy_learn', 1440, 'daily at 04 UTC', 'pipeline_daily.yml, dispatched by the clock'),
  ('strategy_lifecycle', 1440, 'daily at 04 UTC', 'pipeline_daily.yml, dispatched by the clock'),
  ('weather_outcomes', 1440, 'daily at 04 UTC', 'pipeline_daily.yml, dispatched by the clock'),
  ('archive_observations', 1440, 'daily at 02 UTC', 'archive_observations.yml, dispatched by the clock'),
  ('mirror_to_repo', 1440, 'daily at 02 UTC', 'archive_observations.yml, dispatched by the clock'),
  ('restore_verdicts', 1440, 'daily at 02 UTC', 'archive_observations.yml, dispatched by the clock'),
  ('P2.9_honest_record', 1440, 'daily at 02 UTC', 'archive_observations.yml, dispatched by the clock'),
  ('P2.10_ensemble_record', 1440, 'daily at 02 UTC', 'archive_observations.yml, dispatched by the clock'),
  ('archive_book_evidence', 1440, 'daily at 02 UTC', 'archive_observations.yml, dispatched by the clock'),
  ('archive_books', 1440, 'daily at 02 UTC', 'archive_observations.yml, dispatched by the clock'),
  ('archive_correlation', 1440, 'daily at 02 UTC', 'archive_observations.yml, dispatched by the clock'),
  ('archive_decisions', 1440, 'daily at 02 UTC', 'archive_observations.yml, dispatched by the clock'),
  ('archive_edges', 1440, 'daily at 02 UTC', 'archive_observations.yml, dispatched by the clock'),
  ('archive_forecast_features', 1440, 'daily at 02 UTC', 'archive_observations.yml, dispatched by the clock'),
  ('archive_forecast_models', 1440, 'daily at 02 UTC', 'archive_observations.yml, dispatched by the clock'),
  ('archive_forecast_variables', 1440, 'daily at 02 UTC', 'archive_observations.yml, dispatched by the clock'),
  ('archive_forecasts', 1440, 'daily at 02 UTC', 'archive_observations.yml, dispatched by the clock'),
  ('archive_ladders', 1440, 'daily at 02 UTC', 'archive_observations.yml, dispatched by the clock'),
  ('archive_model_payloads', 1440, 'daily at 02 UTC', 'archive_observations.yml, dispatched by the clock'),
  ('archive_probabilities', 1440, 'daily at 02 UTC', 'archive_observations.yml, dispatched by the clock'),
  ('archive_pull_releases', 1440, 'daily at 02 UTC', 'archive_observations.yml, dispatched by the clock'),
  ('archive_research', 1440, 'daily at 02 UTC', 'archive_observations.yml, dispatched by the clock'),
  ('archive_resolution', 1440, 'daily at 02 UTC', 'archive_observations.yml, dispatched by the clock'),
  ('archive_signal_inputs', 1440, 'daily at 02 UTC', 'archive_observations.yml, dispatched by the clock'),
  ('archive_trades', 1440, 'daily at 02 UTC', 'archive_observations.yml, dispatched by the clock'),
  ('ingest_observations', 1440, 'daily at 05 UTC', 'observations.yml, dispatched by the clock'),
  ('export_paper_trades', 1440, 'daily at 05 UTC', 'paper_trade_log.yml, dispatched by the clock'),
  ('weather_model', 10080, 'Mondays at 08 UTC', 'weather_model.yml, dispatched by the clock'),
  ('backfill_wind_direction', 10080, 'Mondays at 08 UTC', 'weather_model.yml, dispatched by the clock')
on conflict (job) do update set cadence_minutes = excluded.cadence_minutes, cadence = excluded.cadence,
                                scheduled_by = excluded.scheduled_by;


create or replace function public.run_health_watchdog()
returns jsonb language plpgsql security definer set search_path = public, pg_temp as $$
declare
  v_gate jsonb := should_run('P4.1_health_watchdog', 'schedule');
  v_failures text[] := '{}';
  v_checks jsonb := '{}';
  v_book timestamptz; v_fc timestamptz; v_age numeric;
  v_err int; v_jobs text; v_anom int;
  v_missed int; v_missed_jobs text; v_not_ok jsonb;
  v_overdue int; v_overdue_jobs text; v_overdue_detail jsonb; v_info_anom int; v_notes jsonb := '[]';
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

  -- THE AGE OF EACH JOB'S LAST OK (plan v2 P6.3, WXPredict build 2.D, R14).
  -- A run that never logged is caught above, but only for what the clock
  -- dispatches; a job that runs every time and never succeeds (the nightly
  -- records, partial on every night 6-9 Oct), or one whose own scheduler
  -- stopped (n8n, pg_cron), was not caught at all. job_sla holds, for every
  -- scheduled job, its cadence plus a margin; a job whose last 'ok' is older
  -- than that is overdue.
  if to_regclass('public.v_job_last_ok') is not null then
    select count(*),
           string_agg(format('%s (last ok %s, allowed %sh)', job,
                             coalesce(round(age_minutes / 60.0, 1)::text || 'h ago', 'none on record'),
                             round(max_age_minutes / 60.0, 1)), ', ' order by job),
           coalesce(jsonb_object_agg(job, jsonb_build_object('last_ok_at', last_ok_at, 'age_h', round(age_minutes / 60.0, 1),
                                                             'sla_h', round(max_age_minutes / 60.0, 1),
                                                             'last_status', last_status)), '{}'::jsonb)
      into v_overdue, v_overdue_jobs, v_overdue_detail
      from public.v_job_last_ok where overdue;
    v_checks := v_checks || jsonb_build_object('overdue_jobs', v_overdue_detail);
    if v_overdue > 0 then
      v_failures := v_failures || format('%s job(s) past their SLA: %s', v_overdue, v_overdue_jobs);
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

  -- WARNINGS APART FROM INFORMATION (plan v2 P6.3). Every anomaly row of the
  -- 7 days to 9 Oct was edge_engine's 'implausible_edge' (1,054 rows): the
  -- model and the market disagree on a band. That is the research's to read,
  -- not a job that failed, and it kept every watchdog run 'attention'. It is
  -- a note now; any other kind of anomaly still fails the check.
  select count(*), count(*) filter (where kind = 'implausible_edge')
    into v_anom, v_info_anom
    from anomalies where detected_at > now() - interval '24 hours';
  v_checks := v_checks || jsonb_build_object('anomalies_24h', v_anom, 'implausible_edges_24h', v_info_anom);
  if v_anom - v_info_anom > 0 then
    v_failures := v_failures || format('%s anomaly row(s) in 24h', v_anom - v_info_anom);
  end if;
  if v_info_anom > 0 then
    v_notes := v_notes || to_jsonb(format('%s implausible-edge row(s) in 24h (information: the model and the market disagree; not a failed job)', v_info_anom));
  end if;

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
                                                                           round(v_vol), v_cities, v_trades)) || v_notes,
                                 'emailed', false, 'email_enabled', false, 'trigger', 'pg_cron');
  perform log_ingest('P4.1_health_watchdog', v_status, cardinality(v_failures), v_detail);
  return v_detail || jsonb_build_object('status', v_status);
end $$;

revoke all on function public.run_health_watchdog() from public, anon, authenticated;
grant execute on function public.run_health_watchdog() to service_role;

-- The view comes last: run_health_watchdog() looks it up only when it runs
-- (to_regclass), and tools/gen_provenance.py reads a view's body as far as the
-- next view or the end of the file.
create or replace view public.v_job_last_ok as
select s.job, s.cadence, s.scheduled_by, s.max_age_minutes,
       ok.last_ok_at,
       round(extract(epoch from now() - ok.last_ok_at) / 60)::int as age_minutes,
       last.logged_at as last_run_at, last.status as last_status,
       (ok.last_ok_at is null or now() - ok.last_ok_at > make_interval(mins => s.max_age_minutes)) as overdue
  from public.job_sla s
  left join lateral (select max(l.logged_at) as last_ok_at from public.ingest_log l
                      where l.job = s.job and l.status = 'ok') ok on true
  left join lateral (select l.logged_at, l.status from public.ingest_log l
                      where l.job = s.job order by l.logged_at desc limit 1) last on true;

comment on view public.v_job_last_ok is
  'Plan v2 P6.3 (9 Oct): each job in job_sla, when it last logged ok, its last run and status, and whether its last ok is older than its SLA. run_health_watchdog() fails on an overdue job.';

revoke all on public.v_job_last_ok from public, anon, authenticated;
grant select on public.v_job_last_ok to service_role;
