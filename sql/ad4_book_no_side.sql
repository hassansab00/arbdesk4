-- THE NO SIDE OF EVERY BAND'S BOOK, OBSERVED RATHER THAN ASSUMED.
--
-- scripts/edge_engine.py has always derived the NO ladder as the complement
-- of the YES book: a NO ask at q treated as a YES bid at 1-q. That identity
-- holds only where minting and merging keep the two tokens in lockstep. The
-- NO token has its own order book, with its own resting orders and its own
-- spread, and what a desk actually pays is on THAT book rather than on the
-- mirror of the other one. Every NO edge the platform has quoted to date is
-- therefore a model of a price and not a price.
--
-- ADDITIVE ON PURPOSE. Every existing column keeps its exact meaning - the
-- YES book - so no view, function or query changes behaviour. A side-tagged
-- row would have been the tidier model and would also have silently doubled
-- every unguarded read of this table, which is not a change to make while the
-- trading loop is one run from its first fill.
--
-- ONE JSONB COLUMN RATHER THAN NINETEEN NUMERIC ONES, because of storage.
-- book_snapshots is 74 MB and the database sits at 402 MB of a 500 MB plan.
-- Mirroring the full YES column set would widen every row for data nothing
-- reads yet, and keeping the full NO ladder would roughly double this table's
-- growth. The tier depths are what the edge engine and the capacity curve
-- consume - ad4_synth_levels() already rebuilds a ladder from tiers when a
-- raw book is absent - so those are what is kept, and the ladder can be added
-- inside the same column later without another migration.
--
-- Books cannot be backfilled. Every hour without this is an hour of the NO
-- side that will never exist, which is why capture lands before the consumer.
--
-- Applied live on 2026-09-16 as migration book_snapshots_no_side_capture.
-- Re-runnable.

alter table public.book_snapshots
  add column if not exists no_best_bid numeric,
  add column if not exists no_best_ask numeric,
  add column if not exists no_book     jsonb;

comment on column public.book_snapshots.no_best_bid is
  'Best bid on the NO token''s own order book, as observed. Null means the NO book was not captured for this snapshot, never that it was empty.';
comment on column public.book_snapshots.no_best_ask is
  'Best ask on the NO token''s own order book, as observed. Null means not captured.';
comment on column public.book_snapshots.no_book is
  'Observed NO-token book: mid, spread, market_state, bid/ask level counts, and cumulative USD within 1/2/5/10/25 cents of the touch plus the whole side. Same tier semantics as the YES columns beside it (cumulative, not per-tier). Null means not captured.';

-- How much of the board has both sides, over the last day. A coverage number
-- that falls is P0.2 losing token_no on new bands, not the capture breaking.
select
  count(*)                                             as snapshots_24h,
  count(*) filter (where no_book is not null)          as with_no_side,
  round(100.0 * count(*) filter (where no_book is not null) / nullif(count(*), 0), 1)
                                                       as pct_both_sides
from public.book_snapshots
where observed_at > now() - interval '24 hours';
