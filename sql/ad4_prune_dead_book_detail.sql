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

create or replace function public.prune_dead_book_detail(p_older_than interval default interval '6 hours')
returns integer
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  n integer := 0;
begin
  update book_snapshots
     set raw_book = null, no_book = null
   where observed_at < now() - p_older_than
     and market_state in ('DEAD_LOSER', 'DEAD_WINNER')
     and (raw_book is not null or no_book is not null);
  get diagnostics n = row_count;
  return n;
end;
$$;

comment on function public.prune_dead_book_detail(interval) is
  'Nulls raw_book and no_book on settled-in-practice bands (DEAD_LOSER/DEAD_WINNER) older than the given age. Keeps every numeric column, including the cumulative USD tiers ad4_synth_levels() rebuilds a ladder from. Never deletes a row.';

revoke all on function public.prune_dead_book_detail(interval) from public, anon, authenticated;
grant execute on function public.prune_dead_book_detail(interval) to service_role;

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
