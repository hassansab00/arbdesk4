-- ===========================================================================
-- EVERY PRICE A READER USES STAYS; THE REST GOES TO THE REPOSITORY AFTER
-- 30 DAYS (plan v2 P1.6 phase 2, step 6, 29 Sep)
--
-- Hassan, 29 Sep: "Do phase 2 as planned" and "do step 6 ten step 5".
--
-- 1. v_prunable_band_probabilities: the band_probabilities rows no reader
--    selects (not the newest of their band, nor the newest before the local
--    day, nor the newest by 18:00 local on the eve, nor the row
--    fact_band_outcome was priced at, nor the newest published row of their
--    city-day, nor cited by an edge), with the market's resolution_date.
--    Service role only.
-- 2. prune_band_probabilities: those rows, for markets dated before the
--    cutoff (at least 30 days back), leave Postgres only after
--    scripts/archive_observations.py has committed them to
--    data/archive/probabilities and read them back, only when the count
--    matches exactly, and never a row computed since yesterday's UTC midnight
--    (the repo mirror copies those after the prune).
-- 3. request_reclaim: band_probabilities joins the allow-list (appended, so
--    the other tables keep their slots).
-- 4. A weekly backstop reclaim, Monday 07:05 (sql/ad4_66).
--
-- The bodies are the ones in sql/ad4_96_prune_band_probabilities.sql and
-- sql/ad4_66_reclaim_archived_tables.sql. Nothing is pruned by this: the
-- first market 30 days old is dated 3 Sep, so the archive's first prune of
-- this table is on or after 3 Oct. Re-runnable.
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
  -- THIRTY DAYS. station_width_score reads 14 days of every price and the
  -- analytics page reads open markets; everything older is read one row at a
  -- time, and those rows are never offered.
  if p_keep_days < 30 then
    return jsonb_build_object(
      'ok', false,
      'error', 'keep_days must be at least 30 - station_width_score reads every price of the last 14 days'
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
  'Delete the band_probabilities rows v_prunable_band_probabilities offers for markets dated before the cutoff (at least 30 days back), only when the caller''s count read back from the committed archive file matches exactly and none was computed since yesterday''s UTC midnight (the repo mirror copies those after the prune). Every band keeps its newest row, so the bands priced cannot change.';


create or replace function public.request_reclaim(p_table text)
returns jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_allowed constant text[] := array[
    'research_captures', 'paper_resolution_evidence', 'book_snapshots', 'edges',
    'weather_observations', 'weather_forecasts', 'trades_observed', 'decisions',
    -- appended (28 Sep), so every table above keeps its two-minute slot
    'paper_book_evidence', 'weather_forecast_features', 'signals',
    -- appended (29 Sep, P1.6 phase 2)
    'weather_forecast_models', 'band_probabilities'];
  v_now   timestamptz := now();
  v_hour  int := extract(hour from (v_now at time zone 'UTC'))::int;
  v_at    timestamptz;
  v_utc   timestamp;
  v_job   text;
  v_expr  text;
begin
  if p_table is null or not (p_table = any (v_allowed)) then
    raise exception 'request_reclaim: % is not a table the archive prunes', p_table
      using errcode = '22023';
  end if;

  if v_hour < 6 then
    v_at := v_now + make_interval(mins => 2 + 2 * (array_position(v_allowed, p_table) - 1));
  else
    v_at := ((date_trunc('day', v_now at time zone 'UTC') + interval '1 day' + interval '1 hour')
             at time zone 'UTC')
            + make_interval(mins => 2 * (array_position(v_allowed, p_table) - 1));
  end if;
  v_utc := v_at at time zone 'UTC';

  v_job  := 'ad4_reclaim_after_archive_' || p_table;
  v_expr := format('%s %s %s %s *',
                   extract(minute from v_utc)::int, extract(hour  from v_utc)::int,
                   extract(day    from v_utc)::int, extract(month from v_utc)::int);

  perform cron.schedule(v_job, v_expr,
                        format('VACUUM (FULL, ANALYZE) public.%I', p_table));

  return jsonb_build_object('ok', true, 'job', v_job, 'cron', v_expr, 'fires_at', v_at);
end $$;

comment on function public.request_reclaim(text) is
  'Schedules a VACUUM FULL of one archive-pruned table in the next quiet window (straight away inside 00:00-06:00 UTC, otherwise the next 01:00 UTC), staggered two minutes a table, so the space a prune frees comes back the same night without locking pages mid-day (plan v2 P6.5).';

revoke all on function public.request_reclaim(text) from public, anon, authenticated;
grant execute on function public.request_reclaim(text) to service_role;

-- The backstop reclaim (the same job sql/ad4_66 schedules).
do $$
begin
  if exists (select 1 from pg_namespace where nspname = 'cron') then
    perform cron.schedule('ad4_reclaim_band_probabilities', '5 7 * * 1',
                          'VACUUM (FULL, ANALYZE) public.band_probabilities');
  end if;
end $$;
