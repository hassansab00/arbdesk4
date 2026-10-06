-- ===========================================================================
-- ad4_96_prune_band_probabilities.sql - EVERY PRICE A READER USES STAYS; THE
-- REST GOES TO THE REPOSITORY AFTER 30 DAYS (plan v2 P1.6 phase 2, step 6,
-- 29 Sep)
--
-- Safe to run any time. Creates one view and one function. Deletes nothing
-- by itself - p_dry_run defaults to true and the caller must ask twice.
--
-- Hassan, 29 Sep: "Do phase 2 as planned" and "do step 6 ten step 5".
--
-- band_probabilities is every price the engine made: 102,678 rows from
-- 3 Sep, 30 MB, about 1.2 MB a day, never pruned (measured 29 Sep). The plan
-- asked for summary tables in front of its long-history readers. Measured
-- instead: every one of them reads ONE ROW per band, city-day or edge - the
-- newest, the newest before the day, the newest by the eve, the one the fact
-- was priced at, the newest published one of the city-day, the one an edge
-- cites. Keeping exactly those rows (the "marks", as the edges prune keeps
-- the newest pricing of each band and side) serves every reader unchanged,
-- needs no new table and repoints no view. Everything else about a market
-- 30 days past its date leaves - archived first.
--
-- WHO READS IT (checked 29 Sep, code and live database; the ten views that
-- read the table directly, and through them the other thirteen):
--
--   the newest row of each band       v_latest_prob, v_prediction_ladder_bands
--                                     (the predictive page, hindsight and the
--                                     ladder MV), v_city_day_readiness,
--                                     v_city_day_execution_readiness,
--                                     databank.py
--   the newest before the local day   v_calibration_evidence (calibration.py),
--                                     v_city_hit_history_live (the hit and
--                                     miss page, mv_city_hit_history, the
--                                     edge engine)
--   the newest by 18:00 local on the  v_hit_ladders (hit_tournament.py)
--   eve
--   the row fact_band_outcome was     v_probability_reliability
--   priced at (priced_at)
--   the newest published row of the   v_trajectory_evidence (trajectory.py)
--   city-day (sigma_c, forecast_sigma_c > 0, forecast_max_c set)
--   the row an edge cites             edges.prob_id, a foreign key with no
--                                     ON DELETE; when the edges prune removes
--                                     the edge, the price leaves the next night
--   v_data_freshness                  the newest computed_at and the row count
--
-- Other readers of every row are manual: data_integrity.py and the web's
-- proprietary export (which already read only what Postgres keeps of seven
-- archived tables), and the analytics page and station_width_score.py read
-- open markets and 14 days respectively - inside the keep.
--
-- PROVEN BEFORE BUILDING (tools/p16_band_probabilities_proof.py, 29 Sep, one
-- REPEATABLE READ snapshot each): with the table replaced by what this view
-- leaves at a 7-DAY cut (46,598 of 102,678 rows gone), every one of the nine
-- views above returns the same rows, count and md5 of every row:
-- v_calibration_evidence 8,289, v_probability_reliability 10, v_latest_prob
-- 10,173, v_hit_ladders 20,713, v_trajectory_evidence 19,958,
-- v_city_hit_history_live 657, v_prediction_ladder_bands 20,779,
-- v_city_day_readiness 96, v_city_day_execution_readiness 96. The view below
-- offers exactly those 46,598 rows at that cut (0 differences either way).
-- At the real 30-day cut it offers 0 today: the first market old enough is
-- dated 3 Sep, so the first prune is on or after 3 Oct.
--
-- THE MIRROR HAS IT FIRST. scripts/mirror_to_repo.py copies this table into
-- data/mirror by computed_at, a whole UTC day a night, AFTER the prune. A row
-- computed since yesterday's UTC midnight for a market 30 days old is not in
-- the mirror yet, and the prune refuses outright while one exists.
-- ===========================================================================

create or replace view public.v_prunable_band_probabilities
with (security_invoker = true) as
with priced as (
  select bp.prob_id, bp.band_id, bp.computed_at, m.city_key, m.resolution_date,
         coalesce(bp.sigma_c > 0 and bp.forecast_sigma_c > 0 and bp.forecast_max_c is not null,
                  false) as published,
         (m.resolution_date::timestamp at time zone coalesce(c.timezone, 'UTC')) as day_starts_at,
         (((m.resolution_date - 1)::timestamp + interval '18 hours')
            at time zone coalesce(c.timezone, 'UTC')) as eve_at
    from public.band_probabilities bp
    join public.bands b on b.band_id = bp.band_id
    join public.markets m on m.market_id = b.market_id
    left join public.cities c on c.city_key = m.city_key
),
ranked as (
  select pr.*,
         -- Each reader's own order: computed_at, then prob_id, newest first.
         row_number() over (partition by pr.band_id
                            order by pr.computed_at desc, pr.prob_id desc) as newest,
         row_number() over (partition by pr.band_id, pr.computed_at < pr.day_starts_at
                            order by pr.computed_at desc, pr.prob_id desc) as newest_before_day,
         row_number() over (partition by pr.band_id, pr.computed_at <= pr.eve_at
                            order by pr.computed_at desc, pr.prob_id desc) as newest_by_eve,
         -- v_trajectory_evidence takes any row at the city-day's newest
         -- published instant (no tie-break), so every row at it stays.
         max(pr.computed_at) filter (where pr.published)
           over (partition by pr.city_key, pr.resolution_date) as city_day_published_at
    from priced pr
)
select r.resolution_date, p.*
  from public.band_probabilities p
  join ranked r on r.prob_id = p.prob_id
 where r.newest > 1
   and not (r.computed_at < r.day_starts_at and r.newest_before_day = 1)
   and not (r.computed_at <= r.eve_at and r.newest_by_eve = 1)
   and not (r.published and r.computed_at = r.city_day_published_at)
   and not exists (select 1 from public.fact_band_outcome f
                    where f.band_id = p.band_id and f.priced_at = p.computed_at)
   and not exists (select 1 from public.edges e where e.prob_id = p.prob_id);

comment on view public.v_prunable_band_probabilities is
  'band_probabilities rows no reader selects: not the newest of their band, nor the newest before the local day, nor the newest by 18:00 local on the eve, nor the row fact_band_outcome was priced at, nor the newest published row of their city-day, nor cited by an edge. With the market''s resolution_date, which the caller cuts on. What the archive exports and prune_band_probabilities deletes. Service role only (plan v2 P1.6 phase 2).';

revoke all on public.v_prunable_band_probabilities from public, anon, authenticated;
grant select on public.v_prunable_band_probabilities to service_role;


create or replace function public.prune_band_probabilities(
  p_keep_days     integer,
  p_dry_run       boolean default true,
  p_before        date    default null,
  p_expected_rows bigint  default null
)
returns jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $function$
declare
  -- A cutoff may be older than the floor, never newer (plan v2 P1.1).
  v_before date := least(coalesce(p_before, current_date - p_keep_days), current_date - p_keep_days);
  -- Tonight's mirror exports every row computed since this instant, after
  -- the prune has run.
  v_unmirrored timestamptz := (date_trunc('day', now() at time zone 'UTC') - interval '1 day') at time zone 'UTC';
  v_doomed bigint;
  v_young  bigint;
  v_rows   bigint;
  v_bands  bigint;
  v_gone   bigint;
begin
  -- EIGHTEEN DAYS (WXPredict build 2.A, group A; Hassan, 6 Oct: offload to
  -- the repository). The window is the market's resolution_date.
  -- station_width_score reads every price of the markets resolved in the last
  -- 14 days (LOOKBACK_DAYS), priced from 3 days before (PRICING_LOOKBACK_DAYS):
  -- markets dated before current_date - 18 are four days past the oldest it
  -- reads. The analytics page reads open markets; everything older is read
  -- one row at a time, and those rows are never offered.
  if p_keep_days < 18 then
    return jsonb_build_object(
      'ok', false,
      'error', 'keep_days must be at least 18 - station_width_score reads every price of the markets of the last 14 days'
    );
  end if;

  if not p_dry_run and p_expected_rows is null then
    return jsonb_build_object(
      'ok', false,
      'error', 'p_expected_rows is required for a committed prune - it is the count '
               'read back from the committed archive file'
    );
  end if;

  -- A late price for an old band must become a count mismatch, not slip past
  -- the verified file.
  if not p_dry_run then
    lock table public.band_probabilities in share row exclusive mode;
  end if;

  select count(*), count(*) filter (where computed_at >= v_unmirrored)
    into v_doomed, v_young
    from public.v_prunable_band_probabilities where resolution_date < v_before;

  -- Never a row the mirror has not had yet, whatever the window.
  if v_young > 0 then
    return jsonb_build_object(
      'ok', false,
      'error', format('%s of the %s rows for markets dated before %s were computed since %s and are '
                      'not in the repo mirror yet - nothing deleted', v_young, v_doomed, v_before, v_unmirrored),
      'would_delete', v_doomed,
      'not_yet_mirrored', v_young
    );
  end if;

  if p_expected_rows is not null and v_doomed <> p_expected_rows then
    return jsonb_build_object(
      'ok', false,
      'error', format('archive row count mismatch: verified %s rows but prune would delete %s',
                      p_expected_rows, v_doomed),
      'expected_rows', p_expected_rows,
      'would_delete', v_doomed
    );
  end if;

  if v_doomed = 0 then
    return jsonb_build_object('ok', true, 'deleted', 0,
                              'note', format('no unread prices for markets dated before %s', v_before));
  end if;

  -- The number that says the guard works: bands still priced. Every band
  -- keeps its newest row, so it cannot change.
  select count(*), count(distinct band_id) into v_rows, v_bands from public.band_probabilities;

  if p_dry_run then
    return jsonb_build_object(
      'ok', true, 'dry_run', true,
      'would_delete', v_doomed,
      'rows_now', v_rows,
      'bands_priced', v_bands,
      'older_than', v_before,
      'expected_rows', p_expected_rows,
      'note', 'call again with p_dry_run => false to actually delete'
    );
  end if;

  delete from public.band_probabilities p
   where p.prob_id in (select v.prob_id from public.v_prunable_band_probabilities v
                        where v.resolution_date < v_before);
  -- The lock holds the table, not the facts and edges that mark a row: one
  -- written between the count and the delete changes the set. Then nothing
  -- is deleted, rather than something other than the verified file.
  get diagnostics v_gone = row_count;
  if v_gone <> v_doomed then
    raise exception 'prune_band_probabilities: counted % rows but the delete took % - rolled back, nothing deleted',
      v_doomed, v_gone;
  end if;

  return jsonb_build_object(
    'ok', true,
    'deleted', v_doomed,
    'rows_now', (select count(*) from public.band_probabilities),
    'bands_priced_before', v_bands,
    'bands_priced_after', (select count(distinct band_id) from public.band_probabilities),
    'older_than', v_before,
    'expected_rows', p_expected_rows,
    'table_now', pg_size_pretty(pg_total_relation_size('public.band_probabilities')),
    'note', 'request_reclaim returns the space the same night'
  );
end;
$function$;

-- SECURITY DEFINER and deletes rows: service_role only (plan v2 P1.1).
revoke execute on function public.prune_band_probabilities(integer, boolean, date, bigint) from public, anon, authenticated;
grant execute on function public.prune_band_probabilities(integer, boolean, date, bigint) to service_role;

comment on function public.prune_band_probabilities(integer, boolean, date, bigint) is
  'Delete the band_probabilities rows v_prunable_band_probabilities offers for markets dated before the cutoff (at least 18 days back), only when the caller''s count read back from the committed archive file matches exactly and none was computed since yesterday''s UTC midnight (the repo mirror copies those after the prune). Every band keeps its newest row, so the bands priced cannot change.';
