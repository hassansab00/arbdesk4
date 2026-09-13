-- ===========================================================================
-- ad4_61_verified_outcome_view_access.sql
--
-- THE THREE /predictive PANELS, AND WHY THEY BROKE TWICE IN ONE DAY.
--
-- Safe to run any time. Changes one view's execution context. No data, no
-- grants, no policies.
--
-- Round one: ad4_57 granted anon SELECT on weather_resolution_evidence
-- table-wide, which includes raw_payload and source_url - the raw station
-- response and where it was fetched from. Those are proprietary, and
-- supabase/migrations/20260913100000_phase2a_verified_outcome_truth.sql says
-- so plainly: "Raw payloads and source URLs remain worker-only even in the
-- single-user/no-login deployment." It grants thirteen verdict columns.
--
-- Round two: narrowing my grant to those thirteen columns put the panels
-- straight back to 42501, because
--
--     v_verified_weather_outcomes  with (security_invoker = true)
--
-- has an inner `ranked` CTE that SELECTS e.source_url and e.raw_payload
-- before the outer SELECT drops them. A security_invoker view is evaluated
-- with the CALLER's privileges, and the caller needs SELECT on every column
-- the view READS, not merely those it returns. So under the correct grant
-- that view cannot be read by anon at all - and v_prediction_scorecard and
-- v_verified_fact_forecast_outcome fail through it.
--
-- Those two facts cannot both hold. Either anon may read the proprietary
-- columns, or that view cannot run as invoker.
--
--
-- THE VIEW IS THE SAFEGUARD, SO THE VIEW SHOULD OWN THE PRIVILEGE
--
-- v_verified_weather_outcomes exists precisely to publish the verdict and
-- withhold the payload: its output is twelve safe columns and nothing else.
-- Running it as DEFINER means anon never touches the base table at all -
-- STRICTER than a column grant, not looser, because anon then cannot select
-- raw_payload even directly. The proprietary columns stay worker-only, and
-- the panels work.
--
-- What this does NOT do: widen any grant, alter any policy, or change the
-- other security_invoker views. It flips one property on one view, and it is
-- reversible in one statement.
--
-- The alternative fix is to drop those two columns from the CTE, which is
-- one line inside that migration. If its author prefers that, do it there and
-- this file becomes a no-op worth deleting - set security_invoker back to
-- true and the panels keep working.
-- ===========================================================================

do $ad4$
begin
  if to_regclass('public.v_verified_weather_outcomes') is null then
    raise notice 'ad4_61: no v_verified_weather_outcomes - nothing to do';
    return;
  end if;

  alter view public.v_verified_weather_outcomes set (security_invoker = false);
  raise notice 'ad4_61: v_verified_weather_outcomes now runs as definer';
end $ad4$;

-- --------------------------------------------------------------------------
-- Prove both halves: the panels read, and the payload stays shut.
-- --------------------------------------------------------------------------
do $ad4$
declare r text; msg text; v_bad text[] := '{}';
begin
  foreach r in array array['v_verified_weather_outcomes',
                           'v_verified_fact_forecast_outcome',
                           'v_prediction_scorecard'] loop
    if to_regclass('public.' || r) is null then continue; end if;
    begin
      set local role anon;
      execute format('select 1 from public.%I limit 1', r);
      reset role;
    exception when others then
      reset role;
      get stacked diagnostics msg = MESSAGE_TEXT;
      v_bad := v_bad || format('%s (%s)', r, left(msg, 80));
    end;
  end loop;
  if array_length(v_bad, 1) > 0 then
    raise exception 'ad4_61: still unreadable as anon: %', array_to_string(v_bad, '; ');
  end if;

  if has_column_privilege('anon', 'public.weather_resolution_evidence', 'raw_payload', 'SELECT')
     or has_column_privilege('anon', 'public.weather_resolution_evidence', 'source_url', 'SELECT') then
    raise exception 'ad4_61: anon can still read raw_payload or source_url - that is the thing this must never do';
  end if;

  raise notice 'ad4_61: panels read; raw_payload and source_url remain worker-only';
end $ad4$;
