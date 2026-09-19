-- A DEAD BAND'S LADDER IS NOISE, AND IT WAS FILLING THE DISK.
--
-- book_snapshots grows ~17,000 rows a day and nothing had ever pruned it.
-- Roughly 63% of those rows are DEAD_LOSER: a band whose best ask is at or
-- under two cents, i.e. one the market has already decided against. Each
-- carried ~700 bytes of raw_book and ~390 of no_book - the full both-side
-- ladder of a contract nobody will trade again.
--
-- On 16 Sep the database stood at 462 MB of a 500 MB plan, growing about
-- 30 MB a day between this table and research_captures. That is roughly one
-- day of headroom before every write starts failing, and the statement
-- timeouts already showing on the Board were the early symptom.
--
-- This drops only the LADDERS, only on bands the market has decided, and only
-- once they are six hours old - so v_latest_book, which reads the newest
-- snapshot per band, is never touched. Every numeric column survives: best
-- bid and ask, the spread, the level counts and the full cumulative USD
-- tiers. ad4_synth_levels() already rebuilds a ladder from those tiers when a
-- raw book is absent, which is exactly the path this leaves in place. The
-- record of what the band was worth, and how much depth stood behind it,
-- stays complete; only the per-level detail of a worthless contract goes.
--
-- Deliberately NOT a row delete. Books cannot be backfilled, and the row is
-- the evidence that the band existed and was dead at that hour.
--
-- Applied live 2026-09-16: 44,788 rows pruned, book_snapshots 94 MB -> 52 MB,
-- database 462 MB -> 436 MB after a VACUUM FULL. Re-runnable.

-- AND THEN THE LIVE ONES CAME BACK. Measured 2026-09-19, three days after
-- the run above:
--
--   market_state   rows      still carrying a ladder   older than 6h
--   DEAD_LOSER   115,609                       3,400               0
--   LIVE          59,915                      43,813          42,333
--   ONE_SIDED      2,308                       1,704           1,648
--   WIDE           2,176                       1,838           1,761
--
-- The dead half is working exactly as intended - 115,609 rows, 3,400 ladders,
-- nothing left to prune. The table is back to 129 MB because a LIVE band's
-- ladder is never pruned at all, and 42,333 of them are more than six hours
-- old.
--
-- A FOUR-DAY-OLD LADDER IS NOT READ BY ANYTHING. v_latest_book takes the
-- newest snapshot per band. v_band_price_history, which draws the monitor's
-- chart, reads best_bid, best_ask and mid. The edge engine prices from the
-- current book. Nothing walks a historical ladder level by level, and if
-- anything ever needs to, ad4_synth_levels() rebuilds one from the cumulative
-- USD tiers that survive here.
--
-- So the window is by STATE: six hours for a band the market has decided,
-- forty-eight for one still trading - long enough that a whole weekend of
-- intraday analysis still has its ladders, short enough that the table stops
-- growing by tens of megabytes a week.
--
-- THE NEWEST SNAPSHOT PER BAND IS NEVER TOUCHED, whatever its age. The age
-- windows almost guarantee that already on an hourly feed, but 'almost' is
-- how v_latest_book would one day return a row with no ladder on a band the
-- collector had stopped seeing.
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
  'Nulls raw_book and no_book on snapshots nobody reads: six hours for a band the market has decided, forty-eight for one still trading, and never the newest snapshot of any band. Keeps every numeric column, including the cumulative USD tiers ad4_synth_levels() rebuilds a ladder from. Never deletes a row.';

drop function if exists public.prune_dead_book_detail(interval);
revoke all on function public.prune_dead_book_detail(interval, interval) from public, anon, authenticated;
grant execute on function public.prune_dead_book_detail(interval, interval) to service_role;

create extension if not exists pg_cron;
select cron.schedule('ad4_prune_dead_book_detail', '23 * * * *',
                     $$select public.prune_dead_book_detail()$$);

-- An UPDATE leaves dead tuples; only a full vacuum returns the space to the
-- filesystem. Safe to run, takes an exclusive lock for a few seconds.
--   vacuum (full, analyze) public.book_snapshots;

-- What the table costs now, and how much of it is still ladder.
select
  count(*)                                    as rows,
  count(raw_book)                             as with_yes_ladder,
  count(no_book)                              as with_no_ladder,
  pg_size_pretty(pg_total_relation_size('public.book_snapshots')) as total
from public.book_snapshots;
