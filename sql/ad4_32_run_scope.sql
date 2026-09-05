-- ===========================================================================
-- ad4_32_run_scope.sql - run a job for the cities you care about, not all 37.
--
-- WHAT THIS IS ACTUALLY WORTH, because the answer differs per job and saying
-- "more efficient" without saying where is how a feature gets built in the
-- wrong place.
--
--   n8n bills per EXECUTION, not per request. Scoping does not reduce the
--   execution count at all. What it reduces is RUNTIME and load on the
--   upstream API - and that matters exactly where a job makes one request per
--   item:
--
--     P0.3 book snapshot   one request per TOKEN: two per band, about eleven
--                          bands a city, 37 cities - roughly 800 requests a
--                          run. Scoping to three cities makes that 66.
--     P0.4 trade history   one request per market.
--     P1.2 NWS monitor     three requests per city - observations, alerts,
--                          point. 111 down to 9.
--
--   And where a job batches, scoping saves almost nothing and should not be
--   used to pretend otherwise:
--
--     P1.5 Open-Meteo      ONE request carries every city. Scoping it changes
--                          the length of a URL.
--
-- So the real gain is that a scoped run FINISHES. An all-cities book snapshot
-- can exceed the workflow timeout and write nothing; three cities cannot. And
-- it gives the desk the move it actually wants before trading a city: refresh
-- that city now, rather than waiting for the next full cycle.
--
-- HOW IT IS CARRIED. should_run() already answers every workflow at node two.
-- It now returns the city list with the answer, so scoping cost zero new nodes
-- in eleven files - and a workflow that ignores the field keeps working
-- exactly as before.
--
-- Run order: after sql/ad4_20_schedules.sql. Re-runnable.
-- ===========================================================================

do $ad4$
begin
  if to_regprocedure('should_run(text,text)') is null then
    raise exception 'ad4_32 needs should_run - run sql/ad4_20_schedules.sql first';
  end if;
end
$ad4$;


-- --------------------------------------------------------------------------
-- 1. The scope, per job. Absent means every city, which is the old behaviour
--    and stays the default: a job that has never been scoped must not
--    silently start doing less than it used to.
-- --------------------------------------------------------------------------
insert into settings (key, value)
values ('run_scope', '{
  "_doc": "Per job: {\"mode\": \"all\" | \"selected\", \"cities\": [\"nyc\", \"chicago\"]}. Absent or mode=all means every active city. Set from the Workflows page.",
  "_default": {"mode": "all", "cities": []}
}'::jsonb)
on conflict (key) do nothing;


-- --------------------------------------------------------------------------
-- 2. Resolve it. One place, so the UI preview and the workflow cannot differ.
-- --------------------------------------------------------------------------
create or replace function run_scope(p_job text)
returns jsonb language plpgsql stable security definer as $ad4$
declare
  v_cfg    jsonb;
  v_mode   text;
  v_wanted text[];
  v_cities text[];
  v_all    int;
begin
  select value -> p_job into v_cfg from settings where key = 'run_scope';
  v_mode := coalesce(v_cfg ->> 'mode', 'all');

  select count(*) into v_all from cities
   where coalesce(status, 'active') = 'active';

  if v_mode <> 'selected' then
    return jsonb_build_object('mode', 'all', 'cities', null,
                              'n', v_all, 'of', v_all);
  end if;

  select array_agg(value::text) into v_wanted
    from jsonb_array_elements_text(coalesce(v_cfg -> 'cities', '[]'::jsonb)) as value;

  -- Only cities that actually exist and are active. A stale key left in the
  -- list after a city is retired must not shrink the run to nothing silently.
  select array_agg(c.city_key order by c.city_key) into v_cities
    from cities c
   where coalesce(c.status, 'active') = 'active'
     and c.city_key = any(coalesce(v_wanted, array[]::text[]));

  -- An empty or entirely stale selection falls back to everything. The
  -- alternative - running zero cities - looks identical to a broken job.
  if v_cities is null or cardinality(v_cities) = 0 then
    return jsonb_build_object('mode', 'all', 'cities', null,
                              'n', v_all, 'of', v_all,
                              'note', 'scope was set to selected but matched no active city - running all');
  end if;

  return jsonb_build_object('mode', 'selected', 'cities', to_jsonb(v_cities),
                            'n', cardinality(v_cities), 'of', v_all);
end;
$ad4$;

comment on function run_scope(text) is
  'Which cities a job should cover this run. Absent, all, or a selection that matches nothing all mean every active city - a scope must never silently reduce a run to zero.';


-- --------------------------------------------------------------------------
-- 3. should_run carries it, so no workflow needs a new node.
--
--    Every answer gains a `scope` object. A workflow that does not read it
--    behaves exactly as before, which is what makes this safe to ship ahead
--    of the workflows that use it.
-- --------------------------------------------------------------------------
create or replace function should_run(p_job text, p_trigger text default 'schedule')
returns jsonb language plpgsql security definer as $ad4$
declare
  v_cfg     jsonb;
  v_mode    text;
  v_every   int;
  v_last    timestamptz;
  v_mins    numeric;
  v_scope   jsonb := run_scope(p_job);
begin
  select value -> p_job into v_cfg from settings where key = 'workflow_schedules';

  -- Unknown job, or no configuration at all: run. A missing setting must never
  -- silently disable a workflow.
  if v_cfg is null then
    return jsonb_build_object('run', true, 'scope', v_scope,
                              'reason', 'no schedule configured for ' || p_job);
  end if;

  v_mode  := coalesce(v_cfg ->> 'mode', 'auto');
  v_every := coalesce((v_cfg ->> 'every_minutes')::int, 0);

  -- A run the operator asked for always happens, whatever the schedule says.
  if p_trigger is distinct from 'schedule' then
    return jsonb_build_object('run', true, 'scope', v_scope,
                              'reason', 'manual trigger: ' || p_trigger);
  end if;

  if v_mode = 'off' then
    return jsonb_build_object('run', false, 'scope', v_scope,
                              'reason', p_job || ' is switched off');
  end if;
  if v_mode = 'manual' then
    return jsonb_build_object('run', false, 'scope', v_scope,
      'reason', p_job || ' is manual-only - use the Run button');
  end if;
  if v_every <= 0 then
    return jsonb_build_object('run', true, 'scope', v_scope,
                              'reason', 'no minimum interval set');
  end if;

  select max(logged_at) into v_last from ingest_log where job = p_job and status <> 'skipped';
  if v_last is null then
    return jsonb_build_object('run', true, 'scope', v_scope, 'reason', 'never run');
  end if;

  v_mins := extract(epoch from (now() - v_last)) / 60.0;
  if v_mins < v_every then
    return jsonb_build_object(
      'run', false, 'scope', v_scope,
      'reason', format('%s ran %s min ago; minimum interval is %s min',
                       p_job, round(v_mins), v_every),
      'minutes_since', round(v_mins), 'every_minutes', v_every);
  end if;

  return jsonb_build_object(
    'run', true, 'scope', v_scope,
    'reason', format('%s min since last run, interval %s min', round(v_mins), v_every),
    'minutes_since', round(v_mins), 'every_minutes', v_every);
end;
$ad4$;


-- --------------------------------------------------------------------------
-- 4. Set a scope without hand-editing jsonb. The Workflows page calls this.
-- --------------------------------------------------------------------------
create or replace function set_run_scope(p_job text, p_cities text[])
returns jsonb language plpgsql security definer as $ad4$
declare v_entry jsonb;
begin
  if p_job is null or p_job = '' then
    return jsonb_build_object('ok', false, 'error', 'p_job is required');
  end if;

  -- Null or empty means "all", which is also how you clear a selection.
  if p_cities is null or cardinality(p_cities) = 0 then
    v_entry := jsonb_build_object('mode', 'all', 'cities', '[]'::jsonb);
  else
    v_entry := jsonb_build_object('mode', 'selected', 'cities', to_jsonb(p_cities));
  end if;

  insert into settings (key, value)
  values ('run_scope', jsonb_build_object(p_job, v_entry))
  on conflict (key) do update set value = settings.value || jsonb_build_object(p_job, v_entry);

  return run_scope(p_job);
end;
$ad4$;

comment on function set_run_scope(text, text[]) is
  'Set which cities a job covers. Pass null or an empty array to go back to all.';


-- --------------------------------------------------------------------------
-- 5. What every job is scoped to, for the Workflows page.
-- --------------------------------------------------------------------------
create or replace view v_run_scope as
select
  j.job,
  (run_scope(j.job) ->> 'mode')                         as mode,
  (run_scope(j.job) ->> 'n')::int                       as cities_covered,
  (run_scope(j.job) ->> 'of')::int                      as cities_total,
  run_scope(j.job) -> 'cities'                          as cities,
  -- Where scoping actually changes the work. Saying "more efficient" about a
  -- job that batches every city into one request is not true.
  case j.job
    when 'P0.3_book_volume_snapshot' then 'one request per token - scoping is the difference between finishing and timing out'
    when 'P0.4_trade_history'        then 'one request per market - scoping cuts the run proportionally'
    when 'P1.2_nws_monitor'          then 'three requests per city - scoping cuts the run proportionally'
    when 'P1.5_open_meteo'           then 'one request carries every city - scoping changes the length of a URL, nothing else'
    else 'batched or per-city-cheap - scope for focus, not for speed'
  end                                                   as effect
from (select jsonb_object_keys(value) as job
        from settings where key = 'workflow_schedules') j
order by j.job;

comment on view v_run_scope is
  'Which cities each job covers, and whether scoping it actually saves anything. A job that batches every city into one request gains nothing from a narrower scope.';


-- --------------------------------------------------------------------------
-- 6. Grants. The browser sets a scope; it already sets a schedule.
-- --------------------------------------------------------------------------
do $ad4$
declare r text;
begin
  foreach r in array array['anon', 'authenticated', 'service_role'] loop
    if exists (select 1 from pg_roles where rolname = r) then
      execute format('grant select on v_run_scope to %I', r);
      execute format('grant execute on function run_scope(text) to %I', r);
      execute format('grant execute on function set_run_scope(text, text[]) to %I', r);
    end if;
  end loop;
end
$ad4$;


do $ad4$
declare v_n int;
begin
  select count(*) into v_n from v_run_scope where mode = 'selected';
  raise notice 'ad4_32: % job(s) are scoped to a selection; the rest cover every active city', v_n;
  raise notice 'ad4_32: set it on the Workflows page, or select set_run_scope(''P0.3_book_volume_snapshot'', array[''nyc'',''chicago'']);';
  raise notice 'ad4_32: back to everything - select set_run_scope(''P0.3_book_volume_snapshot'', null);';
end
$ad4$;
