-- ===========================================================================
-- ad4_57_resolution_evidence_read.sql
--
-- THREE PANELS ON /predictive SHOWED A PERMISSION ERROR TO EVERYONE.
--
-- Safe to run any time. Grants read access and adds one RLS policy. Creates
-- nothing, changes no data, and rewires no view.
--
--     permission denied for table weather_resolution_evidence (42501)
--
-- on "Actual against predicted", "How fast the forecast decays" and "Hit
-- rate, per city, per lead" - all three of which read v_prediction_scorecard.
--
--
-- WHY A GRANT ON A TABLE NOBODY QUERIES FIXES A VIEW
--
-- v_prediction_scorecard now reads v_verified_weather_outcomes, and that view
-- is declared `security_invoker`. A normal view runs with its OWNER's rights,
-- so granting select on the view is enough. A security_invoker view runs with
-- the CALLER's rights, so Postgres checks anon against every base table the
-- view touches - and reports the failure against the base table, which is why
-- the browser names a table the web app never mentions.
--
-- Both views were already granted to anon. The grant that was missing is the
-- one underneath them, and without it the pair could never have worked.
--
--
-- WHY ANON MAY READ IT
--
-- weather_resolution_evidence holds the settlement observation for a city-day
-- and where it came from: station id, source authority, source url, the
-- observed maximum, a hash of the payload. That is the same class of public
-- weather data as weather_observations and weather_forecasts, which ad4_rls
-- has always made anon-readable by the same pattern used here. There is
-- nothing private in it - no keys, no account, no position.
--
--
-- WHAT THIS DOES NOT FIX
--
-- weather_resolution_evidence is EMPTY, so v_verified_weather_outcomes and
-- v_prediction_scorecard both return zero rows even to postgres. The 2,268
-- settled outcomes are still in fact_forecast_outcome, which the scorecard no
-- longer reads.
--
-- So this turns three red error boxes into three honest empty states, and the
-- panels stay empty until whatever fills weather_resolution_evidence runs.
-- Pointing the scorecard back at fact_forecast_outcome would repopulate them
-- immediately and would also undo a deliberate design decision - verified
-- settlement evidence instead of unverified outcomes - so it is not done here.
-- ===========================================================================

do $ad4$
declare r text;
begin
  if to_regclass('public.weather_resolution_evidence') is null then
    raise notice 'ad4_57: no weather_resolution_evidence - nothing to grant';
    return;
  end if;

  -- THE VERDICT COLUMNS ONLY. THIS WAS A TABLE-WIDE GRANT AND THAT WAS WRONG.
  --
  -- The first version of this file did `grant select on
  -- weather_resolution_evidence`, which covers every column - including
  -- raw_payload and source_url. Those two are the proprietary half: the raw
  -- station response and where it was fetched from.
  --
  -- supabase/migrations/20260913100000_phase2a_verified_outcome_truth.sql is
  -- explicit about it - "Raw payloads and source URLs remain worker-only even
  -- in the single-user/no-login deployment" - and grants exactly the thirteen
  -- columns below. That migration had not been applied to the live database
  -- when this file first ran, so nothing narrowed the grant back down and
  -- anon could read both columns until this was corrected.
  --
  -- The list is copied from that migration verbatim and must stay equal to
  -- it. This file exists to make the browser's queries work, not to widen
  -- what the browser may see; if the two ever disagree, that one is right.
  foreach r in array array['anon', 'authenticated'] loop
    if exists (select 1 from pg_roles where rolname = r) then
      execute format('revoke select on public.weather_resolution_evidence from %I', r);
      execute format($g$grant select (evidence_id, city_key, for_date, observed_max_c,
                                      unit, source_authority, station_id, record_status,
                                      observed_at, captured_at, parser_version,
                                      payload_sha256, supersedes_evidence_id)
                        on public.weather_resolution_evidence to %I$g$, r);
    end if;
  end loop;

  -- A grant alone is not enough under row level security: with RLS on and no
  -- SELECT policy, anon reads zero rows without an error - which would be
  -- indistinguishable from "no evidence collected yet" and would hide this
  -- the next time it happens.
  alter table public.weather_resolution_evidence enable row level security;
  drop policy if exists anon_read on public.weather_resolution_evidence;
  create policy anon_read on public.weather_resolution_evidence
    for select to anon, authenticated using (true);

  raise notice 'ad4_57: weather_resolution_evidence readable by anon and authenticated';
end $ad4$;


-- --------------------------------------------------------------------------
-- Prove it, as the browser does.
--
-- has_table_privilege is not the test - a security_invoker view can hold a
-- grant and still fail on a base table, which is the entire bug. The only
-- honest check is to become the role and run the query.
-- --------------------------------------------------------------------------
do $ad4$
declare r text; msg text; st text; v_bad text[] := '{}';
begin
  foreach r in array array['v_verified_weather_outcomes', 'v_prediction_scorecard',
                           'v_outcome_evidence_health'] loop
    if to_regclass('public.' || r) is null then continue; end if;
    begin
      set local role anon;
      execute format('select 1 from public.%I limit 1', r);
      reset role;
    exception when others then
      reset role;
      get stacked diagnostics msg = MESSAGE_TEXT, st = RETURNED_SQLSTATE;
      v_bad := v_bad || format('%s (%s: %s)', r, st, left(msg, 100));
    end;
  end loop;

  if array_length(v_bad, 1) > 0 then
    raise exception 'ad4_57: still unreadable as anon: %', array_to_string(v_bad, '; ');
  end if;
  raise notice 'ad4_57: every dependent view reads cleanly as anon';
end $ad4$;
