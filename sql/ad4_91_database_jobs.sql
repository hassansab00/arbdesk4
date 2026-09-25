-- ad4_91_database_jobs.sql - TWO n8n JOBS THAT ONLY EVER TALKED TO THIS DATABASE.
--
-- Plan v2 P6.2: the n8n instance is shared, and this project may use 1,000 to
-- 1,400 of its 2,500 executions a month (Hassan, 25 Sep). Measured 25 Sep by
-- execution id, the project ran ~115 a day; after P1.6 retired and P0.3 went
-- 2-hourly, ~81. Each of the two jobs below costs 4 executions a day in n8n
-- and does nothing a database function cannot do by itself:
--
--   P2.2 Paper Maintenance  calls expire_paper_commands() and
--                           check_paper_desk_integrity(true), reads the open
--                           orders and positions, logs a summary
--   P4.1 Health Watchdog    reads the newest book snapshot and forecast run,
--                           failed ingest jobs and anomalies in the last 24 h
--                           and v_city_volume, logs a verdict (email is off)
--
-- So pg_cron runs them here, at the minutes n8n ran them in UTC (the n8n
-- instance evaluates cron in UTC+3, so its '31 */6' fired at 03/09/15/21:31 UTC
-- and '58 */6' at 03/09/15/21:58), behind the same should_run() gate so the
-- Workflows page still switches them, logging the same job names with the same
-- status rules. The n8n workflows keep their webhook, so the page's Run button
-- still works; only their Schedule Trigger is disabled in n8n.
--
-- ONE DELIBERATE CHANGE. The watchdog called a book snapshot stale at 60 min.
-- P0.3 has captured books every 2 hours since 25 Sep (P6.2), so 60 min would
-- fail most checks on a healthy desk. The threshold is 150 min: the 120-minute
-- cadence plus P0.3's measured worst run (140 s) and a margin.
--
-- RUN ORDER: after ad4_81 (check_paper_desk_integrity) and ad4_32 (should_run).
-- Re-runnable: functions are replaced, cron jobs are replaced by name.

create or replace function public.run_paper_maintenance()
returns jsonb language plpgsql security definer set search_path = public, pg_temp as $$
declare
  v_gate jsonb := should_run('P2.2_paper_maintenance', 'schedule');
  v_expired int;
  v_books jsonb;
  v_open int; v_queued int; v_positions int; v_shares numeric; v_basis numeric;
  v_books_ok boolean; v_books_text text; v_status text; v_summary text; v_detail jsonb;
begin
  if not coalesce((v_gate ->> 'run')::boolean, true) then
    return v_gate;
  end if;
  v_expired := coalesce(expire_paper_commands(), 0);
  v_books := check_paper_desk_integrity(true);
  select count(*), count(*) filter (where status = 'queued') into v_open, v_queued
    from paper_orders where status in ('queued', 'working');
  select count(*), coalesce(sum(shares), 0), coalesce(sum(cost_basis), 0)
    into v_positions, v_shares, v_basis from paper_positions where shares > 0;

  v_books_ok := case when v_books ->> 'ok' = 'false' then false
                     when v_books ->> 'ok' = 'true' then true end;
  v_books_text := case
    when v_books_ok is false then format('%s of %s desk(s) DO NOT BALANCE - %s.',
           v_books ->> 'breached', v_books ->> 'desks',
           (select string_agg((d ->> 'desk') || ': ' ||
                   coalesce((select string_agg(x, ', ') from jsonb_array_elements_text(d -> 'breaches') x), ''), '; ')
              from jsonb_array_elements(coalesce(v_books -> 'detail', '[]'::jsonb)) d))
    when v_books_ok is true then format('%s desk(s) balance', v_books ->> 'desks')
           || case when coalesce((v_books ->> 'unverifiable_desks')::int, 0) > 0
                   then format(', %s with an identity that cannot be checked', v_books ->> 'unverifiable_desks') else '' end
           || '.'
    else 'the books check returned no verdict' end;
  v_summary := format('AD4 P2.2: %s expired paper command(s) released. %s order(s) still open (%s queued), '
                      || '%s position(s) holding %s shares at a $%s cost basis. %s',
                      v_expired, v_open, v_queued, v_positions, round(v_shares), round(v_basis, 2), v_books_text);
  -- The n8n Summary's rule, unchanged: unbalanced books are an error; a queued
  -- order or a books check with no verdict needs a human; otherwise ok.
  v_status := case when v_books_ok is false then 'error'
                   when v_queued > 0 or v_books_ok is null then 'attention' else 'ok' end;
  v_detail := jsonb_build_object('summary', v_summary, 'expired', v_expired, 'open_orders', v_open,
                                 'queued_orders', v_queued, 'open_positions', v_positions,
                                 'open_shares', v_shares, 'cost_basis_usd', v_basis, 'books', v_books,
                                 'trigger', 'pg_cron');
  perform log_ingest('P2.2_paper_maintenance', v_status, v_expired, v_detail);
  return v_detail || jsonb_build_object('status', v_status);
end $$;

create or replace function public.run_health_watchdog()
returns jsonb language plpgsql security definer set search_path = public, pg_temp as $$
declare
  v_gate jsonb := should_run('P4.1_health_watchdog', 'schedule');
  v_failures text[] := '{}';
  v_checks jsonb := '{}';
  v_book timestamptz; v_fc timestamptz; v_age numeric;
  v_err int; v_jobs text; v_anom int;
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

revoke all on function public.run_paper_maintenance() from public, anon, authenticated;
revoke all on function public.run_health_watchdog() from public, anon, authenticated;
grant execute on function public.run_paper_maintenance() to service_role;
grant execute on function public.run_health_watchdog() to service_role;

select cron.schedule('ad4_p2_2_paper_maintenance', '31 3,9,15,21 * * *', 'select public.run_paper_maintenance()');
select cron.schedule('ad4_p4_1_health_watchdog', '58 3,9,15,21 * * *', 'select public.run_health_watchdog()');

-- Did they take, and did the last runs work?
--   select jobname, schedule, active from cron.job where jobname like 'ad4_p%';
--   select job, status, logged_at, detail->>'summary' from ingest_log
--    where job in ('P2.2_paper_maintenance', 'P4.1_health_watchdog') order by logged_at desc limit 6;
