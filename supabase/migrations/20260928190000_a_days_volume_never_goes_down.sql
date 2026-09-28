-- ===========================================================================
-- A DAY'S VOLUME NEVER GOES DOWN, AND TRADES KEEP TWO DAYS (plan v2 P1.6, 28 Sep)
--
-- Hassan, 28 Sep: get the database under its 500 MB tier through the archive
-- (archive, then prune; never delete what is not archived). It was 692 MB,
-- 138% of the tier; trades_observed alone 138.5 MB.
--
-- 1. refresh_derived recounted days the archive's prune had cut part-way
--    through (it cuts at a timestamp, now() - keep_days) and overwrote the
--    whole day's volume with the part left. Against the repository's mirror
--    (data/mirror/derived_*_volume, written while those days were whole) the
--    live tables were short on exactly the five pruned-through days: 25 Aug
--    348 trades, 11 Sep 423, 12 Sep 330, 13 Sep 524, 14 Sep 634. Nothing
--    updates or deletes a trade except that prune, so a recount may now only
--    raise a stored day, never lower it. (The five days are restored from the
--    mirror separately; see PLAN_PROGRESS P1.6.)
--
-- 2. prune_trades' floor, 14 days -> 2. Its readers are the two 24-hour
--    volume views and refresh_derived; since the trade prints moved into the
--    tick (P6.2) the table takes 23,000-56,500 prints a day, and fourteen days
--    of that is about 390 MB (arithmetic). The archive exports every print,
--    uploads it, reads it back and commits it before the prune may run.
--
-- The function bodies are the ones in sql/ad4_13_reconcile.sql and
-- sql/ad4_65_prune_trades.sql (the install order builds the same). Re-runnable.
-- ===========================================================================

create or replace function public.refresh_derived() returns jsonb
language plpgsql security definer set search_path = public, pg_temp as $ad4$
-- A DAY'S VOLUME NEVER GOES DOWN (plan v2 P1.6, 28 Sep). Nothing updates or
-- deletes a trade except the archive's prune, so a day's true count and
-- volume only grow; a recount below what is stored can only mean trades were
-- pruned from that day. The prune cuts at a timestamp (now() - keep_days),
-- leaving its oldest day part-way through, and the recount overwrote that
-- whole day's volume with the part left: against the repository's mirror,
-- 25 Aug lost 348 trades and 11-14 Sep 330-634 a day. A recount may now only
-- raise a stored day (or fill one that has no count), never lower it - which
-- also holds when a trade arrives late for a day already pruned.
declare
  v_ts        text;
  v_city      boolean;
  v_city_rows int := 0;
  v_band_rows int := 0;
begin
  v_ts := ad4_trades_ts_expr('t');
  if v_ts is null then
    return jsonb_build_object('ok', false,
      'error', 'trades_observed has no recognised timestamp column');
  end if;

  select exists (select 1 from information_schema.columns
                  where table_schema = 'public' and table_name = 'trades_observed'
                    and column_name = 'city_key') into v_city;

  -- ---- city-day ----------------------------------------------------------
  if v_city then
    execute format($f$
      insert into derived_city_day_volume (city_key, trade_date, volume_usd, n_trades, computed_at)
      select t.city_key, (%1$s)::date, sum(t.price * t.size), count(*), now()
      from trades_observed t
      where t.city_key is not null and (%1$s) is not null
      group by t.city_key, (%1$s)::date
      on conflict (city_key, trade_date) do update
        set volume_usd = excluded.volume_usd,
            n_trades   = excluded.n_trades,
            computed_at = excluded.computed_at
        where derived_city_day_volume.n_trades is null
           or (excluded.n_trades >= derived_city_day_volume.n_trades
               and excluded.volume_usd >= coalesce(derived_city_day_volume.volume_usd, 0))
    $f$, v_ts);
  else
    execute format($f$
      insert into derived_city_day_volume (city_key, trade_date, volume_usd, n_trades, computed_at)
      select mk.city_key, (%1$s)::date, sum(t.price * t.size), count(*), now()
      from trades_observed t
      join bands bb   on bb.band_id = t.band_id
      join markets mk on mk.market_id = bb.market_id
      where mk.city_key is not null and (%1$s) is not null
      group by mk.city_key, (%1$s)::date
      on conflict (city_key, trade_date) do update
        set volume_usd = excluded.volume_usd,
            n_trades   = excluded.n_trades,
            computed_at = excluded.computed_at
        where derived_city_day_volume.n_trades is null
           or (excluded.n_trades >= derived_city_day_volume.n_trades
               and excluded.volume_usd >= coalesce(derived_city_day_volume.volume_usd, 0))
    $f$, v_ts);
  end if;
  get diagnostics v_city_rows = row_count;

  -- ---- band-day ----------------------------------------------------------
  execute format($f$
    insert into derived_band_day_volume (band_id, city_key, trade_date, volume_usd, n_trades, computed_at)
    select t.band_id,
           %2$s,
           (%1$s)::date,
           sum(t.price * t.size), count(*), now()
    from trades_observed t
    where t.band_id is not null and (%1$s) is not null
    group by t.band_id, (%1$s)::date
    on conflict (band_id, trade_date) do update
      set city_key   = excluded.city_key,
          volume_usd = excluded.volume_usd,
          n_trades   = excluded.n_trades,
          computed_at = excluded.computed_at
      where derived_band_day_volume.n_trades is null
         or (excluded.n_trades >= derived_band_day_volume.n_trades
             and excluded.volume_usd >= coalesce(derived_band_day_volume.volume_usd, 0))
  $f$,
    v_ts,
    case when v_city then 'max(t.city_key)'
         else '(select max(mk.city_key) from bands bb join markets mk on mk.market_id = bb.market_id where bb.band_id = t.band_id)'
    end);
  get diagnostics v_band_rows = row_count;

  return jsonb_build_object('ok', true,
                            'timestamp_column', v_ts,
                            'city_day_volume_rows', v_city_rows,
                            'band_day_volume_rows', v_band_rows);
end;
$ad4$;

revoke execute on function public.refresh_derived() from public, anon, authenticated;
grant execute on function public.refresh_derived() to service_role;


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
begin
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
  if p_keep_days < 2 then
    return jsonb_build_object(
      'ok', false,
      'error', 'keep_days must be at least 2 - the 24h volume window needs room to be wrong'
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
