-- ===========================================================================
-- THE PRICES KEEP THREE DAYS AND EACH MODEL'S FORECASTS TWO (Fresh Supabase:
-- Hassan, 8 Oct: "offload Supabase completely daily to the repo, keep it
-- fresh each run day"; no paid plan).
--
-- The database holds what a day's runs read; history is read from the
-- repository the archive writes before every prune (export, verify, commit,
-- then delete). Two floors move, nothing else in either function:
--
--   prune_band_probabilities  18 -> 3 days of markets. Measured 8 Oct ~17:33Z
--     (read-only audit): 147,492 rows, 43,663,360 bytes; every live reader
--     takes the markets of yesterday on or one row a band the prune's view
--     never offers. station_width_score read 14 days of every price; it now
--     reads the older ones from data/archive/probabilities through the same
--     guard weather_history uses (the newest prune's file must be in the
--     checkout).
--   prune_forecast_models  7 -> 2 days. 95,637 rows, 19,906,560 bytes; every
--     reader takes yesterday on, station correction fits on the archive, and
--     the hit forecasts are frozen.
--
-- Each function's other guards stay: the verified count, the mirror-first
-- refusal (nothing observed or computed since yesterday's UTC midnight), the
-- frozen hit forecasts, the lock. sql/ad4_95 and sql/ad4_96 match.
--
-- Both tables now shed rows every night, so their reclaim backstops go from
-- weekly to daily, as every dataset kept three days or less is: 03:00 and
-- 03:05, after the 02:36 archive (sql/ad4_66 matches). The archive still asks
-- for its own reclaim after each prune. Re-runnable: cron.schedule replaces a
-- job by name.
-- ===========================================================================

create or replace function public.prune_band_probabilities(
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
  -- Tonight's mirror exports every row computed since this instant, after
  -- the prune has run.
  v_unmirrored timestamptz := (date_trunc('day', now() at time zone 'UTC') - interval '1 day') at time zone 'UTC';
  v_doomed bigint;
  v_young  bigint;
  v_rows   bigint;
  v_bands  bigint;
  v_gone   bigint;
begin
  -- THREE DAYS (Fresh Supabase, Hassan 8 Oct: what a day's runs do not need
  -- goes to the repository every night; 18 before, 30 until 6 Oct). The window
  -- is the market's resolution_date. Every live reader takes the markets of
  -- yesterday on (v_current_prediction, v_latest_prob, the readiness views,
  -- databank's unbanked bands) or one row a band this view never offers (the
  -- marks behind mv_city_hit_history and v_probability_reliability).
  -- station_width_score, which read 14 days of every price, reads the older
  -- ones from the archive's committed files (archived_prices).
  if p_keep_days < 3 then
    return jsonb_build_object(
      'ok', false,
      'error', 'keep_days must be at least 3 - live readers take the markets of yesterday on; older prices are read from the archive'
    );
  end if;

  if not p_dry_run and p_expected_rows is null then
    return jsonb_build_object(
      'ok', false,
      'error', 'p_expected_rows is required for a committed prune - it is the count '
               'read back from the committed archive file'
    );
  end if;

  -- A late price for an old band must become a count mismatch, not slip past
  -- the verified file.
  if not p_dry_run then
    lock table public.band_probabilities in share row exclusive mode;
  end if;

  select count(*), count(*) filter (where computed_at >= v_unmirrored)
    into v_doomed, v_young
    from public.v_prunable_band_probabilities where resolution_date < v_before;

  -- Never a row the mirror has not had yet, whatever the window.
  if v_young > 0 then
    return jsonb_build_object(
      'ok', false,
      'error', format('%s of the %s rows for markets dated before %s were computed since %s and are '
                      'not in the repo mirror yet - nothing deleted', v_young, v_doomed, v_before, v_unmirrored),
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
                              'note', format('no unread prices for markets dated before %s', v_before));
  end if;

  -- The number that says the guard works: bands still priced. Every band
  -- keeps its newest row, so it cannot change.
  select count(*), count(distinct band_id) into v_rows, v_bands from public.band_probabilities;

  if p_dry_run then
    return jsonb_build_object(
      'ok', true, 'dry_run', true,
      'would_delete', v_doomed,
      'rows_now', v_rows,
      'bands_priced', v_bands,
      'older_than', v_before,
      'expected_rows', p_expected_rows,
      'note', 'call again with p_dry_run => false to actually delete'
    );
  end if;

  delete from public.band_probabilities p
   where p.prob_id in (select v.prob_id from public.v_prunable_band_probabilities v
                        where v.resolution_date < v_before);
  -- The lock holds the table, not the facts and edges that mark a row: one
  -- written between the count and the delete changes the set. Then nothing
  -- is deleted, rather than something other than the verified file.
  get diagnostics v_gone = row_count;
  if v_gone <> v_doomed then
    raise exception 'prune_band_probabilities: counted % rows but the delete took % - rolled back, nothing deleted',
      v_doomed, v_gone;
  end if;

  return jsonb_build_object(
    'ok', true,
    'deleted', v_doomed,
    'rows_now', (select count(*) from public.band_probabilities),
    'bands_priced_before', v_bands,
    'bands_priced_after', (select count(distinct band_id) from public.band_probabilities),
    'older_than', v_before,
    'expected_rows', p_expected_rows,
    'table_now', pg_size_pretty(pg_total_relation_size('public.band_probabilities')),
    'note', 'request_reclaim returns the space the same night'
  );
end;
$function$;

-- SECURITY DEFINER and deletes rows: service_role only (plan v2 P1.1).
revoke execute on function public.prune_band_probabilities(integer, boolean, date, bigint) from public, anon, authenticated;
grant execute on function public.prune_band_probabilities(integer, boolean, date, bigint) to service_role;

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

-- The backstops, daily (the same jobs sql/ad4_66 schedules).
do $$
begin
  if exists (select 1 from pg_namespace where nspname = 'cron') then
    perform cron.schedule('ad4_reclaim_weather_forecast_models', '0 3 * * *',
                          'VACUUM (FULL, ANALYZE) public.weather_forecast_models');
    perform cron.schedule('ad4_reclaim_band_probabilities', '5 3 * * *',
                          'VACUUM (FULL, ANALYZE) public.band_probabilities');
  end if;
end $$;
