-- Keep the observation-prune safety check on the same city-local date basis
-- as v_city_day_features and derived_city_day_features.
--
-- The previous guard used UTC dates. Sparse evening observations for cities
-- east of UTC were therefore checked against the wrong cached day and caused
-- a false PRUNE REFUSED even after a successful feature-cache refresh.

create or replace function public.prune_observations(
  p_keep_days int,
  p_dry_run boolean default true,
  p_before timestamptz default null
)
returns jsonb
language plpgsql
security definer
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

  select count(*) into v_doomed
    from public.weather_observations
   where valid_at < v_before;

  if v_doomed = 0 then
    return jsonb_build_object(
      'ok', true,
      'deleted', 0,
      'note', format('nothing older than %s', v_before)
    );
  end if;

  -- derived_city_day_features is keyed by each city's local calendar day.
  -- The prune guard must derive the candidate key identically.
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
    'table_now', v_freed,
    'note', 'run VACUUM FULL weather_observations to return the space to the OS'
  );
end;
$ad4$;

comment on function public.prune_observations(int, boolean, timestamptz) is
  'Delete raw observations older than p_before (or p_keep_days if not given). Refuses unless every affected city-local day is already in derived_city_day_features. Dry run by default. Pass p_before with the exact instant exported so only archived rows can be deleted.';

revoke all on function public.prune_observations(int, boolean, timestamptz)
  from public, anon, authenticated;

grant execute on function public.prune_observations(int, boolean, timestamptz)
  to service_role;
