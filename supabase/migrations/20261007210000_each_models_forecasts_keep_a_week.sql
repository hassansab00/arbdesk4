-- ===========================================================================
-- EACH MODEL'S FORECASTS KEEP A WEEK IN THE DATABASE (WXPredict build 2.A;
-- Hassan, 7 Oct: "proceed to next storage cut").
--
-- weather_forecast_models kept 30 days, as weather_forecasts. Measured 7 Oct
-- 16:5xZ: 60,305 of its 95,059 rows are dated over 7 days back, 6.79 of its
-- 10.79 MiB of row data in an 18.91 MiB table. Every row stays readable: the
-- archive's forecast_models dataset exports it to data/archive and
-- weather_history reads it from there.
--
-- WHY IT WAITED (docs/WXPREDICT_BUILD.md 3.8): two readers took one boundary
-- for both forecast tables, the later of their oldest days.
--   the hit tournament    v_hit_forecasts served live rows from that day and
--                         frozen ones (derived_hit_forecasts) before it; a week
--                         would have frozen weather_forecasts' rows at a week
--                         too, and its previous-runs rows arrive up to 7 days
--                         after their date (1,295 rows 3-7 days late in the 30
--                         days to 7 Oct)
--   the forecast ingest   its catch-up window started there
--                         (scripts/ingest_forecasts.py, first_held_date)
-- Now each takes each table's own oldest day:
--   1. derived_hit_forecasts.source_table says which table a row came from.
--      v_hit_forecasts_live writes it from its own branch; the rows frozen
--      before take it from their model (the two tables' models are disjoint,
--      7 Oct; a model in neither list refuses the migration).
--   2. v_hit_forecasts and freeze_hit_forecasts take each row's own table's
--      oldest day. On 7 Oct both tables start on 7 Sep, so v_hit_forecasts
--      returns the same rows as before (proved after applying).
--   3. prune_forecast_models' floor: 30 -> 7.
-- The ingest's window now follows weather_forecasts, and no model row is
-- written below weather_forecast_models' own oldest day (since 26 Sep every
-- one was written within a day of its date: 51,061 rows); the archive's
-- forecast_models dataset keeps 7 (scripts/archive_observations.py).
--
-- The bodies are the ones in sql/ad4_88_hit_tournament.sql,
-- sql/ad4_97_evidence_cache.sql and sql/ad4_95_prune_forecast_models.sql.
-- Nothing is deleted by this migration; the archive's next run prunes.
-- Re-runnable.
-- ===========================================================================

-- WHICH TABLE EACH ROW CAME FROM (WXPredict build 2.A, 7 Oct). The two
-- forecast tables stop keeping the same days: weather_forecast_models keeps a
-- week, weather_forecasts 30 days. A row is live while ITS table holds the
-- day, so each row says which table that is. v_hit_forecasts_live writes it
-- from its own branch; the rows frozen before 7 Oct take it from their model,
-- the two tables' models being disjoint (7 Oct: nws, open_meteo_forecast and
-- open_meteo_best_match only in weather_forecasts, the seven per-model series
-- only in weather_forecast_models). A model in neither list refuses.
alter table public.derived_hit_forecasts add column if not exists source_table text;

do $source$
declare
  v_unknown text;
begin
  select string_agg(distinct model, ', ') into v_unknown
    from public.derived_hit_forecasts
   where source_table is null
     and model not in ('nws', 'open_meteo_forecast', 'open_meteo_best_match',
                       'open_meteo_ecmwf_ifs025', 'open_meteo_gem_seamless', 'open_meteo_gfs_seamless',
                       'open_meteo_icon_seamless', 'open_meteo_jma_seamless',
                       'open_meteo_meteofrance_seamless', 'open_meteo_ukmo_seamless');
  if v_unknown is not null then
    raise exception 'derived_hit_forecasts: model(s) % belong to no known forecast table - nothing changed', v_unknown;
  end if;
  update public.derived_hit_forecasts
     set source_table = case when model in ('nws', 'open_meteo_forecast', 'open_meteo_best_match')
                             then 'weather_forecasts' else 'weather_forecast_models' end
   where source_table is null;
  if not exists (select 1 from pg_constraint
                  where conname = 'derived_hit_forecasts_source_table'
                    and conrelid = 'public.derived_hit_forecasts'::regclass) then
    alter table public.derived_hit_forecasts
      add constraint derived_hit_forecasts_source_table
      check (source_table in ('weather_forecasts', 'weather_forecast_models'));
  end if;
end
$source$;

alter table public.derived_hit_forecasts alter column source_table set not null;

create or replace view public.v_hit_forecasts_live as
with day as (
  select distinct city_key, for_date, cutoff_at from public.v_hit_ladders
)
select d.city_key, d.for_date, 'asof'::text as lane, f.model, f.forecast_max_c, f.issued_at as known_at,
       'weather_forecasts'::text as source_table
  from day d
  cross join lateral (
    select distinct on (i.model) i.model, i.forecast_max_c, i.issued_at
      from public.v_forecast_issued i
     where i.city_key = d.city_key and i.for_date = d.for_date
       and i.issued_at <= d.cutoff_at
       and i.issued_at_source in ('provider_update_time', 'ingest_time')
       and i.forecast_max_c is not null
     order by i.model, i.issued_at desc) f
union all
select d.city_key, d.for_date, 'asof', m.model, m.forecast_max_c, m.observed_at,
       'weather_forecast_models'
  from day d
  cross join lateral (
    select distinct on (w.model) w.model, w.forecast_max_c, w.observed_at
      from public.weather_forecast_models w
     where w.city_key = d.city_key and w.for_date = d.for_date
       and w.source = 'open-meteo-models-current'
       and w.observed_at <= d.cutoff_at
     order by w.model, w.observed_at desc) m
union all
select d.city_key, d.for_date, 'research', f.model, f.forecast_max_c, f.run_at,
       'weather_forecasts'
  from day d
  join public.weather_forecasts f
    on f.city_key = d.city_key and f.for_date = d.for_date
   and f.source = 'open-meteo-previous-runs' and f.lead_days = 1
   and f.forecast_max_c is not null
union all
select d.city_key, d.for_date, 'research', w.model, w.forecast_max_c, w.run_at,
       'weather_forecast_models'
  from day d
  join public.weather_forecast_models w
    on w.city_key = d.city_key and w.for_date = d.for_date
   and w.source = 'open-meteo-previous-runs' and w.lead_days = 1;

comment on view public.v_hit_forecasts_live is
  'v_hit_forecasts computed from the forecast tables as they stand: correct for every day its source_table still holds. freeze_hit_forecasts copies it nightly; v_hit_forecasts serves it (plan v2 P1.6 phase 2; source_table, WXPredict build 2.A).';

-- Each table's oldest for_date: from it on that table's rows are live, before
-- it they are frozen (WXPredict build 2.A, 7 Oct: the tables keep different
-- days; until then this was one boundary, the later of the two). An empty
-- table serves all its rows frozen.
create or replace view public.v_hit_forecasts as
with held as (
  select (select min(for_date) from public.weather_forecasts)       as forecasts_from,
         (select min(for_date) from public.weather_forecast_models) as models_from
)
select l.city_key, l.for_date, l.lane, l.model, l.forecast_max_c, l.known_at
  from public.v_hit_forecasts_live l, held h
 where l.for_date >= coalesce(case l.source_table when 'weather_forecasts' then h.forecasts_from
                                                  else h.models_from end, 'infinity'::date)
union all
select f.city_key, f.for_date, f.lane, f.model, f.forecast_max_c, f.known_at
  from public.derived_hit_forecasts f, held h
 where f.for_date < coalesce(case f.source_table when 'weather_forecasts' then h.forecasts_from
                                                 else h.models_from end, 'infinity'::date);

comment on view public.v_hit_forecasts is
  'Per settled city-day and model: the newest forecast known by 18:00 local the evening before (lane asof), and previous-runs values at nominal lead 1 whose issue time is unverified (lane research, never promotes). Plan v2.1 P3.8. Live while the row''s own forecast table holds the day, frozen (derived_hit_forecasts) before (plan v2 P1.6 phase 2; per table, WXPredict build 2.A).';

revoke all on public.v_hit_forecasts from public, anon, authenticated;
revoke all on public.v_hit_forecasts_live from public, anon, authenticated;
grant select on public.v_hit_forecasts to service_role;
grant select on public.v_hit_forecasts_live to service_role;


create or replace function public.freeze_hit_forecasts()
returns jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $fn$
declare
  t0 timestamptz := clock_timestamp();
  v_forecasts date;
  v_models    date;
  v_removed   int;
  v_written   int;
begin
  -- Each forecast table's oldest for_date: from it on that table's live rows
  -- are whole, and they replace what was frozen from that table for those
  -- days (WXPredict build 2.A, 7 Oct: weather_forecast_models keeps a week,
  -- weather_forecasts 30 days; until then one boundary, the later of the
  -- two). A table with no row leaves its frozen rows as they are.
  select (select min(for_date) from weather_forecasts),
         (select min(for_date) from weather_forecast_models)
    into v_forecasts, v_models;
  if v_forecasts is null and v_models is null then
    return jsonb_build_object('ok', true, 'rows_written', 0,
                              'note', 'neither forecast table holds a row');
  end if;

  delete from derived_hit_forecasts
   where for_date >= coalesce(case source_table when 'weather_forecasts' then v_forecasts
                                                else v_models end, 'infinity'::date);
  get diagnostics v_removed = row_count;

  insert into derived_hit_forecasts (city_key, for_date, lane, model, forecast_max_c, known_at, frozen_at, source_table)
  select city_key, for_date, lane, model, forecast_max_c, known_at, now(), source_table
    from v_hit_forecasts_live
   where for_date >= coalesce(case source_table when 'weather_forecasts' then v_forecasts
                                                else v_models end, 'infinity'::date);
  get diagnostics v_written = row_count;

  return jsonb_build_object(
    'ok', true, 'from_forecasts', v_forecasts, 'from_models', v_models,
    'rows_replaced', v_removed, 'rows_written', v_written,
    'rows_total', (select count(*) from derived_hit_forecasts),
    'ms', round(extract(epoch from (clock_timestamp() - t0)) * 1000));
end;
$fn$;

comment on function public.freeze_hit_forecasts() is
  'Copy v_hit_forecasts_live into derived_hit_forecasts for every day its source_table still holds, replacing what was frozen from that table for those days; the days before are left as frozen (plan v2 P1.6 phase 2; per table, WXPredict build 2.A). Called once a night by common.refresh_feature_cache.';

revoke all on function public.freeze_hit_forecasts() from public, anon, authenticated;
grant execute on function public.freeze_hit_forecasts() to service_role;


create or replace function public.prune_forecast_models(
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
  -- Tonight's mirror exports every row observed since this instant, after
  -- the prune has run.
  v_unmirrored timestamptz := (date_trunc('day', now() at time zone 'UTC') - interval '1 day') at time zone 'UTC';
  v_doomed bigint;
  v_keep   bigint;
  v_young  bigint;
  v_unfrozen bigint;
begin
  -- SEVEN DAYS (WXPredict build 2.A, 7 Oct; 30 before). Station correction
  -- fits on 45 days and scores on 30 more, and below the keep it reads the
  -- archive, not this (weather_history). The hit forecasts take this table's
  -- own oldest day (v_hit_forecasts, freeze_hit_forecasts), so weather_forecasts
  -- keeps its 30 days live. Since 26 Sep every row has been written within a
  -- day of its date (51,061 rows, 7 Oct); the ingest writes nothing below the
  -- oldest day held (ingest_forecasts.models_first_held_date).
  if p_keep_days < 7 then
    return jsonb_build_object(
      'ok', false,
      'error', 'keep_days must be at least 7 - the newest week of model forecasts stays in the database'
    );
  end if;

  if not p_dry_run and p_expected_rows is null then
    return jsonb_build_object(
      'ok', false,
      'error', 'p_expected_rows is required for a committed prune - it is the count '
               'read back from the committed archive file'
    );
  end if;

  -- A late write for an old date must become a count mismatch, not slip past
  -- the verified file.
  if not p_dry_run then
    lock table public.weather_forecast_models in share row exclusive mode;
  end if;

  select count(*), count(*) filter (where observed_at >= v_unmirrored)
    into v_doomed, v_young
    from public.weather_forecast_models where for_date < v_before;

  -- Never a row the mirror has not had yet, whatever the window.
  if v_young > 0 then
    return jsonb_build_object(
      'ok', false,
      'error', format('%s of the %s rows dated before %s were observed since %s and are not in '
                      'the repo mirror yet - nothing deleted', v_young, v_doomed, v_before, v_unmirrored),
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
                              'note', format('nothing dated before %s', v_before));
  end if;

  -- Every v_hit_forecasts row for the days going is frozen (plan v2 P1.6
  -- phase 2, step 6): hit_tournament.py reads 120 days, the table keeps less.
  select count(*) into v_unfrozen from (
    select city_key, for_date, lane, model, forecast_max_c, known_at
      from public.v_hit_forecasts_live where for_date < v_before
    except
    select city_key, for_date, lane, model, forecast_max_c, known_at
      from public.derived_hit_forecasts where for_date < v_before
  ) x;

  if v_unfrozen > 0 then
    return jsonb_build_object(
      'ok', false,
      'error', format('%s v_hit_forecasts row(s) dated before %s are not in derived_hit_forecasts. Run '
                      'common.refresh_feature_cache first (it freezes them) - the hit tournament reads '
                      'them after the prune.', v_unfrozen, v_before),
      'unfrozen_rows', v_unfrozen,
      'would_delete', v_doomed
    );
  end if;

  select count(*) into v_keep
    from public.weather_forecast_models where for_date >= v_before;

  if p_dry_run then
    return jsonb_build_object(
      'ok', true, 'dry_run', true,
      'would_delete', v_doomed,
      'would_keep', v_keep,
      'older_than', v_before,
      'expected_rows', p_expected_rows,
      'note', 'call again with p_dry_run => false to actually delete'
    );
  end if;

  delete from public.weather_forecast_models where for_date < v_before;

  return jsonb_build_object(
    'ok', true,
    'deleted', v_doomed,
    'kept', v_keep,
    'older_than', v_before,
    'expected_rows', p_expected_rows,
    'table_now', pg_size_pretty(pg_total_relation_size('public.weather_forecast_models')),
    'note', 'request_reclaim returns the space the same night'
  );
end;
$function$;

-- SECURITY DEFINER and deletes rows: service_role only (plan v2 P1.1).
revoke execute on function public.prune_forecast_models(integer, boolean, date, bigint) from public, anon, authenticated;
grant execute on function public.prune_forecast_models(integer, boolean, date, bigint) to service_role;

comment on function public.prune_forecast_models(integer, boolean, date, bigint) is
  'Delete weather_forecast_models rows dated before the cutoff (at least 7 days back; 30 until 7 Oct), only when the caller''s count read back from the committed archive file matches exactly, none was observed since yesterday''s UTC midnight (the repo mirror copies those after the prune), and every v_hit_forecasts row for those days is frozen in derived_hit_forecasts.';
