-- ===========================================================================
-- A TRADE LEAVES ONLY AFTER THE MIRROR HAS IT (plan v2 P1.6 phase 1, 28 Sep)
--
-- Hassan, 28 Sep: "yes keep trades in te mrror to, proceed."
--
-- Since 20260928210000 trades keep one day. scripts/mirror_to_repo.py copies
-- trades_observed into data/mirror by ingested_at, one whole UTC day a night,
-- and runs after the prune in the same workflow. A trade ingested later than
-- it traded (the ingest looks back up to a day) could leave with the night's
-- prune before the mirror copied it: measured 28 Sep, 6,137 trades ingested
-- that day had traded before the next night's cutoff. They were in the
-- archive file, but not in the mirror.
--
-- prune_trades now deletes a trade only if it traded before the cutoff AND
-- was ingested before the midnight that opens the cutoff's UTC day - the
-- boundary the previous night's mirror reached. The count, the presence guard,
-- the kept count and the delete all use the same pair, and so does the
-- archiver's export (spec "mirrored_on"), which also refuses to run while the
-- committed mirror manifest is behind that boundary. A trade held back leaves
-- the next night. Every other guard is unchanged.
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
  -- THE MIRROR HAS IT FIRST (Hassan, 28 Sep: "yes keep trades in te mrror
  -- to"). scripts/mirror_to_repo.py copies trades by ingested_at, one whole
  -- UTC day a night, and runs AFTER the prune in the same workflow. So the
  -- mirror has every trade ingested before the midnight that opens the
  -- cutoff's own UTC day (the archiver checks its committed manifest says
  -- so), and none after. A trade leaves only if it was ingested before then:
  -- one ingested later than it traded waits a night, for the mirror. On
  -- 28 Sep 6,137 trades ingested that day had traded before the next
  -- night's cutoff and would have left with no mirror copy.
  v_mirrored timestamptz := (date_trunc('day', v_before at time zone 'UTC')) at time zone 'UTC';
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
   where traded_at < v_before and ingested_at < v_mirrored;

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
      'note', format('nothing older than %s that the mirror has (ingested before %s)', v_before, v_mirrored)
    );
  end if;

  -- THE GUARD. Every city-day losing its raw trades must already be summarised
  -- in the presence rollup, which is what survives this delete.
  select count(*) into v_uncovered
  from (
    select distinct t.city_key, (t.traded_at at time zone 'UTC')::date as d
      from public.trades_observed t
     where t.traded_at < v_before
       and t.ingested_at < v_mirrored
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
   where not (traded_at < v_before and ingested_at < v_mirrored) or traded_at is null;

  if p_dry_run then
    return jsonb_build_object(
      'ok', true,
      'dry_run', true,
      'would_delete', v_doomed,
      'would_keep', v_keep,
      'older_than', v_before,
      'ingested_before', v_mirrored,
      'presence_rows', v_presence,
      'expected_rows', p_expected_rows,
      'note', 'call again with p_dry_run => false to actually delete'
    );
  end if;

  delete from public.trades_observed
   where traded_at < v_before and ingested_at < v_mirrored;

  v_freed := pg_size_pretty(pg_total_relation_size('public.trades_observed'));

  return jsonb_build_object(
    'ok', true,
    'deleted', v_doomed,
    'kept', v_keep,
    'older_than', v_before,
    'ingested_before', v_mirrored,
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
  'Delete trades_observed rows older than the cutoff and ingested before its UTC day began (so the repo mirror already has them), only when archive_daily_city_presence already covers every affected city-day and the caller''s verified archive row count matches exactly.';
