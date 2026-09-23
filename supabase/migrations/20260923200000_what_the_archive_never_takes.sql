-- ===========================================================================
-- WHAT THE ARCHIVE NEVER TAKES (plan v2.1 P1.7, Hassan 23 Sep: "the best,
-- most efficient option that keeps our data and the platform running clean")
--
-- book_snapshots and edges are the two fastest-growing tables (22,074 and
-- 13,473 rows a day, 7-day averages to 23 Sep). The archive already commits
-- to the repo every row it prunes from them:
--
--   books  v_prunable_book_redundancy  intra-day snapshots that are neither
--                                      the band-day's closing book nor cited
--   edges  v_prunable_edge_history     every pricing but the newest per
--                                      band and side
--
-- What it never takes is the complement - the rows Postgres keeps forever.
-- Measured 23 Sep: 83,334 of 214,494 book snapshots (= 214,494 - 131,160
-- prunable, checked both ways) and 17,730 edges (one per band and side).
-- The mirror copies exactly that complement, once it is final, so between
-- the archive and the mirror the repo holds every row of both tables, each
-- once, at a fraction of mirroring everything (closing books are ~1,317 a
-- day of the 22,074).
--
-- `mirror_resolution_date` is the market's resolution date: the mirror waits
-- until it is old enough that nothing about the row can change (books: 16
-- days, past the edges' 14-day prune that un-cites a snapshot; edges: 9).
-- ===========================================================================

create or replace view public.v_mirror_book_kept as
select s.*, m.resolution_date as mirror_resolution_date
  from public.book_snapshots s
  join public.bands b   on b.band_id   = s.band_id
  join public.markets m on m.market_id = b.market_id
 where not exists (          -- the band-day's closing book: nothing later that UTC day
         select 1 from public.book_snapshots k
          where k.band_id = s.band_id
            and k.observed_at >= s.observed_at
            and k.observed_at < ((((s.observed_at at time zone 'UTC')::date + 1)::timestamp)
                                   at time zone 'UTC')
            and (k.observed_at, k.snapshot_id) > (s.observed_at, s.snapshot_id))
    or exists (select 1 from public.edges   e where e.book_snapshot_id = s.snapshot_id)
    or exists (select 1 from public.signals g where g.book_snapshot_id = s.snapshot_id);

comment on view public.v_mirror_book_kept is
  'The book snapshots the archive never prunes (closing book per band-day, or cited by an edge or signal) - the complement of v_prunable_book_redundancy, which the nightly mirror copies to the repo (plan v2.1 P1.7).';

create or replace view public.v_mirror_edge_latest as
select e.*, m.resolution_date as mirror_resolution_date
  from public.edges e
  join public.bands b   on b.band_id   = e.band_id
  join public.markets m on m.market_id = b.market_id
 where not exists (
         select 1 from public.edges n
          where n.band_id = e.band_id and n.side = e.side
            and (n.computed_at, n.edge_id) > (e.computed_at, e.edge_id));

comment on view public.v_mirror_edge_latest is
  'The newest edge per band and side - the complement of v_prunable_edge_history, which the nightly mirror copies to the repo (plan v2.1 P1.7).';

revoke all on public.v_mirror_book_kept   from public, anon, authenticated;
revoke all on public.v_mirror_edge_latest from public, anon, authenticated;
grant select on public.v_mirror_book_kept   to service_role;
grant select on public.v_mirror_edge_latest to service_role;
