-- ===========================================================================
-- ad4_93_prune_forecast_features.sql - PAST FORECAST FEATURES GO TO THE
-- REPOSITORY (plan v2 P1.6 phase 1, step 3, 28 Sep)
--
-- Safe to run any time. Creates one view and one function. Deletes nothing
-- by itself - p_dry_run defaults to true and the caller must ask twice.
--
-- Hassan, 28 Sep: "YES APPROVED as long as we dont lose any collected data".
--
-- weather_forecast_features holds each forecast run's conditions for a
-- city-day (cloud, wind, humidity, pressure...): ~3,300 rows a day, 61,837
-- rows since 6 Sep, 14 MB (measured 28 Sep), never archived.
--
-- WHO READS IT (checked 28 Sep, code and live database):
--   v_forecast_features   newest run per city and for_date; read by
--                         scripts/weather_model.py for for_date >= today
--                         (predict_forward). The weekly fit reads
--                         derived_city_day_features, not this table.
--   v_archive_inventory   min and max run_at, for the Databank page
--   v_data_freshness      the newest row
-- So a row for a date before yesterday is read by nothing. It goes to
-- data/archive/forecast_features, read back and committed first.
--
-- TWO DAYS, NOT ONE, FOR THE MIRROR. scripts/mirror_to_repo.py already copies
-- this table into data/mirror nightly, by capture day, and it runs AFTER the
-- prune in the same workflow. A forecast's for_date is at most one day before
-- the UTC day it was captured (measured 28 Sep: 588 of 61,837 rows one day
-- before, none two - a US city's local date trails UTC). At a one-day window
-- the 02:36 prune would take rows captured yesterday before the mirror had
-- them: still in the archive file, but in one copy where every other row has
-- two. Two days means a row leaves only after the mirror has it too, and the
-- function refuses outright if a row captured since yesterday's UTC midnight
-- would go, rather than trusting that measurement to hold.
--
-- THE EXPORT NEEDS ONE KEY. The table's primary key is (city_key, for_date,
-- run_at), and the archive pages its export on a single column - keyset
-- paging is what stops a page boundary skipping a row the prune then
-- deletes anyway. v_forecast_features_export joins the three into one text
-- key, fixed-width in every part (ISO date, run_at to the microsecond in
-- UTC), so it is unique exactly when the primary key is and orders totally.
-- The key is not exported: the three columns it is made of are.
-- ===========================================================================

create or replace view public.v_forecast_features_export
with (security_invoker = true) as
select f.city_key || '|' || f.for_date::text || '|'
         || to_char(f.run_at at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US') as feature_key,
       f.*
  from public.weather_forecast_features f;

comment on view public.v_forecast_features_export is
  'weather_forecast_features with one unique, totally ordered text key (city_key|for_date|run_at in UTC to the microsecond) for the archive''s keyset-paged export. Service role only (plan v2 P1.6 phase 1).';

revoke all on public.v_forecast_features_export from public, anon, authenticated;
grant select on public.v_forecast_features_export to service_role;


create or replace function public.prune_forecast_features(
  p_keep_days     integer,
  p_dry_run       boolean default true,
  p_before        date    default null,
  p_expected_rows bigint  default null
)
returns jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $function$
declare
  -- A cutoff may be older than the floor, never newer (plan v2 P1.1).
  v_before date := least(coalesce(p_before, current_date - p_keep_days), current_date - p_keep_days);
  -- Tonight's mirror exports every row captured since this instant, after
  -- the prune has run.
  v_unmirrored timestamptz := (date_trunc('day', now() at time zone 'UTC') - interval '1 day') at time zone 'UTC';
  v_doomed bigint;
  v_keep   bigint;
  v_young  bigint;
begin
  -- TWO DAYS: weather_model reads from today on, and the repo mirror must
  -- have a row before it goes (see the header).
  if p_keep_days < 2 then
    return jsonb_build_object(
      'ok', false,
      'error', 'keep_days must be at least 2 - weather_model predicts from today''s rows forward, '
               'and the repo mirror exports yesterday''s captures after the prune'
    );
  end if;

  if not p_dry_run and p_expected_rows is null then
    return jsonb_build_object(
      'ok', false,
      'error', 'p_expected_rows is required for a committed prune - it is the count '
               'read back from the committed archive file'
    );
  end if;

  -- A late write for an old date must become a count mismatch, not slip past
  -- the verified file.
  if not p_dry_run then
    lock table public.weather_forecast_features in share row exclusive mode;
  end if;

  select count(*), count(*) filter (where captured_at >= v_unmirrored)
    into v_doomed, v_young
    from public.weather_forecast_features where for_date < v_before;

  -- Never a row the mirror has not had yet, whatever the window.
  if v_young > 0 then
    return jsonb_build_object(
      'ok', false,
      'error', format('%s of the %s rows dated before %s were captured since %s and are not in '
                      'the repo mirror yet - nothing deleted', v_young, v_doomed, v_before, v_unmirrored),
      'would_delete', v_doomed,
      'not_yet_mirrored', v_young
    );
  end if;

  if p_expected_rows is not null and v_doomed <> p_expected_rows then
    return jsonb_build_object(
      'ok', false,
      'error', format('archive row count mismatch: verified %s rows but prune would delete %s',
                      p_expected_rows, v_doomed),
      'expected_rows', p_expected_rows,
      'would_delete', v_doomed
    );
  end if;

  if v_doomed = 0 then
    return jsonb_build_object('ok', true, 'deleted', 0,
                              'note', format('nothing dated before %s', v_before));
  end if;

  select count(*) into v_keep
    from public.weather_forecast_features where for_date >= v_before;

  if p_dry_run then
    return jsonb_build_object(
      'ok', true, 'dry_run', true,
      'would_delete', v_doomed,
      'would_keep', v_keep,
      'older_than', v_before,
      'expected_rows', p_expected_rows,
      'note', 'call again with p_dry_run => false to actually delete'
    );
  end if;

  delete from public.weather_forecast_features where for_date < v_before;

  return jsonb_build_object(
    'ok', true,
    'deleted', v_doomed,
    'kept', v_keep,
    'older_than', v_before,
    'expected_rows', p_expected_rows,
    'table_now', pg_size_pretty(pg_total_relation_size('public.weather_forecast_features')),
    'note', 'request_reclaim returns the space the same night'
  );
end;
$function$;

-- SECURITY DEFINER and deletes rows: service_role only (plan v2 P1.1).
revoke execute on function public.prune_forecast_features(integer, boolean, date, bigint) from public, anon, authenticated;
grant execute on function public.prune_forecast_features(integer, boolean, date, bigint) to service_role;

comment on function public.prune_forecast_features(integer, boolean, date, bigint) is
  'Delete weather_forecast_features rows dated before the cutoff (at least two days back: weather_model reads from today on, and the repo mirror must have each row first), only when the caller''s count read back from the committed archive file matches exactly and none was captured since yesterday''s UTC midnight.';
