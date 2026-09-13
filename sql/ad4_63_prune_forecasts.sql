-- ===========================================================================
-- ad4_63_prune_forecasts.sql - THE OTHER HALF OF THE ARCHIVE.
--
-- Safe to run any time. Creates one function. Deletes nothing by itself -
-- p_dry_run defaults to true and the caller must ask twice.
--
-- scripts/archive_observations.py has moved cold observations to a GitHub
-- Release since it was written, guarded by prune_observations(). Nothing did
-- the same for weather_forecasts, which is now the LARGER of the two:
--
--     weather_forecasts      124 MB   343,097 rows   269,744 older than 180d
--     weather_observations   121 MB   519,649 rows   292,356 older than 180d
--
-- 78 MB of that 124 is indexes, so pruning 79% of the rows returns far more
-- than the heap figure suggests.
--
--
-- THE GUARD IS DIFFERENT FROM prune_observations, ON PURPOSE
--
-- That one refuses unless every city-day being pruned is already in
-- derived_city_day_features - the derived row is what survives the delete.
-- The same test cannot be used here: only 2,268 forecasts have a settled
-- outcome against 343,097 rows, because an outcome exists only for days that
-- have both settled AND been banked. Requiring it would block every prune
-- forever.
--
-- What actually survives a forecast prune is derived_forecast_skill: MAE,
-- bias, p90 error and hit rate per city and lead, computed FROM these rows.
-- So the guard is that the summary exists and is not older than the data
-- being removed - if skill has not been recomputed since these forecasts
-- landed, their contribution was never measured and deleting them loses it.
--
-- The real safety net is upstream and unchanged: the archive file is uploaded,
-- RE-DOWNLOADED and its rows counted before this is called with
-- p_dry_run => false. This function is the second lock, not the first.
-- ===========================================================================

do $ad4$
begin
  if to_regclass('public.weather_forecasts') is null then
    raise exception 'ad4_63 needs weather_forecasts - run sql/ad4_phase2.sql first';
  end if;
end $ad4$;

create or replace function public.prune_forecasts(
  p_keep_days integer,
  p_dry_run   boolean default true,
  p_before    date default null
) returns jsonb
language plpgsql security definer
set search_path to 'public', 'pg_temp'
as $ad4$
declare
  -- p_before wins when the caller gives one: it is the cutoff actually
  -- exported. Letting the database recompute its own would delete whatever
  -- crossed the boundary while the export ran - unarchived.
  v_before date := coalesce(p_before, current_date - p_keep_days);
  v_doomed bigint; v_keep bigint; v_skill bigint; v_skill_at timestamptz;
  v_newest_doomed date; v_freed text;
begin
  if p_keep_days < 30 then
    return jsonb_build_object('ok', false,
      'error', 'keep_days must be at least 30 - skill is measured over months');
  end if;

  select count(*), max(for_date) into v_doomed, v_newest_doomed
    from weather_forecasts where for_date < v_before;
  if v_doomed = 0 then
    return jsonb_build_object('ok', true, 'deleted', 0,
      'note', format('nothing older than %s', v_before));
  end if;

  select count(*), max(computed_at) into v_skill, v_skill_at from derived_forecast_skill;

  if v_skill = 0 then
    return jsonb_build_object('ok', false,
      'error', 'derived_forecast_skill is empty - these forecasts have never been scored, and the score is what survives the prune. Run the daily pipeline first.',
      'would_delete', v_doomed);
  end if;

  if v_skill_at is null or v_skill_at::date < v_newest_doomed then
    return jsonb_build_object('ok', false,
      'error', format('derived_forecast_skill was last computed %s, before the newest forecast being removed (%s). Their contribution was never measured. Run the daily pipeline first.',
                      coalesce(v_skill_at::date::text, 'never'), v_newest_doomed),
      'would_delete', v_doomed);
  end if;

  select count(*) into v_keep from weather_forecasts where for_date >= v_before;

  if p_dry_run then
    return jsonb_build_object('ok', true, 'dry_run', true, 'would_delete', v_doomed,
      'would_keep', v_keep, 'older_than', v_before,
      'skill_rows', v_skill, 'skill_computed_at', v_skill_at,
      'note', 'call again with p_dry_run => false to actually delete');
  end if;

  delete from weather_forecasts where for_date < v_before;
  v_freed := pg_size_pretty(pg_total_relation_size('weather_forecasts'));

  return jsonb_build_object('ok', true, 'deleted', v_doomed, 'kept', v_keep,
    'older_than', v_before, 'skill_rows', v_skill, 'table_now', v_freed,
    'note', 'run VACUUM FULL weather_forecasts to return the space to the OS');
end;
$ad4$;

comment on function public.prune_forecasts(integer, boolean, date) is
  'Delete forecasts older than a cutoff, once derived_forecast_skill proves they were scored. Dry run by default. Intended to be called only after scripts/archive_observations.py has uploaded and re-verified the matching archive file.';

do $ad4$
declare r text;
begin
  foreach r in array array['service_role'] loop
    if exists (select 1 from pg_roles where rolname = r) then
      execute format('grant execute on function public.prune_forecasts(integer, boolean, date) to %I', r);
    end if;
  end loop;
  -- anon and authenticated deliberately get nothing: this deletes rows.
end $ad4$;
