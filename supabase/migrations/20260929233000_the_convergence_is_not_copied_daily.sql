-- ===========================================================================
-- THE CONVERGENCE VIEW IS NOT COPIED EVERY DAY (plan v2 P1.6 phase 3, step
-- 3.3, 29 Sep)
--
-- Hassan, 29 Sep: "go ahead 3.3 then 3.2".
--
-- capture_research_state copies every row of v_synthesis_findings,
-- v_learning_state and - once a day - v_forecast_convergence into
-- research_captures, skipping a row whose hash it already holds. The table
-- keeps two days (prune_research_captures, after the archive has exported
-- them to data/archive/research), so after two days it no longer holds the
-- hash and copies the unchanged row again. Measured 29 Sep 19:30Z:
--
--   v_forecast_convergence   30,018 rows, 15 MB of the table's 38 MB
--                            (captures of 27 Sep 5,903, 28 Sep 2,877,
--                            29 Sep 21,238)
--   everything else          7,885 rows, 9.4 MB (pg_column_size, rows only)
--
-- Nothing reads these copies back (checked 29 Sep: scripts, web, tools, SQL -
-- only the prune, the archive, the freshness row, data_integrity.py and the
-- web's manual export, which read whatever Postgres holds). The view is a
-- computation over tables the repository already keeps: weather_forecasts
-- (data/archive/forecasts and data/mirror), derived_forecast_latest and
-- fact_forecast_outcome (data/mirror). The daily copy duplicates them into
-- Postgres and again into data/archive/research.
--
-- The other two views are still captured every cycle, exactly as before.
-- Idempotent; restates the function's settings (search_path, the local
-- statement timeout) because CREATE OR REPLACE FUNCTION drops what it does
-- not restate.
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
begin
  if nullif(p_command,'') is null or nullif(p_engine_version,'') is null then
    raise exception 'command and deployed engine version required';
  end if;

  -- The API role's few seconds are right for a page and wrong for a job that
  -- hashes every row of two views. LOCAL, so it ends with this statement.
  set local statement_timeout = '120s';

  -- v_forecast_convergence is no longer copied (P1.6 phase 3, step 3.3): its
  -- inputs are kept in the repository, and the copy re-duplicated them daily.
  foreach t in array array['v_synthesis_findings','v_learning_state'] loop
    if to_regclass('public.'||t) is null then raise exception 'Missing research source %',t; end if;

    execute format('insert into public.research_captures(command_key,captured_at,engine_version,provenance,source_relation,source_key,payload,payload_hash)
      select $1||'':''||$2||'':''||md5(to_jsonb(v)::text), $3,$4,''forward_capture'',$2,
      md5(to_jsonb(v)::text),to_jsonb(v),md5(to_jsonb(v)::text) from public.%I v
      on conflict do nothing',t) using p_command,t,stamp,p_engine_version;
    get diagnostics n=row_count; total:=total+n;
  end loop;

  return jsonb_build_object('rows',total,'command',p_command,'captured_at',stamp,
                            'note','v_synthesis_findings and v_learning_state every cycle; v_forecast_convergence is not copied (its inputs are in the repository)');
end $function$;

comment on function public.capture_research_state(text, text) is
  'Freezes the synthesis state (v_synthesis_findings, v_learning_state) as research evidence every cycle, skipping rows whose hash research_captures already holds. v_forecast_convergence is no longer copied: its inputs are kept in the repository (plan v2 P1.6 phase 3, step 3.3). Raises its own statement timeout.';

revoke all on function public.capture_research_state(text, text) from public, anon, authenticated;
grant execute on function public.capture_research_state(text, text) to service_role;
