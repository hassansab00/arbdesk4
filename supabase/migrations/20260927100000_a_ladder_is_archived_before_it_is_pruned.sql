-- Plan v2 P5.13: a book's ladder is archived before it is pruned.
--
-- prune_dead_book_detail() (sql/ad4_prune_dead_book_detail.sql, pg_cron
-- hourly at :23) nulls raw_book and no_book once a snapshot is 6 hours old
-- (a decided band) or 48 hours old (one still trading). It asked nobody
-- whether the ladder had been kept. Measured 27 Sep: of the LIVE snapshots
-- of 12-24 Sep, 19 still carried a ladder (7 on the 12th, 4 on the 13th, 6 on
-- the 20th, one each on the 21st and 23rd, none on the other days); the
-- replay's taker fills (P5.12) need exactly those ladders, and the books
-- archive (archive_observations "books", 7 days) always ran after the prune
-- had emptied them - the 7 archived books files hold 0 raw_book values.
--
-- Now the ladder is exported first, by the same two-phase archive as every
-- other table (scripts/archive_observations.py, dataset "ladders"): the
-- nightly run exports the snapshots whose ladder is not yet archived, commits
-- the file, re-reads it, and only then calls mark_ladders_archived(), which
-- stamps ladder_archived_at and DELETES NOTHING. The hourly prune nulls a
-- tradeable book's ladder only once it carries that stamp. Every numeric
-- column, and the row itself, stays as before. Idempotent.
--
-- A DECIDED BOOK (DEAD_LOSER / DEAD_WINNER: ask <= 2c or bid >= 96c) is left
-- to the 6-hour prune as before and not archived: about 500 of them an hour on
-- 27 Sep (1,948 ladders in four hours, 2.2 MB), which held for a day would add
-- tens of MB to a database at 132% of its tier, for a ladder that is a wall at
-- 0.001 or 0.999. The plan (P5.13) keeps the archive to what research needs;
-- their best bid, best ask and depth tiers stay in the row.

alter table public.book_snapshots add column if not exists ladder_archived_at timestamptz;

create or replace view public.v_unarchived_ladders as
  select snapshot_id, band_id, observed_at, market_state, best_bid, best_ask,
         no_best_bid, no_best_ask, raw_book, no_book
    from public.book_snapshots
   where (raw_book is not null or no_book is not null)
     and ladder_archived_at is null
     and market_state not in ('DEAD_LOSER', 'DEAD_WINNER');

comment on view public.v_unarchived_ladders is
  'Tradeable snapshots (not DEAD_LOSER / DEAD_WINNER) still holding a YES or NO ladder that the repository archive does not hold yet (plan v2 P5.13). The nightly archive exports exactly these rows; mark_ladders_archived() stamps exactly these rows.';

revoke all on public.v_unarchived_ladders from public, anon, authenticated;
grant select on public.v_unarchived_ladders to service_role;

-- The archive's prune contract (p_keep_days, p_dry_run, p_before,
-- p_expected_rows -> ok / would_delete / deleted), so the exporter treats it
-- like every other dataset. "deleted" here counts rows MARKED: no row and no
-- column value is removed by this function.
create or replace function public.mark_ladders_archived(
  p_keep_days     integer     default 1,
  p_dry_run       boolean     default true,
  p_before        timestamptz default null,
  p_expected_rows bigint      default null
) returns jsonb
language plpgsql security definer set search_path = '' as $$
declare
  v_before timestamptz := coalesce(p_before, now() - make_interval(days => p_keep_days));
  v_n bigint;
begin
  -- ONE DAY IS THE FLOOR, the archive's own window for this dataset.
  if p_keep_days < 1 then
    return jsonb_build_object('ok', false, 'error', 'keep_days must be at least 1');
  end if;
  if not p_dry_run and p_expected_rows is null then
    return jsonb_build_object('ok', false,
      'error', 'p_expected_rows is required to mark ladders archived - it is the count verified by re-reading the committed archive');
  end if;
  if not p_dry_run then
    lock table public.book_snapshots in share row exclusive mode;
  end if;
  select count(*) into v_n from public.v_unarchived_ladders where observed_at < v_before;
  if p_expected_rows is not null and v_n <> p_expected_rows then
    return jsonb_build_object('ok', false,
      'error', format('archive row count mismatch: verified %s ladders but %s are unarchived before %s', p_expected_rows, v_n, v_before),
      'expected_rows', p_expected_rows, 'would_delete', v_n);
  end if;
  if p_dry_run then
    return jsonb_build_object('ok', true, 'dry_run', true, 'would_delete', v_n, 'older_than', v_before,
      'note', 'would mark these ladders archived; nothing is deleted');
  end if;
  -- The rows the view names, so what is stamped is what was counted.
  update public.book_snapshots b
     set ladder_archived_at = now()
   where b.snapshot_id in (select u.snapshot_id from public.v_unarchived_ladders u where u.observed_at < v_before);
  return jsonb_build_object('ok', true, 'deleted', v_n, 'marked', v_n, 'older_than', v_before,
    'note', 'nothing deleted: these ladders are archived, so prune_dead_book_detail may now null them');
end $$;

revoke all on function public.mark_ladders_archived(integer, boolean, timestamptz, bigint) from public, anon, authenticated;
grant execute on function public.mark_ladders_archived(integer, boolean, timestamptz, bigint) to service_role;

-- The hourly prune, unchanged except for one condition: a tradeable book's
-- ladder must be archived first. The live body before this (27 Sep) was the
-- one in sql/ad4_prune_dead_book_detail.sql.
create or replace function public.prune_dead_book_detail(
  p_older_than      interval default interval '6 hours',
  p_live_older_than interval default interval '48 hours'
) returns integer
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  n integer := 0;
begin
  with newest as (
    select distinct on (band_id) snapshot_id
      from book_snapshots
     order by band_id, observed_at desc
  )
  update book_snapshots b
     set raw_book = null, no_book = null
   where (b.raw_book is not null or b.no_book is not null)
     and (b.ladder_archived_at is not null or b.market_state in ('DEAD_LOSER', 'DEAD_WINNER'))
     and b.snapshot_id not in (select snapshot_id from newest)
     and b.observed_at < now() - case
           when b.market_state in ('DEAD_LOSER', 'DEAD_WINNER') then p_older_than
           else p_live_older_than
         end;
  get diagnostics n = row_count;
  return n;
end;
$$;

comment on function public.prune_dead_book_detail(interval, interval) is
  'Nulls raw_book and no_book on snapshots nobody reads - six hours for a band the market has decided, forty-eight for one still trading, never the newest snapshot of any band - and a trading band''s only once its ladder is in the repository archive (ladder_archived_at, plan v2 P5.13). Keeps every numeric column. Never deletes a row.';

revoke all on function public.prune_dead_book_detail(interval, interval) from public, anon, authenticated;
grant execute on function public.prune_dead_book_detail(interval, interval) to service_role;
