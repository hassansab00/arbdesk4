-- ===========================================================================
-- A RED BOX ON EVERY INTRADAY RUN
--
-- The "Preserve predictive and synthesis evidence" step has been failing on
-- every scheduled cycle:
--
--   capture_research_state -> HTTP 500: {"code":"57014",
--   "message":"canceling statement due to statement timeout"}
--
-- TWO CAUSES, and both are worth fixing rather than either alone.
--
-- 1. THE FUNCTION NEVER RAISED ITS OWN CEILING. It runs from an API role
--    whose statement_timeout is a few seconds - right for a page, wrong for a
--    job that hashes every row of three views. It died at 8 seconds.
--
-- 2. IT CAPTURED THE EXPENSIVE VIEW SIX TIMES A DAY. v_forecast_convergence
--    is a forecast curve per city, model, lead and day - about 22,000 rows -
--    recomputed and hashed on every intraday cycle. It was 30,914 of
--    research_captures' 78,291 rows, the single largest contributor to the
--    table that took the database to 643 MB against a 500 MB tier, and six
--    snapshots of a convergence curve in one day are six near-identical
--    snapshots.
--
--    The other two views are small and stay on every cycle: a finding or a
--    learning-state change is exactly the thing that moves between cycles.
--
-- The skip is decided from the DATA, not from a workflow parameter, so a
-- manual run, a backfill and the schedule all behave the same way and nobody
-- has to remember a flag. Verified on the live database: first call captured
-- 21,931 rows in seconds, second call returned
-- skipped_this_cycle: ["v_forecast_convergence"].
-- ===========================================================================
create or replace function public.capture_research_state(p_command text, p_engine_version text)
returns jsonb
language plpgsql
set search_path to ''
as $function$
declare
  t text;
  n integer;
  total integer := 0;
  stamp timestamptz := clock_timestamp();
  skipped text[] := '{}';
  last_at timestamptz;
begin
  if nullif(p_command,'') is null or nullif(p_engine_version,'') is null then
    raise exception 'command and deployed engine version required';
  end if;

  -- The API role's few seconds are right for a page and wrong for a job that
  -- hashes every row of three views. LOCAL, so it ends with this statement.
  set local statement_timeout = '120s';

  foreach t in array array['v_synthesis_findings','v_learning_state','v_forecast_convergence'] loop
    if to_regclass('public.'||t) is null then raise exception 'Missing research source %',t; end if;

    if t = 'v_forecast_convergence' then
      select max(captured_at) into last_at
        from public.research_captures where source_relation = t;
      if last_at is not null and last_at > stamp - interval '20 hours' then
        skipped := skipped || t;
        continue;
      end if;
    end if;

    execute format('insert into public.research_captures(command_key,captured_at,engine_version,provenance,source_relation,source_key,payload,payload_hash)
      select $1||'':''||$2||'':''||md5(to_jsonb(v)::text), $3,$4,''forward_capture'',$2,
      md5(to_jsonb(v)::text),to_jsonb(v),md5(to_jsonb(v)::text) from public.%I v
      on conflict do nothing',t) using p_command,t,stamp,p_engine_version;
    get diagnostics n=row_count; total:=total+n;
  end loop;

  return jsonb_build_object('rows',total,'command',p_command,'captured_at',stamp,
                            'skipped_this_cycle',to_jsonb(skipped),
                            'note','v_forecast_convergence is captured once a day; the other two every cycle');
end $function$;

comment on function public.capture_research_state(text, text) is
  'Freezes the predictive and synthesis state as research evidence. Raises its own statement timeout, because the API role''s few seconds are right for a page and wrong for a job that hashes every row of three views - and captures v_forecast_convergence once a day rather than six times, which was 30,914 of research_captures'' 78,291 rows.';
