-- ===========================================================================
-- A DECIDED BAND'S LAST LADDER GOES TO THE REPOSITORY (WXPredict build 2.A;
-- Hassan, 7 Oct: "proceed to the next storage cut", as long as no
-- proprietary data is lost).
--
-- prune_dead_book_detail nulls a decided book's ladder (DEAD_LOSER /
-- DEAD_WINNER) at six hours, and a trading book's once the archive holds it,
-- but never the newest snapshot of a band. When a market closes, its band's
-- newest snapshot is its last, and it keeps its ladder forever. Measured
-- 7 Oct ~15:45Z: 10,268 bands' last books are decided and still carry a
-- ladder; 8,145 of them are over three days old and hold 8.77 MiB of the
-- 18.56 MiB of row data in book_snapshots' rows over three days old. About
-- 510 a day (8,145 over 16 market dates, 19 Sep - 4 Oct).
--
-- WHO READS A CLOSED MARKET'S LAST LADDER (7 Oct: the code, the web app,
-- n8n, every live view and function body):
--   edge_engine, v_opportunities, v_opportunity_context, the Board,
--   Opportunities and Goals pages      upcoming markets only
--   recompute_capacity(_city)          every band's newest book, closed
--                                      markets too. Without these ladders it
--                                      falls back to the depth tiers on the
--                                      row: the 166 city-hours they touch
--                                      move by at most $0.00066 at 2c, 5c,
--                                      10c and full depth (measured 7 Oct)
--   the backtest (book_as_of)          a lead-0 run at 12:00 UTC reaches 147
--                                      of the 8,145 (0 at lead 1);
--                                      scripts/book_history.py puts the
--                                      ladder back from data/archive/ladders,
--                                      so the backtest reads what it read
--   book_ladder_cache                  holds each band's newest book for
--                                      three days and assumes it never
--                                      changes; a cached snapshot is never
--                                      stripped
--   v_mirror_book_kept                 the repo mirror copies the closing
--                                      books 16 days after their market; the
--                                      ladder is already in the archive by
--                                      then
--
-- 1. v_unarchived_ladders, which the nightly archive exports (dataset
--    "ladders") and mark_ladders_archived stamps, also takes a decided
--    band's last book once its market's date is two days past. In the 7 days
--    to 7 Oct no book was written two days after its market's date (the
--    latest: one day after, 03:26Z), so the last book stays the last while
--    the archive runs. The columns are unchanged.
-- 2. prune_dead_book_detail may null that ladder once it is stamped, and
--    only once book_ladder_cache no longer holds the snapshot.
--
-- Every numeric column and every row stays. Nothing is stripped by this
-- migration: the archive's next run exports and stamps, the hourly prune
-- strips. The bodies are the ones in sql/ad4_prune_dead_book_detail.sql.
-- Re-runnable.
-- ===========================================================================

create or replace view public.v_unarchived_ladders as
  select s.snapshot_id, s.band_id, s.observed_at, s.market_state, s.best_bid, s.best_ask,
         s.no_best_bid, s.no_best_ask, s.raw_book, s.no_book
    from public.book_snapshots s
   where (s.raw_book is not null or s.no_book is not null)
     and s.ladder_archived_at is null
     and (s.market_state not in ('DEAD_LOSER', 'DEAD_WINNER')
          -- A DECIDED BAND'S LAST BOOK, once its market's date is two days
          -- past (WXPredict build 2.A, 7 Oct). The newest by (observed_at,
          -- snapshot_id), so a tied pair has one last book.
          or (not exists (select 1 from public.book_snapshots k
                           where k.band_id = s.band_id
                             and (k.observed_at, k.snapshot_id) > (s.observed_at, s.snapshot_id))
              and exists (select 1
                            from public.bands b
                            join public.markets m on m.market_id = b.market_id
                           where b.band_id = s.band_id
                             and m.resolution_date < current_date - 1)));

comment on view public.v_unarchived_ladders is
  'Snapshots still holding a YES or NO ladder that the repository archive does not hold yet: tradeable ones (not DEAD_LOSER / DEAD_WINNER, plan v2 P5.13) and a decided band''s last book once its market''s date is two days past (WXPredict build 2.A). The nightly archive exports exactly these rows; mark_ladders_archived() stamps exactly these rows.';

revoke all on public.v_unarchived_ladders from public, anon, authenticated;
grant select on public.v_unarchived_ladders to service_role;


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
    -- by (observed_at, snapshot_id), as v_unarchived_ladders takes a band's
    -- last book: a tied pair has one newest, the same one in both (7 Oct)
    select distinct on (band_id) snapshot_id
      from book_snapshots
     order by band_id, observed_at desc, snapshot_id desc
  )
  update book_snapshots b
     set raw_book = null, no_book = null
   where (b.raw_book is not null or b.no_book is not null)
     -- a trading band's ladder is in the repository archive first (plan v2 P5.13)
     and (b.ladder_archived_at is not null or b.market_state in ('DEAD_LOSER', 'DEAD_WINNER'))
     and (b.snapshot_id not in (select snapshot_id from newest)
          -- a decided band's last book, once the archive holds its ladder and
          -- the three-day ladder cache no longer holds the snapshot
          -- (WXPredict build 2.A, 7 Oct)
          or (b.market_state in ('DEAD_LOSER', 'DEAD_WINNER')
              and b.ladder_archived_at is not null
              and not exists (select 1 from book_ladder_cache c where c.snapshot_id = b.snapshot_id)))
     and b.observed_at < now() - case
           when b.market_state in ('DEAD_LOSER', 'DEAD_WINNER') then p_older_than
           else p_live_older_than
         end;
  get diagnostics n = row_count;
  return n;
end;
$$;

comment on function public.prune_dead_book_detail(interval, interval) is
  'Nulls raw_book and no_book on snapshots nobody reads - six hours for a band the market has decided, forty-eight for one still trading - and a trading band''s only once its ladder is in the repository archive (ladder_archived_at, plan v2 P5.13). Never the newest snapshot of a band, except a decided band''s last book once the archive holds its ladder and book_ladder_cache no longer holds it (WXPredict build 2.A). Keeps every numeric column. Never deletes a row.';

revoke all on function public.prune_dead_book_detail(interval, interval) from public, anon, authenticated;
grant execute on function public.prune_dead_book_detail(interval, interval) to service_role;
