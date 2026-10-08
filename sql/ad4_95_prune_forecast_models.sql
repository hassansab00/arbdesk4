-- ===========================================================================
-- ad4_95_prune_forecast_models.sql - EACH MODEL'S PAST FORECASTS GO TO THE
-- REPOSITORY (plan v2 P1.6 phase 2, step 4, 29 Sep)
--
-- Safe to run any time. Creates one view and one function. Deletes nothing
-- by itself - p_dry_run defaults to true and the caller must ask twice.
--
-- Hassan, 29 Sep: "Do phase 2 as planned" and "proceed pase 2 step 4".
--
-- weather_forecast_models holds every Open-Meteo model's maximum for every
-- city, day and lead (plan v2.1 P2.8): 99,292 rows from for_date 20 Aug,
-- 24 MB, ~4,400 rows per forecast date, never pruned and never archived
-- (measured 29 Sep). This gives it the prune and the archive dataset every
-- other weather table has; phase 2 step 5 sets the window.
--
-- WHO READS IT (checked 29 Sep, code and live database):
--   scripts/station_correction.py   75 days of previous-runs rows, through
--                                   scripts/weather_history.py, which reads
--                                   data/archive/forecast_models for the days
--                                   below the newest prune (step 2)
--   v_hit_forecasts                 the hit tournament's forecasts, 120 days;
--                                   SQL, so it sees only what the table keeps
--                                   (phase 2 step 6 covers it with a summary)
--   the models-current rows         the engine's forward read, for_date >=
--                                   yesterday
--
-- THE MIRROR HAS IT FIRST. scripts/mirror_to_repo.py copies this table into
-- data/mirror by observed_at, a whole UTC day a night, AFTER the prune in the
-- same workflow. Every live row was observed on or after 24 Sep 08:24Z and
-- the mirror holds them all (tools/p16_reader_proof.py, 29 Sep). The ingest
-- ignores duplicates, so a row's observed_at is when it was first written;
-- one observed since yesterday's UTC midnight and dated below the cut is a
-- hole just filled, not yet mirrored, and the prune refuses outright while
-- one exists. The archive refuses to start while the committed mirror
-- manifest is behind that midnight (mirror_first).
--
-- THE KEY. The primary key is four columns and the archive keyset-pages its
-- export on one, so v_forecast_models_export joins them into one text key:
-- city_key|model|run_at|for_date, the primary key's order, run_at in UTC to
-- the microsecond. No city key or model name holds a '|' (0 of 99,292 rows,
-- checked 29 Sep). The key is not exported.
-- ===========================================================================

create or replace view public.v_forecast_models_export
with (security_invoker = true) as
select f.city_key || '|' || f.model || '|'
         || to_char(f.run_at at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US') || '|' || f.for_date::text as model_key,
       f.*
  from public.weather_forecast_models f;

comment on view public.v_forecast_models_export is
  'weather_forecast_models with one unique, totally ordered text key (city_key|model|run_at|for_date, run_at in UTC to the microsecond) for the archive''s keyset-paged export. Service role only (plan v2 P1.6 phase 2).';

revoke all on public.v_forecast_models_export from public, anon, authenticated;
grant select on public.v_forecast_models_export to service_role;


create or replace function public.prune_forecast_models(
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
  -- Tonight's mirror exports every row observed since this instant, after
  -- the prune has run.
  v_unmirrored timestamptz := (date_trunc('day', now() at time zone 'UTC') - interval '1 day') at time zone 'UTC';
  v_doomed bigint;
  v_keep   bigint;
  v_young  bigint;
  v_unfrozen bigint;
begin
  -- TWO DAYS (Fresh Supabase, Hassan 8 Oct; 7 before, 30 until 7 Oct). Every
  -- live reader takes for_date from yesterday on (v_city_status,
  -- station_correction.load_forward, the probability inputs); station
  -- correction fits on the archive (weather_history) and the hit forecasts are
  -- frozen (v_hit_forecasts, freeze_hit_forecasts). Two, not one: 2,304 rows
  -- since 26 Sep were observed a UTC day after their date, and the guard below
  -- keeps a row until the mirror has it.
  if p_keep_days < 2 then
    return jsonb_build_object(
      'ok', false,
      'error', 'keep_days must be at least 2 - live readers take yesterday on, and the mirror copies a day after it ends'
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
    lock table public.weather_forecast_models in share row exclusive mode;
  end if;

  select count(*), count(*) filter (where observed_at >= v_unmirrored)
    into v_doomed, v_young
    from public.weather_forecast_models where for_date < v_before;

  -- Never a row the mirror has not had yet, whatever the window.
  if v_young > 0 then
    return jsonb_build_object(
      'ok', false,
      'error', format('%s of the %s rows dated before %s were observed since %s and are not in '
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

  -- Every v_hit_forecasts row for the days going is frozen (plan v2 P1.6
  -- phase 2, step 6): hit_tournament.py reads 120 days, the table keeps less.
  select count(*) into v_unfrozen from (
    select city_key, for_date, lane, model, forecast_max_c, known_at
      from public.v_hit_forecasts_live where for_date < v_before
    except
    select city_key, for_date, lane, model, forecast_max_c, known_at
      from public.derived_hit_forecasts where for_date < v_before
  ) x;

  if v_unfrozen > 0 then
    return jsonb_build_object(
      'ok', false,
      'error', format('%s v_hit_forecasts row(s) dated before %s are not in derived_hit_forecasts. Run '
                      'common.refresh_feature_cache first (it freezes them) - the hit tournament reads '
                      'them after the prune.', v_unfrozen, v_before),
      'unfrozen_rows', v_unfrozen,
      'would_delete', v_doomed
    );
  end if;

  select count(*) into v_keep
    from public.weather_forecast_models where for_date >= v_before;

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

  delete from public.weather_forecast_models where for_date < v_before;

  return jsonb_build_object(
    'ok', true,
    'deleted', v_doomed,
    'kept', v_keep,
    'older_than', v_before,
    'expected_rows', p_expected_rows,
    'table_now', pg_size_pretty(pg_total_relation_size('public.weather_forecast_models')),
    'note', 'request_reclaim returns the space the same night'
  );
end;
$function$;

-- SECURITY DEFINER and deletes rows: service_role only (plan v2 P1.1).
revoke execute on function public.prune_forecast_models(integer, boolean, date, bigint) from public, anon, authenticated;
grant execute on function public.prune_forecast_models(integer, boolean, date, bigint) to service_role;

comment on function public.prune_forecast_models(integer, boolean, date, bigint) is
  'Delete weather_forecast_models rows dated before the cutoff (at least 2 days back; 7 until 8 Oct, 30 until 7 Oct), only when the caller''s count read back from the committed archive file matches exactly, none was observed since yesterday''s UTC midnight (the repo mirror copies those after the prune), and every v_hit_forecasts row for those days is frozen in derived_hit_forecasts.';
