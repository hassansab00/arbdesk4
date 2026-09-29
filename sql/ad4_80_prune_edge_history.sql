-- ===========================================================================
-- ad4_80_prune_edge_history.sql - 121,644 SUPERSEDED PRICINGS NOBODY READS.
--
-- edges is the fourth-largest table on the desk - 136,184 rows, 38 MB, growing
-- 2.36 MB a day - and the largest one the archive has never covered.
--
-- IT IS AN APPEND-ONLY LOG, NOT A STATE TABLE. edge_engine writes one row per
-- band, per side, per intraday run, and the run happens every four hours. A
-- band that has been on the board for a fortnight therefore carries about
-- eighty rows of which exactly one is current.
--
--     136,184 rows        14,540 are the newest for their band and side
--                        121,644 have been superseded
--
-- WHO ACTUALLY READS IT, enumerated rather than assumed, because reading the
-- wrong set is what broke the resolution archive for three days. Nothing in
-- Python or the web app touches the table: scripts/edge_engine.py is the only
-- writer and there is no reader. Every consumer goes through a view:
--
--   v_latest_edge         distinct on (band_id, side) order by computed_at
--                         desc - the newest row per band and side
--   v_opportunities       built on v_latest_edge
--   ad4_40 live_edges     v_latest_edge, bounded to twelve hours
--   phase1c readiness     max(computed_at) per band, and a twelve-hour filter
--
-- Every one of those is satisfied by the newest row per band and side. The
-- backward-looking findings read fact_band_outcome and band_probabilities,
-- which freeze what the desk showed at the time and are not touched here - so
-- pruning superseded pricings cannot move a settled comparison.
--
-- THAT WAS WRONG BY 29 SEP, and the prune had been emptying two readers since
-- they were written (plan v2 P1.6 phase 3, step 3.1). Both read the market's
-- YES price from edges as it stood at a cutoff - one superseded row a band:
--
--   v_hit_ladders             the newest by 18:00 local on the eve (sql/ad4_88;
--                             hit_tournament.py reads 120 days of it)
--   v_city_hit_history_live   the newest before the local day (sql/ad4_85; the
--                             hit-and-miss page, mv_city_hit_history, and the
--                             edge engine's against-market gate, which reads
--                             30 days)
--
-- Measured 29 Sep: no settled day before 22 Sep had a head-to-head left
-- (0 of 335 city-days 13-21 Sep), and no hit ladder before 23 Sep a market
-- price. Those two rows of every band - its "marks" - are now copied into
-- derived_edge_marks (freeze_edge_marks, sql/ad4_97, nightly) once their
-- cutoff has passed, both readers take the frozen price first, and the view
-- below never offers a mark that has not been copied.
--
-- WHAT IS KEPT, therefore:
--
--   * the newest row of every band and side, whatever its age, which is what
--     v_latest_edge reads and what every page is built on
--   * each band's two marks until derived_edge_marks holds them
--   * every row inside p_keep_days at full resolution, which covers the
--     twelve-hour freshness windows many times over
--
-- AND NOTHING LEAVES WITHOUT BEING ARCHIVED FIRST. The view below is what
-- scripts/archive_observations.py exports and what this function deletes -
-- the same set, by construction, which is the contract that makes the count
-- check meaningful.
--
-- RUN ORDER: after sql/ad4_phase2.sql, which creates edges and v_latest_edge.
-- Re-runnable.
-- ===========================================================================

-- ---------------------------------------------------------------------------
-- THE MARKS (plan v2 P1.6 phase 3, step 3.1, 29 Sep). Each band's YES price at
-- the two cutoffs the record reads, on its market's city clock:
--   'eve'  the newest by 18:00 local the evening before (v_hit_ladders, <=)
--   'day'  the newest before the local day began (v_city_hit_history, <)
-- A frozen mark is never rewritten: freeze_edge_marks copies one only six
-- hours after its cutoff, because edge_engine stamps every row of a run with
-- the moment the run began. edge_id is NULL on a mark restored from
-- data/archive/edges, which does not carry it; source says where it came from.
-- ---------------------------------------------------------------------------
create table if not exists public.derived_edge_marks (
  band_id      uuid        not null,
  mark         text        not null check (mark in ('eve', 'day')),
  cutoff_at    timestamptz not null,
  computed_at  timestamptz not null,
  market_price numeric,
  edge_id      bigint,
  source       text        not null default 'edges',
  frozen_at    timestamptz not null default now(),
  primary key (band_id, mark)
);

comment on table public.derived_edge_marks is
  'Each band''s YES edge at the two cutoffs the record reads - the newest by 18:00 local on the eve (mark eve, v_hit_ladders) and the newest before the local day (mark day, v_city_hit_history) - copied by freeze_edge_marks once the cutoff is six hours past, so the edges prune can take the rest. Never rewritten (plan v2 P1.6 phase 3).';

alter table public.derived_edge_marks enable row level security;
revoke all on public.derived_edge_marks from public, anon, authenticated;
grant select, insert, update, delete on public.derived_edge_marks to service_role;

-- Every band's marks as edges holds them now. What freeze_edge_marks copies,
-- and what the view below holds back until it has.
create or replace view public.v_edge_marks_live as
select k.band_id, k.mark, k.cutoff_at, x.edge_id, x.computed_at, x.market_price
  from (
    select b.band_id, v.mark, v.cutoff_at
      from public.bands b
      join public.markets m on m.market_id = b.market_id
      left join public.cities c on c.city_key = m.city_key
      cross join lateral (values
        ('eve'::text, (((m.resolution_date - 1)::timestamp + interval '18 hours')
                        at time zone coalesce(c.timezone, 'UTC'))),
        ('day'::text, (m.resolution_date::timestamp at time zone coalesce(c.timezone, 'UTC')))
      ) v(mark, cutoff_at)
     where m.resolution_date is not null
  ) k
  cross join lateral (
    select e.edge_id, e.computed_at, e.market_price
      from public.edges e
     where e.band_id = k.band_id
       and e.side = 'YES'
       and e.computed_at <= k.cutoff_at
       and (k.mark = 'eve' or e.computed_at < k.cutoff_at)
     order by e.computed_at desc
     limit 1) x;

comment on view public.v_edge_marks_live is
  'Each band''s YES edge at its two cutoffs as edges holds it now: the newest by 18:00 local on the eve (eve) and the newest before the local day (day). freeze_edge_marks copies it; v_prunable_edge_history holds each one back until it has (plan v2 P1.6 phase 3).';

revoke all on public.v_edge_marks_live from public, anon, authenticated;
grant select on public.v_edge_marks_live to service_role;

create or replace view v_prunable_edge_history as
with ranked as (
  select e.edge_id,
         e.band_id,
         e.side,
         e.computed_at,
         -- Newest first within the band AND SIDE, because YES and NO are
         -- priced separately and v_latest_edge keeps one of each. Ranking by
         -- band alone would offer up the newest NO row of every band.
         row_number() over (partition by e.band_id, e.side
                            order by e.computed_at desc) as rn
    from public.edges e
),
-- A mark derived_edge_marks does not hold yet, at the same cutoff: the
-- readers match on it, so a frozen price for another cutoff serves nobody.
unfrozen_marks as (
  select m.edge_id
    from public.v_edge_marks_live m
   where not exists (select 1 from public.derived_edge_marks d
                      where d.band_id = m.band_id and d.mark = m.mark
                        and d.cutoff_at = m.cutoff_at)
)
select s.*
  from public.edges s
  join ranked r on r.edge_id = s.edge_id
  left join unfrozen_marks u on u.edge_id = s.edge_id
 where r.rn > 1
   and u.edge_id is null;

comment on view v_prunable_edge_history is
  'Edge rows that are not the newest pricing of their band and side, nor a mark (v_edge_marks_live) derived_edge_marks does not hold yet. v_latest_edge takes exactly one row per band and side and every page is built on it; v_hit_ladders and v_city_hit_history read the marks, frozen first. Age is applied by the caller.';

grant select on v_prunable_edge_history to service_role;


create or replace function public.prune_edge_history(
  p_keep_days     integer     default 14,
  p_dry_run       boolean     default true,
  p_before        timestamptz default null,
  p_expected_rows bigint      default null
) returns jsonb
language plpgsql
security definer
set search_path to 'public', 'pg_temp'
as $fn$
declare
  v_before timestamptz := coalesce(p_before, now() - make_interval(days => p_keep_days));
  v_doomed bigint;
  v_pairs  bigint;
begin
  -- TWO DAYS IS THE FLOOR. The longest window any live reader asks for is the
  -- twelve hours the readiness views and ad4_40 use; two days is four times
  -- that, and going under it would start deleting rows a page is still
  -- allowed to ask for.
  if p_keep_days < 2 then
    return jsonb_build_object('ok', false,
      'error', 'keep_days must be at least 2 - the readiness views read a twelve-hour window');
  end if;

  if not p_dry_run and p_expected_rows is null then
    return jsonb_build_object('ok', false,
      'error', 'p_expected_rows is required for a committed prune - it is the count '
               'verified by re-downloading the uploaded archive');
  end if;

  if not p_dry_run then
    lock table public.edges in share row exclusive mode;
  end if;

  select count(*) into v_doomed
    from public.v_prunable_edge_history where computed_at < v_before;

  if p_expected_rows is not null and v_doomed <> p_expected_rows then
    return jsonb_build_object('ok', false,
      'error', format('archive row count mismatch: verified %s rows but prune would delete %s',
                      p_expected_rows, v_doomed),
      'expected_rows', p_expected_rows, 'would_delete', v_doomed);
  end if;

  if v_doomed = 0 then
    return jsonb_build_object('ok', true, 'deleted', 0,
      'note', format('no superseded pricings older than %s', v_before));
  end if;

  -- The number that says the guard works: band-sides still priced after this
  -- runs. It must not change, because every band and side keeps its newest
  -- row - and if it ever does, a page just lost a price.
  select count(*) into v_pairs from public.v_latest_edge;

  if p_dry_run then
    return jsonb_build_object('ok', true, 'dry_run', true,
      'would_delete', v_doomed, 'rows_now', (select count(*) from public.edges),
      'band_sides_priced', v_pairs, 'older_than', v_before,
      'expected_rows', p_expected_rows,
      'note', 'call again with p_dry_run => false to actually delete');
  end if;

  perform set_config('arbdesk.archiving', 'edges', true);
  delete from public.edges e
   where e.computed_at < v_before
     and exists (select 1 from public.v_prunable_edge_history p
                  where p.edge_id = e.edge_id);
  perform set_config('arbdesk.archiving', '', true);

  return jsonb_build_object('ok', true, 'deleted', v_doomed,
    'rows_now', (select count(*) from public.edges),
    'band_sides_priced_before', v_pairs,
    'band_sides_priced_after', (select count(*) from public.v_latest_edge),
    'older_than', v_before, 'expected_rows', p_expected_rows,
    'table_now', pg_size_pretty(pg_total_relation_size('public.edges')),
    'note', 'ad4_reclaim_edges returns the space on Monday');
end;
$fn$;

comment on function public.prune_edge_history(integer, boolean, timestamptz, bigint) is
  'Remove edge rows that are not the newest pricing of their band and side, and only after scripts/archive_observations.py has uploaded them to a Release and counted them back. v_latest_edge keeps exactly one row per band and side, so the count it returns cannot change.';

revoke all on function public.prune_edge_history(integer, boolean, timestamptz, bigint) from public;
grant execute on function public.prune_edge_history(integer, boolean, timestamptz, bigint) to service_role;
