-- ===========================================================================
-- TRADES KEEP ONE DAY, AND NEVER ONE THE INGEST MAY READ AGAIN
-- (plan v2 P1.6 phase 1, 28 Sep)
--
-- Hassan, 28 Sep: "YES APPROVED as long as we dont lose any collected data".
-- Every print older than the cutoff is still exported, read back from GitHub
-- byte for byte and committed before this deletes it, and the delete runs
-- only when the count matches the file exactly.
--
-- 1. The floor 2 -> 1 day, tied to what reads the table: v_band_volume and
--    v_city_volume look back settings.volume_thresholds.lookback_hours (24),
--    refresh_derived never lowers a stored day. The function now refuses a
--    keep_days shorter than that window, whatever the setting says.
-- 2. The cutoff may not pass the trade ingest's mark (the `since` of its
--    last run), so a stalled ingest cannot re-insert trades already archived
--    and have them archived twice.
--
-- 3. Their backstop reclaim runs daily (03:20 UTC), not weekly: the archive
--    now sheds a day of trades every night (sql/ad4_66).
--
-- The body is the one in sql/ad4_65_prune_trades.sql. Re-runnable.
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
  -- THE FLOOR HOLDS FOR p_before TOO (plan v2 P1.1). This was
  -- coalesce(p_before, ...), so a caller passing p_before => now() deleted
  -- every row it could reach with the 30-day check above never consulted.
  -- A cutoff may be older than the floor, never newer. The archiver passes
  -- its own now() - keep_days, which is always earlier than this now(), so a
  -- legitimate cutoff is never moved.
  v_before timestamptz := least(coalesce(p_before, (current_date - p_keep_days)::timestamptz),
                                now() - make_interval(days => p_keep_days));
  v_cut date := (v_before at time zone 'UTC')::date;
  v_doomed bigint;
  v_keep bigint;
  v_uncovered bigint;
  v_presence bigint;
  v_freed text;
  v_lookback int;
  v_mark timestamptz;
begin
  -- ONE DAY, NEVER LESS THAN WHAT READS IT (plan v2 P1.6 phase 1, 28 Sep;
  -- Hassan approved it on condition that no collected trade is lost: every
  -- one is exported, read back and committed before this deletes it). The
  -- earlier note, from when the floor went to two, follows.
  --
  -- TWO (plan v2 P1.6, 28 Sep; was fourteen from 24 Sep). The readers are
  -- v_band_volume and v_city_volume, whose window is
  -- settings.volume_thresholds.lookback_hours (24), and refresh_derived,
  -- which may raise a day's stored volume but never lower it (so the day a
  -- prune cuts keeps its whole total). Nothing else reads trades_observed: not the replay (P5.12),
  -- checked 28 Sep. Since P6.2 moved the trade prints into the tick
  -- (340d1dd, 824ccd3: its first live runs, 24-25 Sep) the table has taken
  -- 23,034-56,510 prints a day (24-27 Sep) against 1,174-7,995 on 14-23 Sep,
  -- at about 615 bytes a row with its indexes (138.5 MB, 225,491
  -- rows, 28 Sep): fourteen days of that is about 390 MB (arithmetic, not
  -- measured) on a 500 MB tier the database was already 38% over. Two days
  -- is twice the window and a day of room; every print older than that is in
  -- the archive, committed and read back before this deletes it.
  --
  -- ONE, 28 Sep evening. The readers still look back 24 hours and nothing
  -- else reads the table (recompute_market_peak reads all of it, but it has
  -- not run since 1 Sep and nothing calls it), so the floor is the window
  -- itself, read from the setting the two views read: widen lookback_hours
  -- and this refuses until keep_days covers it. At 40,000-57,000 prints a
  -- day and ~615 bytes a row, the day dropped is 25-35 MB (arithmetic).
  v_lookback := coalesce(((select value from settings where key = 'volume_thresholds')
                          ->> 'lookback_hours')::int, 24);
  if p_keep_days < 1 or p_keep_days * 24 < v_lookback then
    return jsonb_build_object(
      'ok', false,
      'error', format('keep_days must be at least 1 and cover the %s h the volume views read', v_lookback),
      'lookback_hours', v_lookback
    );
  end if;

  -- NEVER A TRADE THE INGEST MAY READ AGAIN (28 Sep). ingest_trades.py reads
  -- from its mark, the `since` of its last run: the newest stored trade less
  -- an hour, or, while a cycle is unfinished, the older mark the cycle began
  -- from (on 28 Sep a starved cycle held 04:36Z for sixteen hours). A trade
  -- pruned after that mark would be fetched again, inserted as new - the
  -- dedupe index cannot see a deleted row - and archived a second time. So
  -- the cutoff may not pass the mark: a stalled ingest holds the prune back,
  -- and the archiver, told no, exports nothing for trades that night.
  select (l.detail ->> 'since')::timestamptz into v_mark
    from ingest_log l
   where l.job = 'P0.4_trade_history' and l.detail ->> 'since' is not null
   order by l.logged_at desc nulls last, l.log_id desc
   limit 1;
  if v_mark is not null and v_before > v_mark then
    return jsonb_build_object(
      'ok', false,
      'error', format('the trade ingest still reads from %s; a cutoff of %s would prune trades it may insert again', v_mark, v_before),
      'ingest_mark', v_mark,
      'older_than', v_before
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

-- A new function is executable by PUBLIC unless revoked, and this one is
-- SECURITY DEFINER and deletes rows: service_role only (plan v2 P1.1).
revoke execute on function public.prune_trades(integer, boolean, timestamptz, bigint) from public, anon, authenticated;
grant execute on function public.prune_trades(integer, boolean, timestamptz, bigint) to service_role;

comment on function public.prune_trades(integer, boolean, timestamptz, bigint) is
  'Delete trades_observed rows older than the cutoff, only when archive_daily_city_presence already covers every affected city-day and the caller''s verified archive row count matches exactly.';

-- The backstop reclaim, daily now (the same job sql/ad4_66 schedules).
do $$
begin
  if exists (select 1 from pg_namespace where nspname = 'cron') then
    perform cron.schedule('ad4_reclaim_trades_observed', '20 3 * * *',
                          'VACUUM (FULL, ANALYZE) public.trades_observed');
  end if;
end $$;
