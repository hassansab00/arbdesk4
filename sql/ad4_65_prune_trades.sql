-- ===========================================================================
-- ad4_65_prune_trades.sql - THE TABLE THE ARCHIVE NEVER COVERED.
--
-- Safe to run any time. Creates one function. Deletes nothing by itself -
-- p_dry_run defaults to true and the caller must ask twice.
--
-- scripts/archive_observations.py moves cold weather rows to a GitHub
-- Release, guarded by prune_observations() and prune_forecasts(). Nothing did
-- the same for trades_observed, which has quietly become the LARGEST table in
-- the database:
--
--     trades_observed        91 MB   156,008 rows   90,640 older than 90d
--     research_captures      67 MB
--     weather_observations   57 MB   239,617 rows    114,283 older than 90d
--     weather_forecasts      33 MB    81,693 rows     31,787 older than 90d
--
-- 55 MB of that 91 is indexes. The table has never been vacuumed, by hand or
-- by autovacuum, and does not need to be: n_dead_tup is 0, because nothing
-- ever updates or deletes a trade. All 91 MB is LIVE rows, 58% of which are
-- older than ninety days. That is why pruning is the only thing that shrinks
-- it - there is no bloat to reclaim, only rows nothing reads.
--
--
-- WHAT SURVIVES THE PRUNE, which is the only question that matters
--
-- Nothing live reads a trade older than a day. Both consumers -
-- v_band_volume and v_city_volume - filter to
-- `now() - settings.volume_thresholds.lookback_hours`, which is 24. A trade
-- from January contributes to no price, no edge, no probability and no
-- ranking; it sits in the heap and in five indexes and is read by nothing.
--
-- What IS permanent is archive_daily_city_presence and archive_daily_rollup:
-- one row per dataset-day-city and one per dataset-day with its row count,
-- written by arbdesk_private.capture_archive_daily() on every insert. 'Trades
-- seen' reaches back to 2025-01-18 across all 54 cities. That coverage record
-- is what proves the desk was watching a city on a day, and it is untouched
-- by deleting the raw rows behind it.
--
-- So the guard mirrors prune_observations: every city-day about to lose its
-- raw trades must ALREADY be in the presence rollup. If the trigger never
-- fired for a day - a bulk load that bypassed it, a restore - that day is not
-- summarised anywhere and deleting it would be the one loss this design
-- cannot survive. It refuses, and says how many days and which cutoff.
--
-- Run:
--   select prune_trades(90);                          -- dry run, says what
--   select prune_trades(90, false, null, 90640);      -- deletes, count-bound
--
-- scripts/archive_observations.py --table trades calls both halves in that
-- order and only after the gzipped CSV has been uploaded AND read back.
-- ===========================================================================

create or replace function public.prune_trades(
  p_keep_days     integer,
  p_dry_run       boolean     default true,
  p_before        timestamptz default null,
  p_expected_rows bigint      default null
)
returns jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $function$
declare
  v_before timestamptz := coalesce(p_before, (current_date - p_keep_days)::timestamptz);
  v_cut date := (v_before at time zone 'UTC')::date;
  v_doomed bigint;
  v_keep bigint;
  v_uncovered bigint;
  v_presence bigint;
  v_freed text;
begin
  if p_keep_days < 30 then
    return jsonb_build_object(
      'ok', false,
      'error', 'keep_days must be at least 30 - the 24h volume window needs room to be wrong'
    );
  end if;

  if not p_dry_run and p_expected_rows is null then
    return jsonb_build_object(
      'ok', false,
      'error', 'p_expected_rows is required for a committed prune'
    );
  end if;

  if not p_dry_run then
    lock table public.trades_observed in share row exclusive mode;
  end if;

  select count(*) into v_doomed
    from public.trades_observed
   where traded_at < v_before;

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

  -- THE GUARD. Every city-day losing its raw trades must already be summarised
  -- in the presence rollup, which is what survives this delete.
  select count(*) into v_uncovered
  from (
    select distinct t.city_key, (t.traded_at at time zone 'UTC')::date as d
      from public.trades_observed t
     where t.traded_at < v_before
       and t.city_key is not null
  ) x
  where not exists (
    select 1
      from public.archive_daily_city_presence p
     where p.dataset = 'Trades seen'
       and p.city_key = x.city_key
       and p.day = x.d
  );

  if v_uncovered > 0 then
    return jsonb_build_object(
      'ok', false,
      'error', format(
        '%s city-day(s) older than %s have trades but no row in archive_daily_city_presence. The presence rollup is what survives this prune - deleting now would destroy the only record the desk was watching those cities. Backfill it first.',
        v_uncovered,
        v_cut
      ),
      'uncovered_city_days', v_uncovered,
      'would_delete', v_doomed
    );
  end if;

  select count(*) into v_presence
    from public.archive_daily_city_presence
   where dataset = 'Trades seen';

  select count(*) into v_keep
    from public.trades_observed
   where traded_at >= v_before or traded_at is null;

  if p_dry_run then
    return jsonb_build_object(
      'ok', true,
      'dry_run', true,
      'would_delete', v_doomed,
      'would_keep', v_keep,
      'older_than', v_before,
      'presence_rows', v_presence,
      'expected_rows', p_expected_rows,
      'note', 'call again with p_dry_run => false to actually delete'
    );
  end if;

  delete from public.trades_observed
   where traded_at < v_before;

  v_freed := pg_size_pretty(pg_total_relation_size('public.trades_observed'));

  return jsonb_build_object(
    'ok', true,
    'deleted', v_doomed,
    'kept', v_keep,
    'older_than', v_before,
    'presence_rows', v_presence,
    'expected_rows', p_expected_rows,
    'table_now', v_freed,
    'note', 'run VACUUM FULL trades_observed to return the space to the OS'
  );
end;
$function$;

grant execute on function public.prune_trades(integer, boolean, timestamptz, bigint) to service_role;

comment on function public.prune_trades(integer, boolean, timestamptz, bigint) is
  'Delete trades_observed rows older than the cutoff, only when archive_daily_city_presence already covers every affected city-day and the caller''s verified archive row count matches exactly.';
