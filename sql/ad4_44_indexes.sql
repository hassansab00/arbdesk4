-- ===========================================================================
-- ad4_44_indexes.sql - STOP THE PAGES TIMING OUT.
--
-- THE SYMPTOM
-- -----------
--     canceling statement due to statement timeout  (57014)
--
-- appearing in red boxes across the platform, alongside empty containers on
-- pages whose data is right there in the database.
--
-- 57014 is not a bug in a view. It is Supabase cancelling a query that ran
-- longer than the statement_timeout it sets for the `anon` role - a few
-- seconds. Every page fires ten to fifteen queries at once, so one slow view
-- does not fail alone; it starves the others and a whole screen goes red.
--
-- WHAT WAS ACTUALLY WRONG
-- -----------------------
-- Four of the hottest tables carried NO index except a surrogate primary key
-- nobody queries by:
--
--   trades_observed   168k rows, indexed only on trade_id.    Every volume
--                     figure on the platform is
--                     `where band_id = ? and traded_at > ?` - a full scan of
--                     the table, per band, and there are thousands of bands.
--   book_snapshots    indexed only on snapshot_id. Every price the desk
--                     quotes comes from
--                     `where band_id = ? order by observed_at desc limit 1`.
--   bands             indexed only on band_id. Every market -> bands join,
--                     which is every board, every ladder, every edge.
--   markets           indexed only on market_id. Every city-day lookup.
--   band_probabilities  indexed only on prob_id, and read the same way as
--                     book_snapshots.
--
-- weather_observations and weather_forecasts were indexed properly, which is
-- why the weather pages were survivable and the market pages were not.
--
-- Confirmed with EXPLAIN before writing a line of this: `Seq Scan on
-- book_snapshots`, `Parallel Seq Scan on trades_observed`, `Seq Scan on
-- bands` - for single-row lookups.
--
-- WHY IT ONLY BIT NOW
-- -------------------
-- Sequential scans of a small table are fast. These tables grow every hour a
-- collector runs, and there is no moment at which a page "starts" timing out
-- - it gets slower until the timeout catches it. Adding the indexes late is
-- not a tuning exercise; it is the difference between a desk that works at
-- scale and one that worked in week one.
--
-- SAFE TO RUN AT ANY TIME. Every index is IF NOT EXISTS and creating one
-- changes no data. On a large table this takes a minute or two - that is the
-- index being built, not a hang.
--
-- RUN ORDER: after ad4_00_preflight.sql. Anywhere after that. Re-runnable.
-- ===========================================================================

-- --------------------------------------------------------------------------
-- 1. The market side: every one of these is read per band, thousands of times
--    per page render.
-- --------------------------------------------------------------------------
do $ad4$
declare
  v_made int := 0;
  r record;
  spec text[][] := array[
    -- table                  index name                    columns
    ['book_snapshots',        'ad4_ix_book_band_time',      '(band_id, observed_at desc)'],
    ['book_snapshots',        'ad4_ix_book_time',           '(observed_at desc)'],
    ['trades_observed',       'ad4_ix_trades_band_time',    '(band_id, traded_at desc)'],
    ['trades_observed',       'ad4_ix_trades_time',         '(traded_at desc)'],
    -- AND THE TWO ABOVE ARE ON THE WRONG COLUMN FOR THE QUERY THAT MATTERS.
    --
    -- Every volume figure on the desk comes from v_band_volume and
    -- v_city_volume, and both filter on `observed_at >= now() - lookback`,
    -- not on traded_at. traded_at is when Polymarket says the trade printed;
    -- observed_at is when this desk saw it, and it is the column the views
    -- window on. So the indexes above were maintained on every insert and
    -- used by neither, and v_opportunities kept a Seq Scan on all 170,940
    -- rows - twice, once per volume view - which is ~1.4s of its ~1.8s.
    --
    -- INCLUDE carries price, size and band_id/city_key in the index leaf, so
    -- the aggregate never touches the heap: an index-only scan of just the
    -- rows inside the lookback window. trades_observed is append-only, so the
    -- write cost is one leaf insert per trade.
    -- ON THE EXPRESSION, because that is what the query filters on.
    -- ad4_trades_ts_expr() resolves to COALESCE(traded_at, observed_at,
    -- ingested_at) on a schema that has all three, and no index on a plain
    -- COLUMN can serve a COALESCE over three of them. Measured on the real
    -- desk before this existed:
    --     Index Scan using ad4_ix_trades_band_time on trades_observed
    --       Rows Removed by Filter: 127585
    -- - every volume figure read all 127,585 trades and discarded all of
    -- them. v_city_volume went 351 ms -> 4 ms on this index alone.
    ['trades_observed',       'ad4_ix_trades_seen_expr_band',
     '((coalesce(traded_at, observed_at, ingested_at)) desc, band_id) include (price, size)'],
    ['trades_observed',       'ad4_ix_trades_seen_expr_city',
     '((coalesce(traded_at, observed_at, ingested_at)) desc, city_key) include (price, size)'],
    ['band_probabilities',    'ad4_ix_prob_band_time',      '(band_id, computed_at desc)'],
    ['bands',                 'ad4_ix_bands_market',        '(market_id)'],
    ['markets',               'ad4_ix_markets_city_date',   '(city_key, resolution_date desc)'],
    ['markets',               'ad4_ix_markets_date',        '(resolution_date desc)'],
    -- the trading record
    ['signals',               'ad4_ix_signals_band',        '(band_id)'],
    ['signals',               'ad4_ix_signals_fired',       '(fired_at desc)'],
    ['paper_trades',          'ad4_ix_trades_paper_band',   '(band_id)'],
    ['paper_trades',          'ad4_ix_trades_paper_open',   '(closed_at)'],
    ['ledger',                'ad4_ix_ledger_deployment',   '(deployment_id)'],
    ['ledger',                'ad4_ix_ledger_stage_time',   '(stage, recorded_at desc)'],
    ['edges',                 'ad4_ix_edges_band',          '(band_id)'],
    -- the weather side that was not already covered
    ['weather_events',        'ad4_ix_events_city_time',    '(city_key, detected_at desc)'],
    ['live_weather',          'ad4_ix_live_city',           '(city_key)'],
    -- the evidence tables the Data Bank and Analytics read by city-day
    ['fact_band_outcome',     'ad4_ix_fbo_city_date',       '(city_key, for_date desc)'],
    ['fact_forecast_outcome', 'ad4_ix_ffo_city_date',       '(city_key, for_date desc)'],
    ['fact_signal_outcome',   'ad4_ix_fso_city_date',       '(city_key, for_date desc)'],
    ['derived_city_day_features', 'ad4_ix_dcdf_city_date',  '(city_key, obs_date desc)'],
    ['derived_forecast_skill','ad4_ix_dfs_city_lead',       '(city_key, lead_days, computed_at desc)']
  ];
begin
  for i in 1 .. array_length(spec, 1) loop
    -- A table this database does not have is not an error: the index file
    -- runs on a partial install too, and says what it skipped.
    if to_regclass('public.' || spec[i][1]) is null then
      raise notice 'ad4_44: % does not exist - skipped', spec[i][1];
      continue;
    end if;
    -- Nor is a column this schema does not carry. Checking beats failing the
    -- whole file on one name that differs between installs.
    begin
      execute format('create index if not exists %I on public.%I %s',
                     spec[i][2], spec[i][1], spec[i][3]);
      v_made := v_made + 1;
    exception when undefined_column then
      raise notice 'ad4_44: %.% - column missing on this schema, skipped', spec[i][1], spec[i][3];
    end;
  end loop;
  raise notice 'ad4_44: % index(es) present.', v_made;
end
$ad4$;


-- --------------------------------------------------------------------------
-- 2. Tell the planner the truth.
--
--    A table that has just gained an index still carries the statistics it
--    had before, and the planner will keep choosing the sequential scan it
--    was told was cheapest. ANALYZE is the half of this people leave out.
-- --------------------------------------------------------------------------
do $ad4$
declare t text;
begin
-- --------------------------------------------------------------------------
-- THE FRESHNESS TIMESTAMPS, which nothing indexed.
--
-- v_data_freshness renders on EVERY page and asks max(ts) of every table it
-- tracks. A btree whose LEADING column is that timestamp makes each one a
-- single-row backward index scan; without one it is a full scan.
--
-- weather_forecasts carried indexes CONTAINING run_at and none LEADING with
-- it, so max(run_at) read all 329,553 rows - 1,399 ms, on every page load,
-- for one value. Built from data_freshness_spec so it covers exactly what
-- that view asks for, and only where the table is big enough to matter.
-- --------------------------------------------------------------------------
do $ad4$
declare r record; idx text; made int := 0;
begin
  if to_regclass('public.data_freshness_spec') is null then
    raise notice 'ad4_44: no data_freshness_spec yet - run sql/ad4_39_freshness.sql, then this again';
  else
    for r in
      select spec.table_name, spec.ts_column,
             coalesce((select greatest(c.reltuples, 0)::bigint from pg_class c
                        join pg_namespace n on n.oid = c.relnamespace
                       where n.nspname = 'public' and c.relname = spec.table_name), 0) as n
        from data_freshness_spec spec
       where spec.ts_column is not null and spec.ts_column <> '-'
         and to_regclass('public.' || spec.table_name) is not null
    loop
      continue when r.n < 1000;
      if exists (
        select 1 from pg_index i
        join pg_class ic on ic.oid = i.indexrelid
        join pg_class tc on tc.oid = i.indrelid
        join pg_namespace n on n.oid = tc.relnamespace
        join pg_attribute a on a.attrelid = tc.oid and a.attnum = i.indkey[0]
        where n.nspname = 'public' and tc.relname = r.table_name
          and a.attname = r.ts_column
      ) then continue; end if;
      idx := format('ad4_ix_fresh_%s', r.table_name);
      begin
        execute format('create index if not exists %I on public.%I (%I desc nulls last)',
                       idx, r.table_name, r.ts_column);
        made := made + 1;
        raise notice 'ad4_44: % on %(%) - max() was scanning % rows',
                     idx, r.table_name, r.ts_column, r.n;
      exception when others then
        raise notice 'ad4_44: could not index %.% (%)', r.table_name, r.ts_column, sqlerrm;
      end;
    end loop;
    raise notice 'ad4_44: % freshness index(es) created', made;
  end if;
end
$ad4$;

  foreach t in array array['book_snapshots','trades_observed','band_probabilities','bands',
                           'markets','signals','paper_trades','ledger','edges','weather_events',
                           'live_weather','weather_observations','weather_forecasts',
                           'fact_band_outcome','fact_forecast_outcome','fact_signal_outcome',
                           'derived_city_day_features','derived_forecast_skill'] loop
    if to_regclass('public.' || t) is not null then
      execute format('analyze public.%I', t);
    end if;
  end loop;
  raise notice 'ad4_44: statistics refreshed.';
end
$ad4$;


-- --------------------------------------------------------------------------
-- 3. What is still unindexed, so this file can be checked rather than trusted.
-- --------------------------------------------------------------------------
create or replace view v_table_scan_risk as
select
  c.relname                                          as table_name,
  c.reltuples::bigint                                as est_rows,
  pg_size_pretty(pg_total_relation_size(c.oid))      as size,
  count(x.indexrelid)::int                           as n_indexes,
  string_agg(i.relname, ', ' order by i.relname)     as indexes,
  case
    when c.reltuples > 50000 and count(x.indexrelid) <= 1
      then 'AT RISK: a big table with nothing but its primary key. Every lookup on any other column is a full scan, and that is what a 57014 timeout is.'
    when c.reltuples > 200000
      then 'Large. Watch the query times on any page that reads it.'
    else 'ok'
  end                                                as verdict
from pg_class c
join pg_namespace n on n.oid = c.relnamespace and n.nspname = 'public'
left join pg_index x on x.indrelid = c.oid
left join pg_class i on i.oid = x.indexrelid
where c.relkind = 'r'
group by c.relname, c.reltuples, c.oid
order by c.reltuples desc;

comment on view v_table_scan_risk is
  'Every table by size with the indexes it has. A large table whose only index is its primary key is where a statement timeout comes from.';

do $ad4$
declare r text;
begin
  foreach r in array array['anon', 'authenticated', 'service_role'] loop
    if exists (select 1 from pg_roles where rolname = r) then
      execute format('grant select on v_table_scan_risk to %I', r);
    end if;
  end loop;
end
$ad4$;

-- --------------------------------------------------------------------------
-- 4. What the browser's query budget actually is.
--
--    Reported, not changed. Raising the timeout hides a slow query rather
--    than fixing one, and a page that takes eight seconds is a broken page
--    whether or not the database cancels it. This is here so the number is
--    known: everything above exists to fit inside it.
-- --------------------------------------------------------------------------
do $ad4$
declare r record; v_any boolean := false;
begin
  for r in
    select rolname,
           coalesce((select option_value from unnest(rolconfig) o(cfg),
                     lateral (select split_part(cfg, '=', 1) as option_name,
                                     split_part(cfg, '=', 2) as option_value) x
                      where x.option_name = 'statement_timeout'), 'not set (uses the server default)') as timeout
      from pg_roles
     where rolname in ('anon', 'authenticated', 'service_role')
     order by rolname
  loop
    v_any := true;
    raise notice 'ad4_44: statement_timeout for % = %', r.rolname, r.timeout;
  end loop;
  if not v_any then
    raise notice 'ad4_44: none of anon/authenticated/service_role exist here - normal outside Supabase.';
  end if;
  raise notice 'ad4_44: a query slower than the anon timeout reaches the browser as 57014 and shows as a red box. Nothing above changes that limit; it makes the queries fit inside it.';
end
$ad4$;


select table_name, est_rows, size, n_indexes, verdict
  from v_table_scan_risk where est_rows > 1000 order by est_rows desc;
