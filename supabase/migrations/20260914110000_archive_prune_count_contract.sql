-- Bind every destructive weather archive prune to the exact number of rows
-- that the caller exported and verified. This closes two failure modes:
-- PostgREST silently capping an export page, and late historical rows arriving
-- between export and deletion.

drop function if exists public.prune_observations(integer, boolean, timestamptz);
drop function if exists public.prune_observations(integer, boolean, timestamptz, bigint);

create function public.prune_observations(
  p_keep_days integer,
  p_dry_run boolean default true,
  p_before timestamptz default null,
  p_expected_rows bigint default null
)
returns jsonb
language plpgsql
security definer
set search_path to 'public', 'pg_temp'
as $ad4$
declare
  v_before timestamptz := coalesce(
    p_before,
    (current_date - p_keep_days)::timestamptz
  );
  v_cut date := (v_before at time zone 'UTC')::date;
  v_doomed bigint;
  v_cached_before bigint;
  v_uncovered bigint;
  v_freed text;
begin
  if p_keep_days < 30 then
    return jsonb_build_object(
      'ok', false,
      'error', 'keep_days must be at least 30 - the model needs months, not days'
    );
  end if;

  if not p_dry_run and p_expected_rows is null then
    return jsonb_build_object(
      'ok', false,
      'error', 'p_expected_rows is required for a committed prune'
    );
  end if;

  if not p_dry_run then
    lock table public.weather_observations in share row exclusive mode;
  end if;

  select count(*) into v_doomed
    from public.weather_observations
   where valid_at < v_before;

  if p_expected_rows is not null and v_doomed <> p_expected_rows then
    return jsonb_build_object(
      'ok', false,
      'error', format(
        'archive row count mismatch: verified %s rows but prune would delete %s',
        p_expected_rows,
        v_doomed
      ),
      'expected_rows', p_expected_rows,
      'would_delete', v_doomed
    );
  end if;

  if v_doomed = 0 then
    return jsonb_build_object(
      'ok', true,
      'deleted', 0,
      'note', format('nothing older than %s', v_before)
    );
  end if;

  select count(*) into v_uncovered
  from (
    select distinct
           o.city_key,
           (o.valid_at at time zone coalesce(c.timezone, 'UTC'))::date as d
      from public.weather_observations o
      join public.cities c on c.city_key = o.city_key
     where o.valid_at < v_before
  ) x
  where not exists (
    select 1
      from public.derived_city_day_features f
     where f.city_key = x.city_key
       and f.obs_date = x.d
  );

  if v_uncovered > 0 then
    return jsonb_build_object(
      'ok', false,
      'error', format(
        '%s city-day(s) older than %s are not in derived_city_day_features. Run select refresh_feature_cache(); first - pruning now would destroy them.',
        v_uncovered,
        v_cut
      ),
      'uncovered_city_days', v_uncovered
    );
  end if;

  select count(*) into v_cached_before
    from public.derived_city_day_features;

  if p_dry_run then
    return jsonb_build_object(
      'ok', true,
      'dry_run', true,
      'would_delete', v_doomed,
      'older_than', v_before,
      'cached_city_days', v_cached_before,
      'expected_rows', p_expected_rows,
      'note', 'call again with p_dry_run => false to actually delete'
    );
  end if;

  delete from public.weather_observations
   where valid_at < v_before;

  v_freed := pg_size_pretty(
    pg_total_relation_size('public.weather_observations')
  );

  return jsonb_build_object(
    'ok', true,
    'deleted', v_doomed,
    'older_than', v_before,
    'cached_city_days', v_cached_before,
    'expected_rows', p_expected_rows,
    'table_now', v_freed,
    'note', 'run VACUUM FULL weather_observations to return the space to the OS'
  );
end;
$ad4$;

comment on function public.prune_observations(integer, boolean, timestamptz, bigint) is
  'Delete archived observations only when p_expected_rows matches the verified export count and every affected city-local day is cached. Committed calls require the expected count.';

revoke all on function public.prune_observations(integer, boolean, timestamptz, bigint)
  from public, anon, authenticated;
grant execute on function public.prune_observations(integer, boolean, timestamptz, bigint)
  to service_role;


drop function if exists public.prune_forecasts(integer, boolean, date);
drop function if exists public.prune_forecasts(integer, boolean, date, bigint);

create function public.prune_forecasts(
  p_keep_days integer,
  p_dry_run boolean default true,
  p_before date default null,
  p_expected_rows bigint default null
)
returns jsonb
language plpgsql
security definer
set search_path to 'public', 'pg_temp'
as $ad4$
declare
  v_before date := coalesce(p_before, current_date - p_keep_days);
  v_doomed bigint;
  v_keep bigint;
  v_skill bigint;
  v_skill_at timestamptz;
  v_newest_doomed date;
  v_freed text;
begin
  if p_keep_days < 30 then
    return jsonb_build_object(
      'ok', false,
      'error', 'keep_days must be at least 30 - skill is measured over months'
    );
  end if;

  if not p_dry_run and p_expected_rows is null then
    return jsonb_build_object(
      'ok', false,
      'error', 'p_expected_rows is required for a committed prune'
    );
  end if;

  if not p_dry_run then
    lock table public.weather_forecasts in share row exclusive mode;
  end if;

  select count(*), max(for_date) into v_doomed, v_newest_doomed
    from public.weather_forecasts
   where for_date < v_before;

  if p_expected_rows is not null and v_doomed <> p_expected_rows then
    return jsonb_build_object(
      'ok', false,
      'error', format(
        'archive row count mismatch: verified %s rows but prune would delete %s',
        p_expected_rows,
        v_doomed
      ),
      'expected_rows', p_expected_rows,
      'would_delete', v_doomed
    );
  end if;

  if v_doomed = 0 then
    return jsonb_build_object(
      'ok', true,
      'deleted', 0,
      'note', format('nothing older than %s', v_before)
    );
  end if;

  select count(*), max(computed_at) into v_skill, v_skill_at
    from public.derived_forecast_skill;

  if v_skill = 0 then
    return jsonb_build_object(
      'ok', false,
      'error', 'derived_forecast_skill is empty - these forecasts have never been scored, and the score is what survives the prune. Run the daily pipeline first.',
      'would_delete', v_doomed
    );
  end if;

  if v_skill_at is null or v_skill_at::date < v_newest_doomed then
    return jsonb_build_object(
      'ok', false,
      'error', format(
        'derived_forecast_skill was last computed %s, before the newest forecast being removed (%s). Their contribution was never measured. Run the daily pipeline first.',
        coalesce(v_skill_at::date::text, 'never'),
        v_newest_doomed
      ),
      'would_delete', v_doomed
    );
  end if;

  select count(*) into v_keep
    from public.weather_forecasts
   where for_date >= v_before;

  if p_dry_run then
    return jsonb_build_object(
      'ok', true,
      'dry_run', true,
      'would_delete', v_doomed,
      'would_keep', v_keep,
      'older_than', v_before,
      'skill_rows', v_skill,
      'skill_computed_at', v_skill_at,
      'expected_rows', p_expected_rows,
      'note', 'call again with p_dry_run => false to actually delete'
    );
  end if;

  delete from public.weather_forecasts
   where for_date < v_before;

  v_freed := pg_size_pretty(
    pg_total_relation_size('public.weather_forecasts')
  );

  return jsonb_build_object(
    'ok', true,
    'deleted', v_doomed,
    'kept', v_keep,
    'older_than', v_before,
    'skill_rows', v_skill,
    'expected_rows', p_expected_rows,
    'table_now', v_freed,
    'note', 'run VACUUM FULL weather_forecasts to return the space to the OS'
  );
end;
$ad4$;

comment on function public.prune_forecasts(integer, boolean, date, bigint) is
  'Delete archived forecasts only when p_expected_rows matches the verified export count and derived_forecast_skill proves the rows were scored. Committed calls require the expected count.';

revoke all on function public.prune_forecasts(integer, boolean, date, bigint)
  from public, anon, authenticated;
grant execute on function public.prune_forecasts(integer, boolean, date, bigint)
  to service_role;
