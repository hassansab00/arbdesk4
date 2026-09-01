-- ===========================================================================
-- AD4 VERIFY  --  run this LAST, after all 12 files, against the REAL database.
--
-- v2 - REWRITTEN AS A FUNCTION.
--
-- The previous version built its report in a scratch table across several
-- statements, and failed on the live Supabase SQL editor with
-- `relation "_ad4_verify" does not exist` - as a TEMPORARY table and again
-- as a permanent one.
--
-- This version does not rely on anything surviving between statements. All
-- the work happens inside one function body, which is a single call on a
-- single connection by construction; the scratch table is created and used
-- entirely within it. Functions are known to persist across statements in
-- this setup - sql/ad4_rpc.sql defines them and sql/ad4_rls.sql grants on
-- them a whole file later, which works.
--
-- If the two statements below somehow still get separated, the function is
-- committed and you can simply run this on its own, any time:
--
--     select * from ad4_verify();
--
-- Read-only with respect to your data: it creates one function and reads
-- catalogs. It changes no table, row, policy or setting.
--
-- WHAT TO DO WITH THE OUTPUT
-- Copy the whole result grid and paste it back. It answers, from the real
-- database rather than a test one:
--   * did the 12 files actually take, and completely (sections 1-3);
--   * is the security boundary the one we intended (section 4);
--   * is Realtime actually wired (section 5);
--   * which tables have data and which are still waiting on a job (6);
--   * and the REAL shape of the six tables this repo had to guess at (7).
-- Section 7 is the important one: it is the only way to close the last
-- data-shape assumptions in docs/schema_assumptions.md.
-- ===========================================================================

create or replace function ad4_verify()
returns table (section text, check_name text, status text, detail text)
language plpgsql
as $ad4v$
declare
  r          record;
  v_missing  text[];
  v_extra    text[];
  v_n        bigint;
  v_txt      text;
  v_ok       boolean;
begin
  -- Scratch table lives entirely inside this one function call, so it
  -- cannot be lost between statements - there are none to cross.
  drop table if exists _ad4_verify;
  create temp table _ad4_verify (
    seq      int generated always as identity,
    section  text,
    check_   text,
    status   text,
    detail   text
  );

  -- =========================================================================
  -- 1. TABLES
  -- =========================================================================
  v_missing := '{}';
  foreach v_txt in array array[
    'cities','markets','bands','book_snapshots','trades_observed',
    'band_probabilities','model_versions','strategies','deployments',
    'signals','paper_trades','ledger','backtest_runs','backtest_results',
    'backtest_trades','settings','anomalies','ingest_log',
    'weather_observations','weather_forecasts','derived_weather_peak',
    'derived_market_peak','derived_city_day_volume','derived_band_day_volume',
    'derived_forecast_skill','edges','cost_params','anomaly_rules',
    'strategy_conflicts','derived_capacity','derived_city_correlation',
    'live_weather','weather_events'
  ] loop
    if to_regclass('public.' || quote_ident(v_txt)) is null then
      v_missing := v_missing || v_txt;
    end if;
  end loop;
  insert into _ad4_verify (section, check_, status, detail) values (
    '1. SCHEMA', '33 required tables',
    case when cardinality(v_missing) = 0 then 'PASS' else 'FAIL' end,
    case when cardinality(v_missing) = 0 then 'all present'
         else cardinality(v_missing) || ' MISSING: ' || array_to_string(v_missing, ', ')
              || '  -> re-run sql/ad4_00_preflight.sql and read its NOTICE output' end);

  -- =========================================================================
  -- 2. VIEWS
  -- =========================================================================
  v_missing := '{}';
  foreach v_txt in array array[
    'v_latest_book','v_latest_prob','v_latest_edge','v_opportunities',
    'v_band_volume','v_city_volume'
  ] loop
    if not exists (select 1 from information_schema.views
                   where table_schema='public' and table_name=v_txt) then
      v_missing := v_missing || v_txt;
    end if;
  end loop;
  insert into _ad4_verify (section, check_, status, detail) values (
    '1. SCHEMA', '6 required views',
    case when cardinality(v_missing) = 0 then 'PASS' else 'FAIL' end,
    case when cardinality(v_missing) = 0 then 'all present'
         else 'MISSING: ' || array_to_string(v_missing, ', ')
              || '  -> re-run sql/ad4_phase2.sql then sql/ad4_phase2_ranking.sql' end);

  -- v_opportunities must carry the volume columns, or the whole
  -- market-volume layer is silently absent from the UI.
  v_missing := '{}';
  foreach v_txt in array array[
    'volume_usd','n_trades','last_trade_at','city_volume_usd','thin_market',
    'liquidity_factor','score_depth_only','score'
  ] loop
    if not exists (select 1 from information_schema.columns
                   where table_schema='public' and table_name='v_opportunities'
                     and column_name=v_txt) then
      v_missing := v_missing || v_txt;
    end if;
  end loop;
  insert into _ad4_verify (section, check_, status, detail) values (
    '1. SCHEMA', 'v_opportunities volume columns',
    case when cardinality(v_missing) = 0 then 'PASS' else 'FAIL' end,
    case when cardinality(v_missing) = 0 then 'all 8 present'
         else 'MISSING: ' || array_to_string(v_missing, ', ')
              || '  -> sql/ad4_phase2_ranking.sql did not run, or ran before ad4_phase2.sql' end);

  -- =========================================================================
  -- 3. FUNCTIONS
  -- =========================================================================
  v_missing := '{}';
  foreach v_txt in array array[
    'calc_recommendation','log_paper_trade','approve_signal','dismiss_signal',
    'close_position','queue_backtest','update_setting','upsert_deployment',
    'set_deployment_status','settle_markets','recompute_capacity',
    'recompute_correlation','refresh_derived','build_morning_brief',
    'build_eod_report','walk_ladder_jsonb','depth_usd','capacity_side',
    'compute_edges','evaluate_signals','recompute_regime_thresholds',
    'recompute_behavioural_clusters','log_ingest'
  ] loop
    if not exists (select 1 from pg_proc p join pg_namespace n on n.oid=p.pronamespace
                   where n.nspname='public' and p.proname=v_txt) then
      v_missing := v_missing || v_txt;
    end if;
  end loop;
  insert into _ad4_verify (section, check_, status, detail) values (
    '1. SCHEMA', '23 required RPCs',
    case when cardinality(v_missing) = 0 then 'PASS' else 'FAIL' end,
    case when cardinality(v_missing) = 0 then 'all present'
         else 'MISSING: ' || array_to_string(v_missing, ', ') end);

  -- =========================================================================
  -- 4. THE FIVE COLUMNS THAT FAILED IN PRODUCTION, plus the four gaps the
  --    preflight exercise exposed. These are the specific regressions.
  -- =========================================================================
  for r in select * from (values
      ('strategies','universe'), ('strategies','regime_filter'),
      ('strategies','capital_cap_pct'), ('strategies','max_concurrent'),
      ('strategies','extra'),
      ('paper_trades','partial_fill'), ('paper_trades','requested_shares'),
      ('backtest_trades','legs_requested'), ('backtest_trades','legs_filled'),
      ('markets','rules_text'), ('markets','rules_changed_at')
    ) as t(tbl, col)
  loop
    insert into _ad4_verify (section, check_, status, detail)
    select '2. KNOWN GAPS', r.tbl || '.' || r.col,
           case when exists (select 1 from information_schema.columns
                             where table_schema='public' and table_name=r.tbl and column_name=r.col)
                then 'PASS' else 'FAIL' end,
           coalesce((select data_type from information_schema.columns
                     where table_schema='public' and table_name=r.tbl and column_name=r.col),
                    'ABSENT -> re-run sql/ad4_00_preflight.sql');
  end loop;

  -- =========================================================================
  -- 5. SEED AND SETTINGS
  -- =========================================================================
  if to_regclass('public.strategies') is not null then
    execute 'select count(*) from strategies' into v_n;
    execute 'select count(*) from strategies where enabled = true' into v_txt;
    insert into _ad4_verify (section, check_, status, detail) values (
      '3. SEED', 'six strategies seeded',
      case when v_n = 6 then 'PASS' else 'FAIL' end,
      v_n || ' rows' || case when v_n = 6 then '' else ' (expected 6) -> re-run sql/ad4_strategies_seed.sql' end);
    insert into _ad4_verify (section, check_, status, detail) values (
      '3. SEED', 'all strategies DISABLED',
      case when v_txt::bigint = 0 then 'PASS' else 'ATTENTION' end,
      case when v_txt::bigint = 0 then 'none enabled - correct for a first run'
           else v_txt || ' ENABLED. Deliberate? Nothing should trade until you mean it.' end);
  end if;

  if to_regclass('public.settings') is not null then
    for r in select * from (values
        ('bankroll'), ('max_slippage_cents'), ('tradeability_yes'), ('tradeability_no'),
        ('risk_limits'), ('correlation_warn_threshold'), ('auto_approve'),
        ('weather_alerts'), ('volume_thresholds'), ('settlement_verified')
      ) as t(k)
    loop
      execute format('select count(*) from settings where key = %L', r.k) into v_n;
      insert into _ad4_verify (section, check_, status, detail) values (
        '3. SEED', 'settings.' || r.k,
        case when v_n > 0 then 'PASS' else 'FAIL' end,
        case when v_n > 0 then coalesce((select left(value::text, 160) from settings where key = r.k), '')
             else 'ABSENT' end);
    end loop;

    execute $q$select coalesce((select (value->>'value')::boolean from settings where key='settlement_verified'), false)$q$ into v_ok;
    insert into _ad4_verify (section, check_, status, detail) values (
      '3. SEED', 'settlement is still gated',
      case when v_ok then 'ATTENTION' else 'PASS' end,
      case when v_ok then 'settlement_verified = TRUE. Only correct if docs/settlement_verification.md has real values in it.'
           else 'settlement_verified = false - correct until the resolution-source spot-check passes' end);
  end if;

  -- =========================================================================
  -- 6. SECURITY BOUNDARY
  -- =========================================================================
  select count(*) into v_n from pg_policies where schemaname='public' and policyname='anon_read';
  insert into _ad4_verify (section, check_, status, detail) values (
    '4. SECURITY', 'anon_read policies',
    case when v_n >= 30 then 'PASS' else 'FAIL' end,
    v_n || ' policies' || case when v_n >= 30 then '' else ' (expected 30+) -> re-run sql/ad4_rls.sql' end);

  -- RLS must actually be ENABLED, not just have a policy attached.
  v_missing := '{}';
  for r in
    select c.relname
    from pg_class c join pg_namespace n on n.oid = c.relnamespace
    where n.nspname='public' and c.relkind='r' and not c.relrowsecurity
      and c.relname in (
        'cities','markets','bands','book_snapshots','band_probabilities','edges',
        'strategies','deployments','signals','paper_trades','ledger','settings',
        'anomalies','trades_observed','live_weather','weather_events',
        'backtest_runs','backtest_results','backtest_trades')
  loop
    v_missing := v_missing || r.relname;
  end loop;
  insert into _ad4_verify (section, check_, status, detail) values (
    '4. SECURITY', 'row level security enabled',
    case when cardinality(v_missing) = 0 then 'PASS' else 'FAIL' end,
    case when cardinality(v_missing) = 0 then 'enabled on every table checked'
         else 'RLS OFF on: ' || array_to_string(v_missing, ', ') || ' -> re-run sql/ad4_rls.sql' end);

  -- anon must be able to SELECT and nothing else. A write grant here would
  -- let the browser bypass every RPC.
  v_extra := '{}';
  for r in
    select distinct table_name, privilege_type
    from information_schema.role_table_grants
    where grantee = 'anon' and table_schema = 'public'
      and privilege_type in ('INSERT','UPDATE','DELETE','TRUNCATE')
  loop
    v_extra := v_extra || (r.table_name || ':' || r.privilege_type);
  end loop;
  insert into _ad4_verify (section, check_, status, detail) values (
    '4. SECURITY', 'anon has NO write grants',
    case when cardinality(v_extra) = 0 then 'PASS' else 'FAIL' end,
    case when cardinality(v_extra) = 0 then 'select only, as intended'
         else 'WRITE GRANTED: ' || array_to_string(v_extra, ', ') || ' -> revoke these' end);

  -- anon EXECUTE must be exactly the nine UI-callable RPCs, plus the six
  -- pure helpers that v_latest_book / v_band_book call from inside
  -- themselves. A view's function calls are permission-checked against the
  -- CALLING role, so without those grants `select * from v_latest_book`
  -- fails for anon. They take jsonb/numeric arguments, read no tables and
  -- write nothing, so granting them costs no privilege.
  v_extra := '{}';
  for r in
    select p.proname
    from pg_proc p join pg_namespace n on n.oid = p.pronamespace
    where n.nspname='public'
      and has_function_privilege('anon', p.oid, 'EXECUTE')
      and p.proname not in (
        'calc_recommendation','log_paper_trade','approve_signal','dismiss_signal',
        'close_position','queue_backtest','update_setting','upsert_deployment',
        'set_deployment_status',
        -- pure view helpers (sql/ad4_13_reconcile.sql)
        'ad4_num','ad4_norm_levels','ad4_raw_book_side','ad4_synth_levels',
        'depth_usd','capacity_side')
  loop
    v_extra := v_extra || r.proname;
  end loop;
  insert into _ad4_verify (section, check_, status, detail) values (
    '4. SECURITY', 'anon EXECUTE is exactly the 9 UI RPCs + view helpers',
    case when cardinality(v_extra) = 0 then 'PASS' else 'ATTENTION' end,
    case when cardinality(v_extra) = 0 then 'no extras'
         else 'also executable by anon: ' || array_to_string(v_extra, ', ')
              || '  -> settle_markets / recompute_* / build_* / ad4_verify must NOT be here' end);

  -- =========================================================================
  -- 7. REALTIME  (the thing that could not be verified without a live project)
  -- =========================================================================
  if not exists (select 1 from pg_publication where pubname='supabase_realtime') then
    insert into _ad4_verify (section, check_, status, detail) values (
      '5. REALTIME', 'supabase_realtime publication', 'FAIL',
      'publication does not exist - this is not a normal Supabase project');
  else
    for r in select * from (values ('signals'), ('weather_events')) as t(tbl) loop
      insert into _ad4_verify (section, check_, status, detail)
      select '5. REALTIME', r.tbl || ' in publication',
             case when exists (select 1 from pg_publication_tables
                               where pubname='supabase_realtime' and tablename=r.tbl)
                  then 'PASS' else 'FAIL' end,
             case when exists (select 1 from pg_publication_tables
                               where pubname='supabase_realtime' and tablename=r.tbl)
                  then 'subscribed - the UI feed will be live'
                  else 'NOT in the publication -> alter publication supabase_realtime add table '
                       || r.tbl || ';   (the UI falls back to its error state without this)' end;
    end loop;
  end if;

  -- =========================================================================
  -- 8. DATA - what has run and what has not
  -- =========================================================================
  for r in select * from (values
      ('cities',              'Phase 0 city universe'),
      ('markets',             'Phase 0 market discovery'),
      ('bands',               'Phase 0 market discovery'),
      ('book_snapshots',      'Phase 0 book poller'),
      ('trades_observed',     'Phase 0 trade ingest - THIS IS WHAT FEEDS ALL MARKET VOLUME'),
      ('weather_observations','Actions -> Observations (IEM METAR)'),
      ('weather_forecasts',   'Actions -> Forecasts (Open-Meteo Previous Runs)'),
      ('derived_forecast_skill','Actions -> Measure Forecast Skill'),
      ('band_probabilities',  'Actions -> Probability + Edge Pipeline'),
      ('edges',               'Actions -> Probability + Edge Pipeline'),
      ('signals',             'Actions -> Signal Engine (fires nothing while all strategies are disabled)'),
      ('live_weather',        'Actions -> Live Weather Monitor'),
      ('weather_events',      'Actions -> Live Weather Monitor'),
      ('derived_capacity',    'Actions -> Derived Recompute'),
      ('derived_city_day_volume','select refresh_derived();'),
      ('derived_band_day_volume','select refresh_derived();'),
      ('paper_trades',        'written once a signal is approved'),
      ('backtest_runs',       'queued from the Backtest page')
    ) as t(tbl, filled_by)
  loop
    if to_regclass('public.' || quote_ident(r.tbl)) is null then
      insert into _ad4_verify (section, check_, status, detail)
      values ('6. DATA', r.tbl, 'FAIL', 'table does not exist');
    else
      execute format('select count(*) from public.%I', r.tbl) into v_n;
      insert into _ad4_verify (section, check_, status, detail)
      values ('6. DATA', r.tbl,
              case when v_n > 0 then 'HAS DATA' else 'EMPTY' end,
              v_n || ' rows' || case when v_n = 0 then '  -> filled by: ' || r.filled_by else '' end);
    end if;
  end loop;

  -- Market volume specifically: this is the layer that could not be checked
  -- against real data anywhere.
  if to_regclass('public.v_city_volume') is not null then
    execute 'select coalesce(sum(volume_usd),0)::numeric(20,2) from v_city_volume' into v_txt;
    execute 'select count(*) from v_city_volume' into v_n;
    insert into _ad4_verify (section, check_, status, detail) values (
      '6. DATA', 'MARKET VOLUME (rolling 24h)',
      case when v_n > 0 then 'HAS DATA' else 'EMPTY' end,
      '$' || v_txt || ' across ' || v_n || ' cities' ||
      case when v_n = 0 then '  -> every band will read $0 and show as THIN. Honest, but it means the volume layer has nothing to work with. Load trades_observed, then: select refresh_derived();'
           else '' end);
  end if;

  -- =========================================================================
  -- 9. DISCOVERY - the real shape of the tables this repo had to guess at.
  --    This is the section that closes docs/schema_assumptions.md.
  -- =========================================================================
  for r in select * from (values
      ('trades_observed'), ('derived_market_peak'), ('model_versions'),
      ('book_snapshots'), ('band_probabilities'), ('strategies'),
      ('paper_trades'), ('signals'), ('ledger'), ('anomalies'), ('markets'), ('bands')
    ) as t(tbl)
  loop
    if to_regclass('public.' || quote_ident(r.tbl)) is null then
      insert into _ad4_verify (section, check_, status, detail)
      values ('7. ACTUAL SHAPE', r.tbl, 'ABSENT', '');
    else
      select string_agg(column_name || ' ' || data_type, ', ' order by ordinal_position)
        into v_txt
      from information_schema.columns
      where table_schema='public' and table_name=r.tbl;
      insert into _ad4_verify (section, check_, status, detail)
      values ('7. ACTUAL SHAPE', r.tbl, 'INFO', v_txt);
    end if;
  end loop;

  -- The book ladder. This used to probe book_snapshots.ask_levels directly
  -- and blew up with "jsonb_typeof(integer) does not exist", which is how we
  -- learned those columns are level COUNTS. Ask the adapter instead: what
  -- matters is whether a usable ladder comes out and where it came from.
  if to_regclass('public.v_band_book') is not null then
    begin
      execute $q$
        select string_agg(src || '=' || n, ', ' order by src)
        from (select ask_levels_source src, count(*) n from v_band_book group by 1) x
      $q$ into v_txt;
      execute 'select count(*) from v_band_book where jsonb_array_length(ask_levels) > 0' into v_n;
    exception when others then
      v_txt := 'could not sample: ' || sqlerrm; v_n := 0;
    end;
    insert into _ad4_verify (section, check_, status, detail) values (
      '7. ACTUAL SHAPE', 'book ladder resolves (v_band_book)',
      case when v_n > 0 then 'PASS' else 'ATTENTION' end,
      coalesce(v_txt, 'no book snapshots') || '  [' || v_n ||
      ' bands have a non-empty ask ladder. raw_book/levels_jsonb are real books; ' ||
      'synthetic_tiers is approximated from the cumulative *_usd_*c depth columns]');
  else
    insert into _ad4_verify (section, check_, status, detail) values (
      '7. ACTUAL SHAPE', 'book ladder resolves (v_band_book)', 'FAIL',
      'v_band_book does not exist - run sql/ad4_13_reconcile.sql');
  end if;

  -- Where the volume figure comes from, per band.
  if to_regclass('public.v_band_volume') is not null then
    begin
      execute $q$
        select string_agg(src || '=' || n, ', ' order by src)
        from (select volume_source src, count(*) n from v_band_volume group by 1) x
      $q$ into v_txt;
    exception when others then
      v_txt := 'v_band_volume has no volume_source column - run sql/ad4_13_reconcile.sql';
    end;
    insert into _ad4_verify (section, check_, status, detail) values (
      '7. ACTUAL SHAPE', 'volume provenance (v_band_volume)',
      case when coalesce(v_txt, '') like '%book_24h%' or coalesce(v_txt, '') like '%trades_observed%'
           then 'PASS' else 'ATTENTION' end,
      coalesce(v_txt, 'no rows'));
  end if;

  -- close_position must take whatever type paper_trades.trade_id actually is.
  if to_regclass('public.paper_trades') is not null then
    select data_type into v_txt from information_schema.columns
     where table_schema='public' and table_name='paper_trades' and column_name='trade_id';
    insert into _ad4_verify (section, check_, status, detail)
    select '7. ACTUAL SHAPE', 'close_position argument type',
           case when pg_get_function_identity_arguments(p.oid) like 'p_trade_id ' || v_txt || '%'
                then 'PASS' else 'FAIL' end,
           pg_get_function_identity_arguments(p.oid) ||
           '   [paper_trades.trade_id is ' || v_txt || ']'
    from pg_proc p join pg_namespace n on n.oid = p.pronamespace
    where n.nspname = 'public' and p.proname = 'close_position';
  end if;

  -- Same question for trades_observed: band_id is what band-level volume needs.
  if to_regclass('public.trades_observed') is not null then
    if exists (select 1 from information_schema.columns
               where table_schema='public' and table_name='trades_observed' and column_name='band_id') then
      execute 'select count(*) from trades_observed where band_id is not null' into v_n;
      insert into _ad4_verify (section, check_, status, detail) values (
        '7. ACTUAL SHAPE', 'trades_observed.band_id populated',
        case when v_n > 0 then 'PASS' else 'ATTENTION' end,
        v_n || ' rows carry a band_id' ||
        case when v_n = 0 then '  -> band-level volume stays 0 and every band reads THIN. City-level volume still works if city_key is populated.' else '' end);
    else
      insert into _ad4_verify (section, check_, status, detail) values (
        '7. ACTUAL SHAPE', 'trades_observed.band_id populated', 'ATTENTION',
        'column absent -> band-level volume cannot be computed; only city-level');
    end if;
  end if;

  return query
    select v.section, v.check_, v.status, v.detail
    from _ad4_verify v
    order by v.seq;
end
$ad4v$;

-- ===========================================================================
-- THE REPORT.  Copy the whole grid.
-- ===========================================================================
-- ad4_verify() is created by the statement above and so arrives with
-- PUBLIC EXECUTE, which is how it ended up callable on the anon key.
-- Take it back: a schema audit is not something the browser may run.
revoke execute on function ad4_verify() from public;

select * from ad4_verify();
