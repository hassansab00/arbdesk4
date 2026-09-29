const { PGlite } = require('@electric-sql/pglite');
const fs = require('fs');
const path = require('path');
const assert = require('node:assert/strict');

(async () => {
  const db = new PGlite();
  await db.exec(`create role anon; create role authenticated; create role service_role bypassrls;
    alter default privileges in schema public grant all on tables to anon,authenticated,service_role;
    create schema auth; grant usage on schema auth to authenticated,service_role;
    create table auth.users(id uuid primary key);
    create function auth.uid() returns uuid language sql as $$ select nullif(current_setting('request.jwt.claim.sub',true),'')::uuid $$;
    create table public.bands(band_id uuid primary key,market_id uuid,band_index int,band_label text,
      band_lo numeric,band_hi numeric,open_low boolean,open_high boolean,token_yes text,token_no text,condition_id text);
    -- The four resolution columns are live (uuid, uuid, timestamptz, text,
    -- all nullable): 20260923140000_a_market_whose_day_ended_is_closed.sql
    -- fills them from the venue's confirmed settlement.
    create table public.markets(market_id uuid primary key,closed boolean,resolution_date date,city_key text,
      event_slug text,unit text,condition_id text,last_seen_at timestamptz default now(),
      winning_band_id uuid,resolved_band_id uuid,resolution_verified_at timestamptz,resolution_source_used text);
    -- created_at/updated_at are NOT NULL in production (both default now()).
    -- A migration that stamps updated_at fails here without them, and the
    -- 2026-09-19 retirement migration does exactly that. See CLAUDE.md: the
    -- fixture has to match the live shape or the contracts pass against a
    -- database that does not exist.
    create table public.cities(city_key text primary key,display_name text,unit text,status text,
      timezone text,latitude numeric,longitude numeric,
      created_at timestamptz not null default now(),
      updated_at timestamptz not null default now());
    -- temp_c is live (numeric, nullable): 20260929100000 builds
    -- v_city_climb_profile_live over it. temp_f, source and station are live
    -- too (numeric, text, text, nullable): 20260929180000 builds
    -- v_station_day_max over them.
    create table public.weather_observations(obs_id bigint primary key,city_key text,valid_at timestamptz,
      observed_at timestamptz,temp_c numeric,temp_f numeric,source text,station text);
    -- observed_at, variables and source are live columns (timestamptz, jsonb,
    -- text): 20260923160000 builds v_forecast_issued over them.
    create table public.weather_forecasts(forecast_id bigint primary key,city_key text,model text,run_at timestamptz,
      for_date date,lead_days int default 0,forecast_max_c numeric default 0,
      observed_at timestamptz not null default now(),variables jsonb,source text not null default 'open-meteo');
    create table public.book_snapshots(snapshot_id bigint primary key,band_id uuid,observed_at timestamptz,
      market_state text default 'LIVE',tradeable boolean default true,
      -- the ladder columns, live types (27 Sep): 20260927100000 builds
      -- v_unarchived_ladders over them
      best_bid numeric,best_ask numeric,no_best_bid numeric,no_best_ask numeric,raw_book jsonb,no_book jsonb);
    -- ingested_at as live (not null, default now()): prune_trades takes only
    -- trades ingested before the cutoff's UTC day, which the mirror has.
    create table public.trades_observed(trade_id bigint primary key,city_key text,traded_at timestamptz,
      ingested_at timestamptz not null default now());
    -- signals.payload carries the decision snapshot that
    -- 20260919180000_trade_decision_lineage.sql stamps onto every trade, so
    -- the column has to exist here or that migration's backfill fails on a
    -- table shape production does not have.
    -- regime_label is created by sql/ad4_rpc.sql, which this harness never
    -- applies, and 20260920220000 stamps it onto the trade at fill. Nullable
    -- text, matching the live column. Appended last so the positional inserts
    -- below keep working.
    -- side, price_at_fire, band_id, city_key and status are on the live table
    -- and v_strategy_board reads status to count what is waiting. Appended
    -- last so the positional inserts below keep working.
    create table public.signals(signal_id bigint primary key,action text,strategy_id text,
      fired_at timestamptz,reason text,payload jsonb,regime_label text,book_snapshot_id bigint,
      band_id uuid,city_key text,side text,price_at_fire numeric,status text,
      prob_at_fire numeric);
    -- ledger is created by sql/ad4_00_preflight.sql, which this harness never
    -- applies. Column types and the two NOT NULLs match the live table.
    --
    -- ledger_trade_id_fkey IS PART OF THAT SHAPE AND WAS MISSING HERE, which
    -- is how prune_exported_paper_trades passed a contract while being unable
    -- to delete a single row in production: every paper trade has ledger rows
    -- behind it, the live foreign key has no ON DELETE action, and the delete
    -- raised 23503 every time. Without the constraint this harness modelled a
    -- database where the delete simply worked. Declared below the paper_trades
    -- table, since it references it.
    create table public.ledger(entry_id bigserial primary key,
      recorded_at timestamptz not null default now(),trade_id uuid,signal_id bigint,
      event_type text not null,payload jsonb not null,stage text,strategy_id text,
      deployment_id uuid,band_id uuid,regime_label text,forecast_version text,
      calibration_version text,cost_version text,detail jsonb);
    -- settings is created by sql/ad4_00_preflight.sql, which this harness
    -- never applies - so a migration seeding a settings row works in
    -- production and dies here with: relation "settings" does not exist.
    -- (No backticks anywhere in this block - the whole fixture is one JS
    -- template literal and a single backtick ends the string.)
    -- That is the trap CLAUDE.md describes, and it caught
    -- 20260922210000_retention_that_answers_to_the_tier.sql.
    --
    -- Both columns are NOT NULL on the live table. Declaring them nullable
    -- here would let a migration seed a null value and pass.
    create table public.settings(key text primary key, value jsonb not null,
      updated_at timestamptz not null default now());
    -- Live ACL (pg_class.relacl, 23 Sep): anon=rm, authenticated=rm. With the
    -- grant in place, what anon may read is decided by the RLS policy, which
    -- is what the operator-list contract below tests.
    grant select on public.settings to anon, authenticated;
    -- strategies is created by sql/ad4_rpc.sql. Only v_paper_desks reads it,
    -- and only to count the enabled ones, but the column types and NOT NULLs
    -- match the live table so the view is built against the real shape.
    create table public.strategies(strategy_id text primary key,name text not null,side text,
      origin text,config jsonb not null default '{}'::jsonb,conflict_class text,
      enabled boolean not null default true,created_at timestamptz not null default now(),
      universe jsonb,regime_filter jsonb,capital_cap_pct numeric,max_concurrent integer,extra jsonb);
    insert into public.strategies(strategy_id,name) values('s1','price entry');
    -- band_probabilities matches the live table's columns, types, NOT NULLs
    -- and defaults (information_schema.columns, 23 Sep). It was three columns
    -- with a uuid prob_id; live is bigint. The research-capture trigger fires
    -- on its pricing columns, so the contract needs them to exist.
    create table public.band_probabilities(prob_id bigserial primary key,band_id uuid not null,
      computed_at timestamptz not null default now(),forecast_version uuid,calibration_version uuid,
      raw_prob numeric,calibrated_prob numeric,input_forecast_run timestamptz,input_book_snapshot bigint,
      forecast_max_c numeric,bias_applied_c numeric,sigma_c numeric,lead_days integer,
      lattice_applied boolean default false,confidence numeric,regime_label text,skill_lead_days integer,
      skill_proxy boolean not null default false,skill_source text not null default 'legacy',
      pricing_eligible boolean not null default true,pricing_block_reason text,observed_floor_c numeric,
      centre_c numeric,forecast_sigma_c numeric);
    -- book_snapshot_id is a foreign key into book_snapshots in production and
    -- v_prunable_book_redundancy reads it to refuse anything an edge cites.
    -- Missing here, the view does not compile - the fixture-does-not-match-
    -- production trap CLAUDE.md describes, arriving from the other side.
    -- prob_id, too: v_prunable_band_probabilities keeps every price an edge
    -- cites, through this live foreign key (no ON DELETE, 29 Sep).
    -- market_price, too: v_hit_ladders (sql/ad4_88, applied below before the
    -- migration that rebuilds v_hit_forecasts on it) reads the eve's price.
    create table public.edges(edge_id bigint primary key,band_id uuid,computed_at timestamptz default now(),
      side text,tradeable boolean default false,book_snapshot_id bigint,
      prob_id bigint references public.band_probabilities(prob_id),market_price numeric);
    -- derived_climb_profile is created by sql/ad4_28_feature_cache.sql, which
    -- this harness never applies; v_trajectory_evidence joins it
    -- (20260929160000). The live shape (information_schema, 29 Sep).
    create table public.derived_climb_profile(city_key text not null,local_hour integer not null,
      n_days integer,typical_climb_left_c numeric,climb_left_sd_c numeric,climb_left_p10_c numeric,
      climb_left_p90_c numeric,pct_already_peaked numeric,computed_at timestamptz not null default now(),
      primary key(city_key,local_hour));
    create table public.live_weather(city_key text primary key,updated_at timestamptz,observed_at timestamptz);
    -- weather_forecast_features is created by sql/ad4_24_nws_gridpoint.sql,
    -- which this harness never applies - so a migration doing an ALTER TABLE
    -- on it works in production and dies here. (No backticks in this block:
    -- the whole fixture is a JS template literal, and one ends the string.)
    -- Declared WITHOUT the two pressure columns, at the shape production had
    -- before 20260921180000, so that migration's ALTER does real work rather
    -- than finding them already present and proving nothing. The key is the
    -- live one, three columns: v_forecast_features_export (P1.6) joins them
    -- into the archive's paging key and is unique only because they are.
    create table public.weather_forecast_features(
      city_key text not null, for_date date not null, run_at timestamptz not null,
      source text not null default 'api.weather.gov', lead_days int,
      forecast_max_c numeric, forecast_min_c numeric, apparent_max_c numeric,
      morning_temp_c numeric, morning_dewpoint_c numeric,
      dewpoint_depression_c numeric, morning_humidity numeric,
      cloud_mean numeric, cloud_max numeric, wind_mean numeric, wind_max numeric,
      precip_total numeric, precip_probability numeric,
      n_hours int, captured_at timestamptz not null default now(),
      primary key (city_key, for_date, run_at));
    create table public.ingest_log(log_id bigint primary key,job text,started_at timestamptz,finished_at timestamptz,
      status text,rows_written integer,detail jsonb,rows integer,logged_at timestamptz default now());
    -- model_versions as live (29 Sep): v_current_prediction (P4.9) reads its label.
    create table public.model_versions(version_id uuid primary key default gen_random_uuid(),
      kind text not null, label text not null, config jsonb not null default '{}'::jsonb,
      structural boolean not null default false, active boolean not null default false,
      created_at timestamptz not null default now());
    -- anomalies is created by sql/ad4_00_preflight.sql, which this harness
    -- never applies. check_paper_desk_integrity() writes to it, so the table
    -- has to stand here or the function is tested against a database shape
    -- that does not exist. Column types and the one NOT NULL match live.
    create table public.anomalies(anomaly_id bigserial primary key,
      detected_at timestamptz not null default now(),kind text not null,
      city_key text,band_id uuid,severity text,detail jsonb,side text,
      value numeric,notified boolean default false);
    -- paper_trades is created by sql/ad4_00_preflight.sql and widened by
    -- ad4_13_reconcile.sql, neither of which is a supabase/ migration - so this
    -- harness has to stand it up itself or every migration that touches it
    -- fails on a table that exists in production. Column types and the four
    -- NOT NULLs match the live table, or the contracts below would pass
    -- against a shape the database does not have.
    create table public.paper_trades(
      trade_id uuid primary key default gen_random_uuid(),
      signal_id bigint, strategy_id text, deployment_id uuid,
      band_id uuid not null, side text not null,
      opened_at timestamptz not null, shares numeric not null,
      avg_fill_price numeric not null, quoted_price numeric,
      slippage_paid numeric, fee_paid numeric, gas_paid numeric,
      partial_fill boolean not null default false, requested_shares numeric,
      closed_at timestamptz, close_price numeric, close_reason text,
      gross_pnl numeric, net_pnl numeric,
      cost_version uuid, forecast_version uuid, calibration_version uuid,
      regime_label text, max_slippage_setting numeric, fill_quality numeric,
      legs_requested integer, legs_filled integer,
      approved_by_user boolean default true, action text, exit_price numeric);
    alter table public.ledger add constraint ledger_trade_id_fkey
      foreign key (trade_id) references public.paper_trades(trade_id);
    create table public.fact_forecast_outcome(city_key text,for_date date,model text,lead_days int,
      run_at timestamptz,forecast_max_c numeric default 0,observed_max_c numeric default 0,
      error_c numeric generated always as (forecast_max_c-observed_max_c) stored,
      abs_error_c numeric generated always as (abs(forecast_max_c-observed_max_c)) stored,
      obs_source text,n_obs int,captured_at timestamptz default now(),primary key(city_key,for_date,model,lead_days));
    create table public.fact_band_outcome(band_id uuid primary key,city_key text,for_date date,
      band_lo numeric,band_hi numeric,open_low boolean,open_high boolean,model_prob numeric,
      sigma_c numeric,confidence numeric,regime_label text,forecast_max_c numeric,market_price numeric,
      edge_net_pp numeric,volume_usd numeric,depth_5c numeric,priced_at timestamptz,
      observed_max_c numeric,settled_yes boolean default false,captured_at timestamptz default now());
    -- Widened to the live shape: 20260919190000_signals_learn_from_settlement.sql
    -- builds v_signal_outcome over these columns, and a view cannot be created
    -- against a table that is missing half of them.
    create table public.fact_signal_outcome(signal_id bigint primary key,strategy_id text,
      band_id uuid,city_key text,for_date date,side text,action text,reason text,
      fired_at timestamptz,severity text,price_at_fire numeric,prob_at_fire numeric,
      edge_at_fire numeric,status text,filled boolean,fill_price numeric,shares numeric,
      settled_yes boolean,gross_pnl numeric,net_pnl numeric,slippage_c numeric,
      captured_at timestamptz default now());
    grant select on public.bands,public.markets,public.signals to service_role;
    create view public.v_synthesis_findings as select 'finding'::text as key;
    create view public.v_learning_state as select 'learning'::text as stage;
    create view public.v_forecast_convergence as select 'city'::text as city_key;`);
  await db.exec(`create view public.v_data_freshness as
    select 'fixture'::text as table_name,'health'::text as layer,'fixture'::text as plain_english,
      1::bigint as rows,false as rows_estimated,now() as newest,0::numeric as age_hours,
      1::numeric as fresh_hours,'ok'::text as state;
    create view public.v_data_health as select 0::bigint absent,0::bigint empty,0::bigint stale,1::bigint ok,
      1::bigint tracked,now() newest_write,'healthy'::text verdict;
    create view public.v_workflow_runs as select null::text job,null::text status,null::integer rows,
      null::jsonb detail,null::timestamptz logged_at,null::text trigger,null::text summary where false;`);
  const historicBand='20000000-0000-0000-0000-000000000099';
  const historicMarket='30000000-0000-0000-0000-000000000099';
  const historicTailBand='20000000-0000-0000-0000-000000000098';
  const historicTailMarket='30000000-0000-0000-0000-000000000098';
  const directory = path.resolve(__dirname,'../../supabase/migrations');
  for (const file of fs.readdirSync(directory).filter(x=>x.endsWith('.sql')).sort()) {
    if (file==='20260912230000_phase1_canonical_contracts.sql') {
      await db.exec(`insert into public.markets(market_id,closed,resolution_date,city_key,event_slug,unit)
        values('${historicMarket}',true,current_date-30,'london','historic-london-contract','F'),
              ('${historicTailMarket}',true,current_date-31,'london','historic-symbolic-tail','F');
        insert into public.bands(band_id,market_id,band_index,band_label,band_lo,band_hi,open_low,open_high,token_yes,token_no,condition_id)
        values('${historicBand}','${historicMarket}',1,'20C',20,20,false,false,'historic-yes','historic-no','historic-condition'),
              ('${historicTailBand}','${historicTailMarket}',1,'<29°F',29,29,false,false,'tail-yes','tail-no','tail-condition');`);
    }
    if (file==='20260929160000_the_evidence_outlasts_the_weather_tables.sql') {
      // v_hit_ladders is created by sql/ad4_88_hit_tournament.sql, never by a
      // migration; this one rebuilds v_hit_forecasts on it. The real view,
      // from the file, over the migrations above - not a copy.
      // It reads derived_edge_marks since 20260929220000, which creates that
      // table: the real one, from sql/ad4_80, goes in first (the migration's
      // `if not exists` then leaves it).
      const e80 = fs.readFileSync(path.resolve(__dirname,'../../sql/ad4_80_prune_edge_history.sql'),'utf8');
      const t = e80.indexOf('create table if not exists public.derived_edge_marks');
      await db.exec(e80.slice(t, e80.indexOf(');', t) + 2));
      const hit = fs.readFileSync(path.resolve(__dirname,'../../sql/ad4_88_hit_tournament.sql'),'utf8');
      const a = hit.indexOf('create or replace view public.v_hit_ladders as');
      await db.exec(hit.slice(a, hit.indexOf('comment on view public.v_hit_ladders', a)));
    }
    if (file==='20260929180000_the_station_days_and_forecast_leads_outlast_the_keep.sql') {
      // obs_primary_source() is created by sql/ad4_82_settlement_agreement.sql,
      // never by a migration; this one rebuilds v_station_day_max on it. The
      // real function, from the file - not a copy.
      const sa = fs.readFileSync(path.resolve(__dirname,'../../sql/ad4_82_settlement_agreement.sql'),'utf8');
      const a = sa.indexOf('create or replace function obs_primary_source()');
      await db.exec(sa.slice(a, sa.indexOf('$$;', a) + 3));
    }
    await db.exec(fs.readFileSync(path.join(directory,file),'utf8'));
    if (file==='20260912083705_paper_accounts_and_research_history.sql') {
      // THE LIVE SHAPE FROM THE START. Production's paper_accounts has
      // parent_account_id (sql/ad4_59_paper_desks.sql, applied below), and
      // 20260923100000's frozen-desk trigger reads it on every UPDATE. The
      // first migration to update paper_accounts after that trigger
      // (20260927210000) failed here with 'record "new" has no field
      // "parent_account_id"' while production has the column. ad4_59 adds
      // it with `add column if not exists`, so this changes nothing there.
      await db.exec('alter table public.paper_accounts add column if not exists parent_account_id uuid references public.paper_accounts(account_id)');
    }
  }
  // DESK MANAGEMENT IS ENGINE CODE AND IT LIVES IN sql/.
  //
  // submit, claim, complete, approve, settle and expire are all defined in
  // supabase/migrations, so this harness exercises them. paper_desk_create,
  // _update, _reset and _archive are defined in sql/ad4_59_paper_desks.sql,
  // which it never applied - so the half of the engine that makes, re-policies,
  // wipes and retires a desk had no contract of any kind. That is how
  // paper_desk_reset kept a `delete from` on four tables for as long as it did.
  //
  // Applying the real file rather than a copy is the point: a fixture
  // definition would test a function the database does not have, which is the
  // failure CLAUDE.md describes. Its only dependencies are the paper_* tables
  // the migrations above create, plus strategies for v_paper_desks.
  await db.exec(fs.readFileSync(
    path.resolve(__dirname,'../../sql/ad4_59_paper_desks.sql'),'utf8'));
  // ...and ad4_60, because THIS deployment runs multi-desk. The migration
  // above creates the unique index one_single_paper_desk, and ad4_60 drops it
  // and redefines paper_desk_create and create_single_paper_account to suit.
  // Production has four desks and no such index, so applying ad4_59 alone
  // would model a single-operator deployment nobody runs - the same trap as a
  // fixture that does not match the live shape, arriving from the other side.
  await db.exec(fs.readFileSync(
    path.resolve(__dirname,'../../sql/ad4_60_multiple_paper_desks.sql'),'utf8'));
  // ad4_67 prunes closed trades into the repository export, and ad4_81 is the
  // view that checks the books. They belong together: the prune is the one
  // production operation that removes a number the identities depend on, and
  // it is the reason the view cannot simply sum paper_trades. Testing either
  // without the other tests a database where the money never leaves.
  await db.exec(fs.readFileSync(
    path.resolve(__dirname,'../../sql/ad4_67_prune_exported_paper_trades.sql'),'utf8'));
  await db.exec(fs.readFileSync(
    path.resolve(__dirname,'../../sql/ad4_81_paper_desk_integrity.sql'),'utf8'));
  // ad4_79 is the archive's half of the same bargain: the one prune that
  // removes rows the BACKTEST reads. It went to production untested against
  // real Postgres and could not run there at all - see the block at the end
  // of this file.
  await db.exec(fs.readFileSync(
    path.resolve(__dirname,'../../sql/ad4_79_prune_book_redundancy.sql'),'utf8'));
  // ad4_65 is the trades prune. It was never applied here, so its PUBLIC
  // grant and its p_before floor bypass had no contract (plan v2 P1.1).
  await db.exec(fs.readFileSync(
    path.resolve(__dirname,'../../sql/ad4_65_prune_trades.sql'),'utf8'));
  // THE FIXED RAILS (plan v2 P5.9, 20260924110000). First: the migration
  // seeded exactly the plan's defaults - that is what production runs. Then
  // they are relaxed for every contract above the rails block, whose $100-$300
  // test desks place orders far above 3% of themselves to test OTHER things.
  // The rails block restores the real values and tests them on their own.
  {
    const r=(await db.query("select value from public.settings where key='risk_rails'")).rows[0].value;
    assert.deepEqual([r.daily_loss_frac,r.city_day_frac,r.cluster_day_frac,r.max_price,r.close_buffer_min],
      [0.05,0.03,0.08,0.97,15],'the migration did not seed the plan\'s default rails');
    assert.equal((await db.query("select value->>'halted' as h from public.settings where key='trading_halt'")).rows[0].h,'false');
    await db.exec(`update public.settings set value=value||'{"daily_loss_frac":1,"city_day_frac":1000,"max_price":0.999,"close_buffer_min":0}'::jsonb
      where key='risk_rails';`);
  }
  const uid='10000000-0000-0000-0000-000000000001', other='10000000-0000-0000-0000-000000000002';
  const band='20000000-0000-0000-0000-000000000001', market='30000000-0000-0000-0000-000000000001';
  const command='40000000-0000-0000-0000-000000000001';
  await db.exec(`insert into auth.users values('${uid}'),('${other}');insert into public.desk_members(user_id) values('${uid}');
    -- UTC, not Europe/London, ON PURPOSE. Every "today" this fixture checks
    -- (v_city_day_readiness, v_archive_daily, the forecast below) is UTC
    -- current_date. Under Europe/London, from 23:00Z in summer the market's
    -- local day has ended, and 20260923140000 (P2.4) rightly closes it - so
    -- the contracts would fail for one hour a day. market-state.cjs tests
    -- the local-day rule with real zones.
    insert into public.cities(city_key,display_name,unit,status,timezone,latitude,longitude)
      values('london','London','C','active','UTC',51.47,-0.45);
    insert into public.markets(market_id,closed,resolution_date,city_key,event_slug,unit)
      values('${market}',false,current_date,'london','highest-temperature-in-london','F');
    insert into public.bands(band_id,market_id,band_index,band_label,band_lo,band_hi,open_low,open_high,token_yes,token_no,condition_id)
      values('${band}','${market}',1,'20C',20,20,false,false,'yes','no','condition');
    insert into public.weather_observations(obs_id,city_key,valid_at,observed_at)
      values(1,'london',now(),now()),(2,'london',now(),now());
    insert into public.weather_forecasts(forecast_id,city_key,model,run_at,for_date)
      values(1,'london','test',now(),current_date);
    insert into public.book_snapshots(snapshot_id,band_id,observed_at,tradeable) values(1,'${band}',now(),true);
    insert into public.band_probabilities(prob_id,band_id,computed_at)
      values(60000001,'${band}',now());
    insert into public.edges(edge_id,band_id,computed_at,side,tradeable) values(1,'${band}',now(),'YES',true);
    insert into public.live_weather(city_key,updated_at,observed_at) values('london',now(),now());
    insert into public.trades_observed(trade_id,city_key,traded_at) values(1,'london',now());
    insert into public.ingest_log(log_id,job,status,rows_written,detail,logged_at)
      values(1,'P0.3_book_volume_snapshot','ok',791,
        '{"requested":1000,"failed":209,"summary":"791 written"}'::jsonb,now());`);

  const ready=(await db.query("select * from v_city_day_readiness where city_key='london' and resolution_date=current_date")).rows[0];
  assert.equal(ready.readiness_state,'ready','Complete current evidence makes a city-day ready');
  assert.equal(Number(ready.book_coverage_pct),100);
  assert.equal(Number(ready.probability_coverage_pct),100);
  const nextDay=(await db.query("select * from v_city_day_readiness where city_key='london' and resolution_date=current_date+1")).rows[0];
  assert.equal(nextDay.readiness_state,'no_market','A roster city without a contract is visible, not silently dropped');
  const normalizedRun=(await db.query("select status,rows,requested,completed,failed from v_workflow_runs where job='P0.3_book_volume_snapshot'")).rows[0];
  assert.deepEqual([normalizedRun.status,Number(normalizedRun.rows),Number(normalizedRun.requested),Number(normalizedRun.completed),Number(normalizedRun.failed)],
    ['attention',791,1000,791,209],'Partial workflow success is visible and its useful work is counted');
  await db.exec('set role anon;');
  assert.equal((await db.query("select readiness_state from v_city_day_readiness where city_key='london' and resolution_date=current_date")).rows[0].readiness_state,'ready');
  assert.equal((await db.query('select count(*)::int as n from v_operational_health')).rows[0].n,1);
  await db.exec('reset role;');

  const daily=(await db.query("select dataset,rows,cities from v_archive_daily where day=current_date order by dataset")).rows;
  assert.deepEqual(daily.map(r=>[r.dataset,Number(r.rows),Number(r.cities)]),[
    ['Forecasts',1,1],['Order books',1,1],['Station observations',2,1],['Trades seen',1,1]
  ],'Data Bank daily chart reads the maintained rollup');
  const archiveCity=(await db.query("select * from v_archive_by_city where city_key='london'")).rows[0];
  assert.deepEqual(
    [Number(archiveCity.observations),Number(archiveCity.forecasts),Number(archiveCity.book_snapshots)],
    [2,1,1],
    'Per-city Data Bank reads maintained counters instead of scanning the archive'
  );
  assert.equal((await db.query("select capture_state from v_book_target_health where band_id=$1",[band])).rows[0].capture_state,'captured');
  assert.equal((await db.query("select execution_state from v_city_day_execution_readiness where city_key='london' and resolution_date=current_date")).rows[0].execution_state,'ready');
  assert.equal((await db.query("select coordinate_state from v_city_metadata_verification where city_key='london'")).rows[0].coordinate_state,'legacy_present');
  await db.exec('set role service_role;');
  await db.query(`insert into book_capture_attempts(execution_id,band_id,token_id,status)
    values('test-run',$1,'yes','written')`,[band]);
  await assert.rejects(db.query("update book_capture_attempts set status='http_error'"),/permission denied|Append-only/);
  await db.exec('reset role;');
  assert.ok(Number((await db.query('select refresh_data_quality_flags() as n')).rows[0].n)>=3);
  assert.equal(Number((await db.query('select refresh_data_quality_flags() as n')).rows[0].n),0,
    'Quality detection is idempotent for one detector version');
  assert.equal((await db.query("select count(*)::int as n from proprietary_data_quality_flags where issue_code='zero_width_non_tail_band'")).rows[0].n,3);

  // Canonical contract corrections are additive: the collected source row
  // stays byte-for-byte unchanged while service-side consumers see the fix.
  assert.equal(Number((await db.query('select band_hi from bands where band_id=$1',[historicBand])).rows[0].band_hi),20,
    'Canonical correction never rewrites source band evidence');
  assert.equal((await db.query('select unit from markets where market_id=$1',[historicMarket])).rows[0].unit,'F',
    'Canonical correction never rewrites source market evidence');
  await db.exec('set role service_role;');
  const canonicalBand=(await db.query('select band_lo,band_hi,label_unit from v_canonical_bands where band_id=$1',[historicBand])).rows[0];
  assert.deepEqual([Number(canonicalBand.band_lo),Number(canonicalBand.band_hi),canonicalBand.label_unit],[20,21,'C']);
  assert.equal((await db.query('select unit from v_canonical_markets where market_id=$1',[historicMarket])).rows[0].unit,'C');
  const canonicalTail=(await db.query('select band_lo,band_hi,open_low,open_high,label_unit from v_canonical_bands where band_id=$1',[historicTailBand])).rows[0];
  assert.equal(canonicalTail.band_lo,null);
  assert.deepEqual([Number(canonicalTail.band_hi),canonicalTail.open_low,canonicalTail.open_high,canonicalTail.label_unit],[29,true,false,'F']);
  await db.exec('reset role;');
  // THE BROWSER READS THE CANONICAL BUCKET ONLY THROUGH A VIEW IT IS GIVEN
  // (20260923150000). An owner-rights view built on the canonical views must
  // serve anon - on 23 Sep three did not, while they were security_invoker -
  // and anon must still be refused the canonical views and the corrections.
  await db.exec(`create view public.contract_anon_bucket as
      select b.band_id, b.band_hi, m.unit from public.v_canonical_bands b
      join public.v_canonical_markets m on m.market_id = b.market_id;
    grant select on public.contract_anon_bucket to anon;`);
  await db.exec('set role anon;');
  const anonBucket=(await db.query('select band_hi,unit from public.contract_anon_bucket where band_id=$1',[historicBand])).rows[0];
  assert.deepEqual([Number(anonBucket.band_hi),anonBucket.unit],[21,'C'],
    'an owner-rights view the browser reads could not read the canonical bucket');
  for (const rel of ['v_canonical_bands','v_canonical_markets','proprietary_data_corrections']) {
    await assert.rejects(db.query(`select 1 from public.${rel} limit 1`), /permission denied/,
      `anon can read ${rel} directly`);
  }
  await db.exec('reset role;drop view public.contract_anon_bucket;');

  await db.exec(`set role authenticated;set request.jwt.claim.sub='${uid}';`);
  // ======================================================================
  // THE BOOKS HAVE TO BALANCE, AFTER EVERY OPERATION, ON ANY DESK.
  //
  // A desk is configuration: created, re-policied, paused, archived, reset,
  // replaced. Its P&L is data. What has to be true is the ENGINE - that for
  // ANY desk, under any policy, after any sequence of operations, the money
  // still adds up. Everything asserted below this line is about one desk's
  // outcome; this is about the mechanism, and it is the only thing here that
  // stays true when every desk in the database is thrown away and remade.
  //
  // FOUR IDENTITIES, none of which anything checked before:
  //
  //   1  cash is the sum of its own activity rows. Cash must never move
  //      except through a logged event, or the ledger stops being the record
  //      and becomes a commentary on it.
  //   2  reserved_cash is exactly what live orders have claimed. A reserve
  //      that outlives its order silently shrinks the desk; one that dies
  //      early lets the same dollar be spent twice.
  //   3  cash = starting_cash - open cost basis + realised P&L (including the
  //      P&L of trades the daily export has archived to the repository and
  //      deleted) + every reset - the cost basis those resets wrote off.
  //      Submitting an order does NOT move cash - it raises the reserve - so
  //      this holds at every instant, not just at rest. The reset term is not
  //      a let-off: a reset deliberately re-bases cash to starting_cash while
  //      the trades that earned the P&L stay on the books, and it records the
  //      difference as an account_reset activity row. Carrying that row in the
  //      identity is what makes the re-basing VISIBLE rather than an exception
  //      the check quietly skips - and it is how this assertion caught its own
  //      first draft the moment paper_desk_reset came under test.
  //   4  nothing is negative, and available cash (cash - reserved) never goes
  //      below zero. That last one is the over-commitment check: the engine
  //      refuses an order whose ceiling exceeds cash - reserved, and this is
  //      what proves the refusal is not bypassable by another route.
  // ======================================================================
  async function books(who, where) {
    const r = (await db.query(`
      select a.starting_cash, a.cash, a.reserved_cash,
             coalesce((select sum(cash_delta) from public.paper_activity
                        where account_id=a.account_id),0)                    as ledger,
             coalesce((select sum(cash_ceiling) from public.paper_orders
                        where account_id=a.account_id
                          and status in ('queued','working')),0)             as live_ceilings,
             coalesce((select sum(cost_basis) from public.paper_positions
                        where account_id=a.account_id),0)                    as basis,
             coalesce((select min(shares) from public.paper_positions
                        where account_id=a.account_id),0)                    as thinnest,
             coalesce((select sum(net_pnl) from public.paper_trades
                        where account_id=a.account_id and closed_at is not null),0) as realized,
             coalesce((select sum(cash_delta) from public.paper_activity
                        where account_id=a.account_id
                          and event_type='account_reset'),0)                    as rebased,
             -- THE TWO TERMS PRODUCTION NEEDS AND A TEST DATABASE DOES NOT.
             -- Both stand for rows that no longer exist: net_pnl on trades the
             -- daily export moved to the repository, and cost basis a reset
             -- deleted with the positions. Without them this identity is only
             -- true of a database that never loses anything, which is not the
             -- one the desk runs on.
             coalesce((select sum((payload->>'realized_pnl')::numeric)
                         from public.paper_activity
                        where account_id=a.account_id
                          and event_type='trades_archived'
                          and payload ? 'realized_pnl'),0)                      as archived,
             coalesce((select sum((payload->>'basis_written_off')::numeric)
                         from public.paper_activity
                        where account_id=a.account_id
                          and event_type='account_reset'
                          and payload ? 'basis_written_off'),0)                 as written_off
        from public.paper_accounts a where a.account_id=$1`, [who])).rows[0];
    const n = x => Number(x);
    const near = (got, want, what) => assert.ok(Math.abs(got - want) < 1e-6,
      `${where}: ${what} - got ${got}, expected ${want}`);
    near(n(r.cash), n(r.ledger),
      'cash is not the sum of its activity rows, so money moved without an event');
    near(n(r.reserved_cash), n(r.live_ceilings),
      'reserved cash is not what live orders claimed');
    near(n(r.cash), n(r.starting_cash) - n(r.basis) + n(r.realized) + n(r.archived)
                    + n(r.rebased) - n(r.written_off),
      'cash != starting cash - open basis + realised + archived realised + resets - written-off basis');
    assert.ok(n(r.cash) >= 0, `${where}: cash went negative`);
    assert.ok(n(r.reserved_cash) >= 0, `${where}: reserved cash went negative`);
    assert.ok(n(r.cash) - n(r.reserved_cash) >= -1e-9,
      `${where}: the desk committed more than it holds (cash ${r.cash}, reserved ${r.reserved_cash})`);
    assert.ok(n(r.thinnest) >= 0, `${where}: a position holds negative shares`);

    // AND THE VIEW PRODUCTION READS HAS TO SAY THE SAME THING.
    //
    // Everything above is this harness's own arithmetic against a database it
    // built thirty seconds ago. v_paper_desk_integrity is what runs hourly
    // against desks nobody wrote a test for. If the two can disagree then one
    // of them is decorative, so the view is checked on its inputs - the raw
    // numbers, against the ones just read independently - and on its verdict.
    //
    // The verdict alone would not be enough: a view that stopped computing
    // would return ok=true forever. That is what the DETECTOR HAS TO DETECT
    // block at the end of this file is for.
    const v = (await db.query(
      'select * from public.v_paper_desk_integrity where account_id=$1', [who])).rows[0];
    assert.ok(v, `${where}: the integrity view has no row for this desk`);
    near(n(v.cash),          n(r.cash),          'the view reads a different cash');
    near(n(v.reserved_cash), n(r.reserved_cash), 'the view reads a different reserved cash');
    near(n(v.ledger_cash),   n(r.ledger),        'the view adds the activity ledger up differently');
    near(n(v.live_ceilings), n(r.live_ceilings), 'the view counts different orders as live');
    near(n(v.open_basis),    n(r.basis),         'the view reads a different open cost basis');
    near(n(v.expected_cash),
         n(r.starting_cash) - n(r.basis) + n(r.realized) + n(r.archived)
         + n(r.rebased) - n(r.written_off),
         'the view expects a different cash than the identity does');
    near(n(v.realized), n(r.realized) + n(r.archived),
         'the view and this harness count different trades as realised');
    assert.ok(v.ok, `${where}: v_paper_desk_integrity reports [${v.breaches}] - ${v.note}`);
  }

  const account=(await db.query(`select create_paper_account('Test account',100) as id`)).rows[0].id;
  const submit=()=>db.query(`select submit_paper_order($1,$2,$3,'YES',10,0.55,6,'test') as id`,[account,command,band]);
  const order=(await submit()).rows[0].id;
  assert.equal((await submit()).rows[0].id,order,'Retry returns the original command');
  assert.equal(Number((await db.query('select reserved_cash from paper_accounts where account_id=$1',[account])).rows[0].reserved_cash),6);
  await db.exec(`set request.jwt.claim.sub='${other}';`);
  // Scoped to the member account: single-desk accounts (the P5.1 ledgers among
  // them) are readable by any signed-in user by design - live policy
  // single_desk_read - so an unscoped count measures them, not this rule.
  assert.equal((await db.query('select * from paper_accounts where account_id=$1',[account])).rows.length,0,'Nonowner cannot read account');
  await assert.rejects(submit(),/Account access denied/);
  await db.exec('reset role;set role anon;');
  await assert.rejects(db.query(`select create_paper_account('Unauthorized',100)`),/permission denied/);
  await db.exec('reset role;set role service_role;');
  const claimed=(await db.query('select claim_paper_order() as job')).rows[0].job;
  await books(account,'after an order is submitted and claimed');
  assert.equal(claimed.order_id,order);
  assert.equal((await db.query('select claim_paper_order() as job')).rows[0].job,null,'Account lease excludes second worker');
  await db.query(`insert into paper_book_evidence(snapshot_id,token_id,observed_at,payload) values('snapshot','yes',now(),'{}')`);
  const result={status:'filled',reason:null,shares:'10',notional:'5.30',fee:'0.12425',snapshot_id:'snapshot',
    fills:[{shares:'4',price:'.50',notional:'2',fee:'.05'},{shares:'6',price:'.55',notional:'3.30',fee:'.07425'}]};
  const finish=(r=result)=>db.query('select complete_paper_order($1,$2,$3::jsonb)',[order,claimed.lease_token,JSON.stringify(r)]);
  await assert.rejects(finish({...result,shares:'11'}),/Invalid amounts/);
  await assert.rejects(finish({...result,notional:'1'}),/Fill totals disagree/);
  await assert.rejects(finish({...result,fee:'Infinity'}),/Invalid amounts/);
  await assert.rejects(finish({...result,fills:[{shares:'10',price:'.50',notional:'5.30',fee:'.12425'}]}),/Invalid fill arithmetic/);
  await finish();await finish();
  await books(account,'after a fill, and after the same fill is replayed');
  const balance=(await db.query('select cash,reserved_cash from paper_accounts where account_id=$1',[account])).rows[0];
  assert.equal(Number(balance.cash),94.57575);assert.equal(Number(balance.reserved_cash),0);
  assert.equal(Number((await db.query('select shares from paper_positions')).rows[0].shares),10);
  assert.equal((await db.query("select count(*)::int as n from paper_activity where event_type='execution_completed'")).rows[0].n,1);
  const researchBefore=Number((await db.query('select count(*)::int as n from research_captures')).rows[0].n);
  await db.query("select capture_research_state('run1','commit1')");
  await db.query("select capture_research_state('run1','commit1')");
  await db.query("select capture_research_state('run2','commit1')");
  // One row from each of the two views still captured, once: a repeat or a
  // second command copies nothing. v_forecast_convergence is not copied since
  // 20260929233000 (plan v2 P1.6 phase 3, step 3.3) - its inputs are in the repo.
  assert.equal((await db.query('select count(*)::int as n from research_captures')).rows[0].n,researchBefore+2);
  assert.equal((await db.query("select count(*)::int as n from research_captures where source_relation='v_forecast_convergence'")).rows[0].n,0,
    'the convergence view is still copied into research_captures');
  await assert.rejects(db.query("delete from research_captures"),/permission denied|Append-only/);
  await assert.rejects(db.query("truncate research_captures"),/permission denied/);
  await db.exec('reset role;set role anon;');
  await assert.rejects(db.query('select * from research_captures'),/permission denied/);

  // Settled research facts are evidence: retries may insert missing rows but
  // even the worker role cannot rewrite, delete or truncate an existing fact.
  await db.exec('reset role;');
  await db.query("insert into fact_forecast_outcome(city_key,for_date,model,lead_days) values('london',current_date-1,'model',1)");
  await db.exec('set role service_role;');
  await assert.rejects(db.query("update fact_forecast_outcome set model='changed'"),/permission denied|Append-only/);
  await assert.rejects(db.query('delete from fact_forecast_outcome'),/permission denied|Append-only/);
  await assert.rejects(db.query('truncate fact_forecast_outcome'),/permission denied|Append-only/);
  await db.query("insert into fact_forecast_outcome(city_key,for_date,model,lead_days) values('madrid',current_date-1,'model',1)");

  await db.exec(`reset role;insert into signals values(1,'ENTER','s1',now(),'test strategy');
    set role authenticated;set request.jwt.claim.sub='${uid}';`);
  const policy={strategies:['s1'],cities:['london'],max_plan_usd:10,max_exposure_usd:50,min_edge:.03};
  await db.query("select set_paper_policy($1,'assisted',true,$2)",[account,JSON.stringify(policy)]);
  await db.exec('reset role;set role service_role;');
  // NO, not YES: the desk already holds YES on this band (the fills above), and
  // since P5.0 item 1 a plan cannot buy a side the desk holds. Approval is what
  // this block tests, so it buys the side the desk does not hold.
  const legs=[{band_id:band,side:'YES',shares:'4',limit_price:'.50',cash_ceiling:'2.10'}];
  const approvalLegs=[{...legs[0],side:'NO'}];
  const plan=(await db.query('select publish_paper_plan($1,$2,1,$3,$4) as id',
    [account,'40000000-0000-0000-0000-000000000002',JSON.stringify(approvalLegs),JSON.stringify({net_edge_per_share:'.10'})])).rows[0].id;
  await db.exec(`reset role;set role authenticated;set request.jwt.claim.sub='${other}';`);
  assert.equal((await db.query('select * from paper_trade_plans')).rows.length,0);
  await assert.rejects(db.query('select approve_paper_plan($1)',[plan]),/Account access denied/);
  await db.exec(`set request.jwt.claim.sub='${uid}';`);
  await db.query('select approve_paper_plan($1)',[plan]);
  await db.query('select approve_paper_plan($1)',[plan]);
  assert.equal(Number((await db.query('select reserved_cash from paper_accounts where account_id=$1',[account])).rows[0].reserved_cash),2.10);
  const queued=(await db.query('select order_id from paper_orders where plan_id=$1',[plan])).rows;
  assert.equal(queued.length,1,'Repeated approval never duplicates legs');
  // A PLAN MUST STOP READING "queued" ONCE ITS ORDER IS DONE. Measured on the
  // live desk 20 Sep: 61 orders filled, 7 partial, 8 expired - and all 72
  // plans still said queued, every one of them past its expiry, because
  // nothing ever walked paper_orders.plan_id back to the plan. The desk was
  // trading and the screen showed a graveyard.
  const planStatus=async()=>(await db.query('select status from paper_trade_plans where plan_id=$1',[plan])).rows[0].status;
  assert.equal(await planStatus(),'queued','a live order is the one case where queued is true');
  await db.query('select cancel_paper_order($1)',[queued[0].order_id]);
  await books(account,'after an order is cancelled and its reserve released');
  assert.equal(await planStatus(),'canceled','a canceled order must not leave its plan reading queued forever');
  assert.equal(Number((await db.query('select reserved_cash from paper_accounts where account_id=$1',[account])).rows[0].reserved_cash),0);
  const exitCommand='40000000-0000-0000-0000-000000000003';
  const exit=()=>db.query("select submit_paper_exit($1,$2,$3,'YES',4,.60) as id",[account,exitCommand,band]);
  const exitId=(await exit()).rows[0].id;
  assert.equal((await exit()).rows[0].id,exitId);
  await assert.rejects(db.query("select submit_paper_exit($1,$2,$3,'YES',7,.60)",
    [account,'40000000-0000-0000-0000-000000000004',band]),/Shares already sold or reserved/);
  await db.exec('reset role;set role service_role;');
  const exitJob=(await db.query('select claim_paper_order() as job')).rows[0].job;
  await db.query('select complete_paper_order($1,$2,$3)',[exitId,exitJob.lease_token,JSON.stringify({status:'filled',shares:'4',
    notional:'2.40',fee:'.048',snapshot_id:'snapshot',fills:[{shares:'4',price:'.60',notional:'2.40',fee:'.048'}]})]);
  const pos=(await db.query('select * from paper_positions')).rows[0];
  assert.equal(Number(pos.shares),6);assert.equal(Number(pos.cost_basis),3.25455);assert.equal(Number(pos.realized_pnl),.1823);
  assert.equal(Number((await db.query('select cash from paper_accounts where account_id=$1',[account])).rows[0].cash),96.92775);
  await db.exec(`reset role;set role authenticated;set request.jwt.claim.sub='${uid}';`);
  await db.query("select set_paper_policy($1,'automatic',true,$2)",[account,JSON.stringify(policy)]);
  await db.query('select set_paper_exit_policy($1,true,.25,.15)',[account]);
  await books(account,'after the exit policy changes');
  const version=(await db.query('select policy_version from paper_accounts where account_id=$1',[account])).rows[0].policy_version;
  await db.exec('reset role;set role service_role;');
  const preview={status:'filled',shares:'6',notional:'4.80',fee:'.048',snapshot_id:'snapshot',
    fills:[{shares:'6',price:'.80',notional:'4.80',fee:'.048'}]};
  const auto=(await db.query("select queue_automatic_paper_exit($1,$2,$3,'YES',.80,$4,$5) as id",
    [account,'40000000-0000-0000-0000-000000000005',band,JSON.stringify(preview),version])).rows[0].id;
  assert.ok(auto,'Entry pause leaves explicitly enabled exits available');
  // P5.0 item 2: 5 of 5 live exits expired at a 5-minute life. An exit lives 30.
  assert.equal(Number((await db.query(
    'select extract(epoch from expires_at-requested_at)::int as s from paper_orders where order_id=$1',[auto])).rows[0].s)>=1800,true,
    'an automatic exit order must live at least 30 minutes');
  const exitClaim=(await db.query('select claim_paper_order() as job')).rows[0].job;
  assert.equal(exitClaim.order_id,auto);
  await db.query('select complete_paper_order($1,$2,$3)',[auto,exitClaim.lease_token,JSON.stringify(preview)]);
  await books(account,'after an automatic exit filled');
  assert.equal(Number((await db.query('select shares from paper_positions')).rows[0].shares),0);
  await db.exec(`reset role;set role authenticated;set request.jwt.claim.sub='${uid}';`);
  const expiring=(await db.query("select submit_paper_order($1,$2,$3,'YES',2,.50,1.10,'expiry test') as id",
    [account,'40000000-0000-0000-0000-000000000006',band])).rows[0].id;
  await db.exec('reset role;set role service_role;');
  await db.query("update paper_orders set expires_at=now()-interval '1 second' where order_id=$1",[expiring]);
  await db.query('select expire_paper_commands()');await db.query('select expire_paper_commands()');
  await books(account,'after the expiry sweep ran twice');
  assert.equal(Number((await db.query('select reserved_cash from paper_accounts where account_id=$1',[account])).rows[0].reserved_cash),0);
  assert.equal((await db.query("select count(*)::int as n from paper_activity where event_type='order_expired'")).rows[0].n,1);
  await db.exec(`reset role;set role authenticated;set request.jwt.claim.sub='${uid}';`);
  const settleOrder=(await db.query("select submit_paper_order($1,$2,$3,'YES',2,.50,1.10,'settlement test') as id",
    [account,'40000000-0000-0000-0000-000000000007',band])).rows[0].id;
  await db.exec('reset role;set role service_role;');
  const settleClaim=(await db.query('select claim_paper_order() as job')).rows[0].job;
  await db.query('select complete_paper_order($1,$2,$3)',[settleOrder,settleClaim.lease_token,JSON.stringify({status:'filled',shares:'2',
    notional:'1',fee:'.025',snapshot_id:'snapshot',fills:[{shares:'2',price:'.50',notional:'1',fee:'.025'}]})]);
  const beforeSettlement=Number((await db.query('select cash from paper_accounts where account_id=$1',[account])).rows[0].cash);
  const gamma={conditionId:'condition',closed:true,umaResolutionStatus:'resolved'};
  const clob={condition_id:'condition',closed:true,accepting_orders:false,tokens:[{token_id:'yes',winner:true},{token_id:'no',winner:false}]};
  await db.query('insert into paper_resolution_evidence(proof_id,condition_id,token_yes,token_no,winning_token,gamma,clob,source_urls) values($1,$2,$3,$4,$5,$6,$7,$8)',
    ['resolution','condition','yes','no','yes',JSON.stringify(gamma),JSON.stringify(clob),'[]']);
  assert.equal((await db.query("select settle_paper_inventory($1,'resolution') as n",[band])).rows[0].n,1);
  assert.equal((await db.query("select settle_paper_inventory($1,'resolution') as n",[band])).rows[0].n,0);
  await books(account,'after venue settlement, and after it was replayed');
  assert.equal(Number((await db.query('select cash from paper_accounts where account_id=$1',[account])).rows[0].cash),beforeSettlement+2);
  assert.equal(Number((await db.query('select shares from paper_positions')).rows[0].shares),0);
  assert.equal((await db.query('select count(*)::int as n from paper_position_settlements')).rows[0].n,1);

  // THE FILL AND THE PAYOUT BOTH REACH paper_trades, which is the table every
  // page that shows trading reads. Before 16 Sep only the approve-by-hand RPC
  // wrote it, so an automatic desk could fill four orders and the dashboard
  // would say it had never traded.
  const settled=(await db.query(
    'select * from paper_trades where band_id=$1 order by opened_at',[band])).rows;
  assert.ok(settled.length>0,'a filled order must open a paper_trades row');
  const paid=settled.find(t=>t.close_reason==='venue_resolution_won');
  assert.ok(paid,'a position held to resolution must close - it never sees a SELL order, '
    +'so without this net_pnl stays null for ever and analytics stays empty');
  // 2 shares bought at .50 (notional 1, fee .025), paid out at 1.00 each.
  assert.equal(Number(paid.shares),2);
  assert.equal(Number(paid.close_price),1);
  assert.equal(Number(paid.gross_pnl),1,'gross is proceeds minus entry notional, no fees');
  assert.equal(Number(paid.net_pnl),0.975,'net also subtracts the entry fee');
  assert.equal(paid.city_key,'london','the city is denormalised so the row survives a band prune');
  assert.equal(paid.approved_by_user,false,'nobody approved this - it was automatic');

  // A replayed completion must not record the trade twice. complete_paper_order
  // returns early on an already-terminal order, and the unique index on
  // order_id is the second guard.
  const tradesBefore=(await db.query('select count(*)::int as n from paper_trades')).rows[0].n;
  await db.query('select complete_paper_order($1,$2,$3)',[settleOrder,settleClaim.lease_token,
    JSON.stringify({status:'filled',shares:'2',notional:'1',fee:'.025',snapshot_id:'snapshot',
      fills:[{shares:'2',price:'.50',notional:'1',fee:'.025'}]})]);
  assert.equal((await db.query('select count(*)::int as n from paper_trades')).rows[0].n,tradesBefore,
    'a replayed fill recorded a second trade');

  // ---- THE DECISION THAT PRODUCED THE TRADE ------------------------------
  //
  // 65 fills on the live desk carried NULL forecast_version,
  // calibration_version, cost_version and fill_quality, and the ledger had
  // zero rows. Every one of those inputs is rewritten by the next pricing
  // run, so a trade that does not copy them keeps no record of what it was
  // priced on and every post-mortem is guesswork.

  // fill_quality is the share of the request that filled, and both numbers
  // were on the row the whole time.
  assert.equal(Number(paid.fill_quality),1,
    'a fully filled order must record fill_quality 1.0');

  // The chain is written as the money moves, not maintained beside it.
  const links=(await db.query(
    'select event_type,payload from ledger where trade_id=$1 order by recorded_at',
    [paid.trade_id])).rows;
  assert.deepEqual(links.map(l=>l.event_type),['fill','close'],
    'ledger has a column for every part of the chain and had zero rows in it - '
    +'an empty table the schema advertises implies a record that does not exist');
  assert.equal(Number(links[0].payload.filled_shares),2);
  assert.equal(Number(links[1].payload.net_pnl),0.975);

  // NO SIGNAL, NO INVENTED LINEAGE. These orders carry no signal_id, so there
  // is nothing to copy and the columns stay null rather than being filled
  // with whatever is current.
  assert.equal(paid.forecast_version,null);

  // ...and with a signal, the snapshot is found at the shallow path.
  await db.query(`insert into public.signals(signal_id,action,strategy_id,payload,regime_label)
    values (901,'ENTER','s1',$1::jsonb,'SHARP'),(902,'ENTER','s1',$2::jsonb,null)`,
    [JSON.stringify({decision_snapshot:{[band]:{
       forecast_version:'11111111-1111-1111-1111-111111111111',
       calibration_version:'22222222-2222-2222-2222-222222222222',
       cost_version:'33333333-3333-3333-3333-333333333333',
       raw_prob:0.31,calibrated_prob:0.34,sigma_c:1.8}}}),
     // the older shape: only the forecast identity survives, buried in the
     // full BandView. Recovering it is what let the 65 existing fills get
     // back what was recoverable instead of staying blank for ever.
     JSON.stringify({decision_inputs:{[band]:{decision_evidence:{forecast:{
       forecast_version:'44444444-4444-4444-4444-444444444444'}}}}})]);
  const shallow=(await db.query(
    'select arbdesk_private.decision_snapshot(901,$1) as s',[band])).rows[0].s;
  assert.equal(shallow.forecast_version,'11111111-1111-1111-1111-111111111111');
  assert.equal(Number(shallow.calibrated_prob),0.34);
  const recovered=(await db.query(
    'select arbdesk_private.decision_snapshot(902,$1) as s',[band])).rows[0].s;
  assert.equal(recovered.forecast_version,'44444444-4444-4444-4444-444444444444');
  assert.equal(recovered.recovered_from,'decision_inputs');

  // THE REGIME CAME LAST AND FROM SOMEWHERE ELSE. It is not in the per-band
  // snapshot - signal_engine never froze it - it is a column on the signal,
  // and every one of the 69 trades on the books was recoverable by a join
  // right up until signals gets pruned. Stamping it is what makes that prune
  // safe, which is the same argument the three versions already won.
  assert.equal(shallow.regime_label,'SHARP',
    'the regime is on the signal and nowhere else once signals is archived');
  assert.ok(!('regime_label' in recovered),
    'a signal with no regime must not acquire one - an invented regime is worse than a null');

  // WRITE-ONCE. A snapshot that can be edited afterwards is not evidence.
  await db.query(`update paper_trades set forecast_version=$2 where trade_id=$1`,
    [paid.trade_id,'11111111-1111-1111-1111-111111111111']);
  await assert.rejects(
    db.query(`update paper_trades set forecast_version=$2 where trade_id=$1`,
      [paid.trade_id,'99999999-9999-9999-9999-999999999999']),
    /forecast_version is the decision that produced this trade/,
    'rebanking was able to rewrite what the desk believed at entry');
  // regime_label was the one lineage column the guard did not name, so the one
  // field nothing ever wrote was also the one field anything could rewrite.
  await db.query(`update paper_trades set regime_label='SHARP' where trade_id=$1`,
    [paid.trade_id]);
  await assert.rejects(
    db.query(`update paper_trades set regime_label='NORMAL' where trade_id=$1`,
      [paid.trade_id]),
    /regime_label is the decision that produced this trade/,
    'the regime the desk traded in was rewritable after the fact');

  // ...while everything that is not lineage still moves freely.
  await db.query('update paper_trades set close_price=1 where trade_id=$1',[paid.trade_id]);

  // Outcome collection attempts are private, append-only evidence. A failed
  // source read is retained for diagnosis but cannot be promoted into the
  // verified weather view or rewritten after the fact.
  await db.exec('reset role;set role service_role;');
  await db.query(`insert into weather_resolution_attempts(
    attempt_id,market_id,city_key,for_date,source_authority,station_id,source_url,
    outcome_status,parser_version,detail)
    values('attempt-1',$1,'london',current_date,'test authority','EGLL',
      'https://example.invalid','not_final','test-v1','{"reason":"next day absent"}')`,[market]);
  assert.equal(Number((await db.query('select attempts from v_weather_resolution_collection_health')).rows[0].attempts),1);
  await assert.rejects(db.query("update weather_resolution_attempts set outcome_status='captured'"),/permission denied|Append-only/);
  await assert.rejects(db.query('delete from weather_resolution_attempts'),/permission denied|Append-only/);
  await assert.rejects(db.query('truncate weather_resolution_attempts'),/permission denied|Append-only/);
  await db.exec('reset role;set role anon;');
  await assert.rejects(db.query('select * from weather_resolution_attempts'),/permission denied/);

  // The optional single-desk mode removes application credentials without
  // granting the browser role direct paper or research access.
  await db.exec("reset role;reset request.jwt.claim.sub;set role anon;");
  await assert.rejects(db.query("select create_single_paper_account('Blocked',250)"),/permission denied/);
  await assert.rejects(db.query('select * from paper_accounts'),/permission denied/);
  await assert.rejects(db.query('select * from research_captures'),/permission denied/);
  await db.exec('reset role;set role service_role;');
  const single=(await db.query("select create_single_paper_account('Single desk',250) as id")).rows[0].id;
  assert.equal((await db.query("select create_single_paper_account('Ignored retry',999) as id")).rows[0].id,single);
  const visibleAccounts=(await db.query('select account_id,access_mode from paper_accounts')).rows;
  assert.ok(visibleAccounts.some(a=>a.account_id===single&&a.access_mode==='single_desk'));
  const singleOrder=(await db.query("select submit_single_paper_order($1,$2,$3,'NO',2,.40,1,'single test') as id",
    [single,'50000000-0000-0000-0000-000000000001',band])).rows[0].id;
  assert.ok(singleOrder);
  assert.equal((await db.query('select count(*)::int as n from paper_orders where account_id=$1',[single])).rows[0].n,1);
  assert.equal((await db.query('select cancel_single_paper_order($1) as canceled',[singleOrder])).rows[0].canceled,true);
  await db.query("select set_single_paper_policy($1,'assisted',true,$2)",[single,JSON.stringify(policy)]);
  await db.query('select set_single_paper_exit_policy($1,true,.25,.15)',[single]);
  await db.exec("reset role;insert into signals values(2,'ENTER','s1',now(),'single strategy');update signals set prob_at_fire=.61 where signal_id=2;set role service_role;");
  const singlePlan=(await db.query('select publish_paper_plan($1,$2,2,$3,$4) as id',
    [single,'50000000-0000-0000-0000-000000000002',JSON.stringify(legs),JSON.stringify({net_edge_per_share:'.10'})])).rows[0].id;
  await db.query('select approve_single_paper_plan($1)',[singlePlan]);
  assert.equal((await db.query("select count(*)::int as n from paper_orders where plan_id=$1",[singlePlan])).rows[0].n,1);

  // AND THE CASE THE WHOLE THING EXISTS FOR: the order fills, so the plan says
  // filled. This is the assertion that was missing while 61 filled orders sat
  // behind 0 filled plans.
  const singlePlanOrder=(await db.query('select order_id from paper_orders where plan_id=$1',[singlePlan])).rows[0].order_id;
  const singlePlanJob=(await db.query('select claim_paper_order() as job')).rows[0].job;
  assert.equal(singlePlanJob.order_id,singlePlanOrder,'the claim took a different order than the one under test');
  await db.query('select complete_paper_order($1,$2,$3)',[singlePlanOrder,singlePlanJob.lease_token,
    JSON.stringify({status:'filled',shares:'4',notional:'2.00',fee:'.05',snapshot_id:'snapshot',
      fills:[{shares:'4',price:'.50',notional:'2.00',fee:'.05'}]})]);
  assert.equal((await db.query('select status from paper_trade_plans where plan_id=$1',[singlePlan])).rows[0].status,'filled',
    'an order that filled must carry its plan with it');
  // POSITIONS CARRY THEIR ENTRY (plan v2 P5.0 item 4,
  // 20260924070000_positions_carry_their_lineage.sql): the order, strategy,
  // ledger, plan (the group of linked legs) and the probability it was bought at.
  const lineage=async(side)=>(await db.query(
    `select strategy_id,ledger_id,entry_order_id,group_id,p_at_entry,entry_decision_id,p_cons_at_entry,params_version,entered_at
       from paper_positions where account_id=$1 and band_id=$2 and side=$3`,[single,band,side])).rows[0];
  const entry=await lineage('YES');
  assert.deepEqual([entry.strategy_id,entry.ledger_id,entry.entry_order_id,entry.group_id,Number(entry.p_at_entry)],
    ['s1',single,singlePlanOrder,singlePlan,0.61],'a new position records the entry that opened it');
  assert.deepEqual([entry.entry_decision_id,entry.p_cons_at_entry,entry.params_version],[null,null,null],
    'the columns later steps fill (P5.11, P5.3, P5.5) stay empty rather than guessed');
  assert.ok(entry.entered_at!==null);
  // The two-leg plan below buys YES again on this band, and since P5.0 item 1
  // a desk cannot buy a side it holds. So the desk sells what it just bought
  // first: a flat position (0 shares) is not a holding.
  const flatExit=(await db.query("select submit_single_paper_exit($1,$2,$3,'YES',4,.50) as id",
    [single,'50000000-0000-0000-0000-0000000000e1',band])).rows[0].id;
  const flatJob=(await db.query('select claim_paper_order() as job')).rows[0].job;
  assert.equal(flatJob.order_id,flatExit);
  await db.query('select complete_paper_order($1,$2,$3)',[flatExit,flatJob.lease_token,
    JSON.stringify({status:'filled',shares:'4',notional:'2.00',fee:'.05',snapshot_id:'snapshot',
      fills:[{shares:'4',price:'.50',notional:'2.00',fee:'.05'}]})]);
  assert.equal(Number((await db.query("select shares from paper_positions where account_id=$1 and side='YES'",[single])).rows[0].shares),0);

  // A TWO-LEG PLAN WITH ONE LEG ON IS NOT "filled". s8 covers two buckets and
  // s9 builds a ladder, so this is their shape - carried here under s1, the
  // strategy this desk's policy allows, because the rule under test is about
  // legs and not about which strategy proposed them. Reporting the whole plan
  // filled because one leg came back would claim a position the desk does not
  // hold.
  await db.exec("reset role;insert into signals values(3,'ENTER','s1',now(),'two legs');set role service_role;");
  // Two sides of the one band the fixture carries: its inventory contracts
  // are counted against exactly one band, and a second would move them.
  const twoLegs=[{band_id:band,side:'YES',shares:'2',limit_price:'.50',cash_ceiling:'1.10'},
                 {band_id:band,side:'NO', shares:'2',limit_price:'.40',cash_ceiling:'0.90'}];
  const coverPlan=(await db.query('select publish_paper_plan($1,$2,3,$3,$4) as id',
    [single,'50000000-0000-0000-0000-000000000003',JSON.stringify(twoLegs),JSON.stringify({net_edge_per_share:'.10'})])).rows[0].id;
  await db.query('select approve_single_paper_plan($1)',[coverPlan]);
  const coverOrders=(await db.query('select order_id,side from paper_orders where plan_id=$1',[coverPlan])).rows;
  assert.equal(coverOrders.length,2,'a two-leg plan must place two orders');
  const coverStatus=async()=>(await db.query('select status from paper_trade_plans where plan_id=$1',[coverPlan])).rows[0].status;
  assert.equal(await coverStatus(),'queued');

  // Cancel the NO leg first, deliberately: the book evidence in this fixture
  // is the YES token's, so which leg gets filled has to be chosen here rather
  // than left to whichever one claim_paper_order() happens to hand back.
  const noLeg=coverOrders.find(o=>o.side==='NO').order_id;
  const yesLeg=coverOrders.find(o=>o.side==='YES').order_id;
  await db.query('select cancel_single_paper_order($1)',[noLeg]);
  assert.equal(await coverStatus(),'queued',
    'one leg canceled while the other is still working is not a finished plan');

  const legJob=(await db.query('select claim_paper_order() as job')).rows[0].job;
  assert.equal(legJob.order_id,yesLeg,'the only claimable leg left is the YES one');
  await db.query('select complete_paper_order($1,$2,$3)',[yesLeg,legJob.lease_token,
    JSON.stringify({status:'filled',shares:'2',notional:'1.00',fee:'.025',snapshot_id:'snapshot',
      fills:[{shares:'2',price:'.50',notional:'1.00',fee:'.025'}]})]);
  assert.equal(await coverStatus(),'partial',
    'one leg filled and one canceled is a PARTIAL plan - calling it filled claims a position the desk does not hold');
  const reentry=await lineage('YES');
  assert.deepEqual([reentry.entry_order_id,reentry.group_id],[yesLeg,coverPlan],
    'a position bought again from flat is a new entry and records the new order');
  // ======================================================================
  // A RESET FLATTENS A DESK. IT DOES NOT ERASE ITS RECORD.
  //
  // paper_desk_reset had no contract at all, which is how it kept the one
  // behaviour the desk is not allowed to have: it ran `delete from` on
  // paper_position_settlements, paper_positions, paper_orders and
  // paper_trade_plans, so pressing Reset destroyed every order the desk had
  // placed, every proposal it made and every block reason that explained why.
  //
  // The line is between a RECORD and a DERIVED TOTAL. Orders, plans and
  // settlements are records. paper_positions is a running total of them, and
  // clearing it is only safe BECAUSE they survive to rebuild it - which was
  // not true before, when the reset deleted its own sources.
  //
  // Run on the desk that has been trading for the whole file, so the rows
  // being protected are real ones rather than a fixture made to pass.
  // ======================================================================
  const before = (await db.query(`
    select (select count(*) from public.paper_orders      where account_id=$1) as orders,
           (select count(*) from public.paper_trade_plans where account_id=$1) as plans,
           (select count(*) from public.paper_position_settlements where account_id=$1) as settled,
           (select count(*) from public.paper_trades      where account_id=$1) as trades`,
    [account])).rows[0];
  assert.ok(Number(before.orders) > 0 && Number(before.plans) > 0
            && Number(before.settled) > 0,
    'the reset contract is meaningless unless the desk actually has a record to lose');

  // IT HAD TO BE UNPAUSED FIRST, or "still paused afterwards" proves nothing:
  // the desk is already paused by the time this runs, so an assertion that it
  // is paused would hold even if the reset stopped pausing it. A mutation
  // removing entries_paused=true walked straight through the first version.
  await db.query('update public.paper_accounts set entries_paused=false where account_id=$1',
    [account]);

  // AND THE RESET HAS TO COMPLETE AT ALL. The previous version opened with
  // `delete from paper_position_settlements`, which carries an append-only
  // trigger, so it raised "Append-only record; write a linked correction
  // instead" and rolled back the whole function - and had it got past that,
  // paper_activity.order_id references paper_orders with no ON DELETE action,
  // so the next delete would have been refused too. Verified against the live
  // database on the trading desk (78 orders, 83 activity rows referencing
  // them) in a rolled-back transaction.
  //
  // So nothing was ever destroyed: the database refused. What was broken is
  // that Reset silently did NOTHING on any desk that had settled a position.
  // This call failing is the regression, and it is why the assertions below
  // are reachable at all.
  await db.query('select paper_desk_reset($1)', [account]);

  const after = (await db.query(`
    select (select count(*) from public.paper_orders      where account_id=$1) as orders,
           (select count(*) from public.paper_trade_plans where account_id=$1) as plans,
           (select count(*) from public.paper_position_settlements where account_id=$1) as settled,
           (select count(*) from public.paper_trades      where account_id=$1) as trades,
           (select count(*) from public.paper_positions   where account_id=$1) as positions,
           (select count(*) from public.paper_orders where account_id=$1
             and status in ('queued','working')) as still_live,
           (select cash from public.paper_accounts where account_id=$1) as cash,
           (select starting_cash from public.paper_accounts where account_id=$1) as start,
           (select entries_paused from public.paper_accounts where account_id=$1) as paused`,
    [account])).rows[0];

  assert.equal(after.orders,  before.orders,  'a reset deleted orders - that is the desk\'s record of what it asked the venue for');
  assert.equal(after.plans,   before.plans,   'a reset deleted trade plans, and with them every reason a trade was blocked');
  assert.equal(after.settled, before.settled, 'a reset deleted settlements, each of which carries its own proof_id');
  assert.equal(after.trades,  before.trades,  'a reset deleted trades');
  assert.equal(Number(after.positions), 0, 'the running position total survived, so the desk still holds exposure its cash no longer covers');
  assert.equal(Number(after.still_live), 0, 'an order was left live after a reset and can still fill against cash that was returned');
  assert.equal(Number(after.cash), Number(after.start), 'cash did not go back to the starting balance');
  assert.equal(after.paused, true, 'a reset desk must not resume entering on its own');

  // And the books still balance afterwards, which is the point of resetting.
  await books(account, 'after the desk was reset');

  // ======================================================================
  // YOU CANNOT SELL MORE THAN YOU HOLD, INCLUDING WHAT IS ALREADY QUEUED.
  //
  // submit_single_paper_exit is not a thin wrapper - it carries the over-sell
  // guard, and nothing exercised it. The guard that matters is the CUMULATIVE
  // one: two exits that each fit the position but together exceed it. Without
  // the `pending` term a desk could queue its whole position twice and fill
  // both, ending short a contract it never owned.
  // ======================================================================
  await db.exec('reset role; set role service_role;');
  const held=(await db.query(
    `select band_id, side, shares from public.paper_positions
      where account_id=$1 and shares > 0 order by shares desc limit 1`,[single])).rows[0];
  assert.ok(held,'the exit contract needs the single desk to be holding something');
  const have=Number(held.shares);

  await assert.rejects(
    db.query('select submit_single_paper_exit($1,$2,$3,$4,$5,0.10)',
      [single,crypto.randomUUID(),held.band_id,held.side,have+1]),
    /Shares already sold or reserved for exit/,
    'an exit larger than the position was accepted');

  await assert.rejects(
    db.query('select submit_single_paper_exit($1,$2,$3,$4,$5,0.10)',
      [single,crypto.randomUUID(),
       '20000000-0000-0000-0000-0000000000ff',held.side,1]),
    /No position/, 'an exit was accepted on a band the desk holds nothing in');

  // Queue the whole position, then try to queue any part of it again.
  const firstExit=crypto.randomUUID();
  await db.query('select submit_single_paper_exit($1,$2,$3,$4,$5,0.10)',
    [single,firstExit,held.band_id,held.side,have]);
  await assert.rejects(
    db.query('select submit_single_paper_exit($1,$2,$3,$4,0.01,0.10)',
      [single,crypto.randomUUID(),held.band_id,held.side]),
    /Shares already sold or reserved for exit/,
    'the whole position was queued for exit twice - `pending` is not being counted');

  // Idempotent on command_key: a retried request is the same order, not a
  // second one, which is what makes an uncertain network response safe.
  const again=(await db.query('select submit_single_paper_exit($1,$2,$3,$4,$5,0.10) as id',
    [single,firstExit,held.band_id,held.side,have])).rows[0].id;
  assert.equal(
    (await db.query('select count(*)::int as n from public.paper_orders where command_key=$1',
      [firstExit])).rows[0].n,1,
    'a retried exit with the same command key created a second order');
  assert.ok(again,'the retry did not return the original order id');

  // A SELL reserves no cash - it returns some - so the books are unmoved.
  await books(single,'after a full-position exit was queued');

  // ======================================================================
  // THE LEASE IS WHAT STOPS TWO WORKERS WRITING THE SAME FILL.
  //
  // claim_paper_order hands out a lease_token and a lease_until, and
  // complete_paper_order refuses anything that does not match BOTH. One
  // exclusion case was tested - a second worker getting null while a lease is
  // live. Everything the completion side refuses was not, and each of those
  // is a way for a worker to write money the desk did not spend:
  //
  //   a stale or wrong token   a worker whose lease expired writing over the
  //                            one that replaced it
  //   an expired lease_until   the same, from the clock rather than the token
  //   more shares than asked   a fill larger than the order that authorised it
  //   NaN / Infinity           arithmetic that poisons cash and cost basis
  //   a non-terminal status    an order left neither open nor closed
  // ======================================================================
  await db.exec('reset role; set role service_role;');
  for (const o of (await db.query(
      `select order_id from public.paper_orders
        where account_id=$1 and status in ('queued','working')`,[single])).rows) {
    await db.query('select cancel_single_paper_order($1)',[o.order_id]);
  }
  const raceOrder=(await db.query(
    "select submit_single_paper_order($1,$2,$3,'YES',2,.50,2,'lease contract') as id",
    [single,crypto.randomUUID(),band])).rows[0].id;
  const raceJob=(await db.query('select claim_paper_order() as job')).rows[0].job;
  assert.equal(raceJob.order_id,raceOrder,'the claim did not hand back the order just queued');

  const good={status:'filled',shares:'2',notional:'1.00',fee:'.025',snapshot_id:'snapshot',
              fills:[{shares:'2',price:'.50',notional:'1.00',fee:'.025'}]};
  const finishRace=(lease,result)=>db.query('select complete_paper_order($1,$2,$3::jsonb)',
    [raceOrder,lease,JSON.stringify(result)]);

  await assert.rejects(finishRace(crypto.randomUUID(),good),/Lease lost/,
    'a worker holding the wrong lease token was able to complete the order');
  await assert.rejects(finishRace(null,good),/Active lease required|Lease lost/,
    'an order was completed with no lease at all');
  await assert.rejects(finishRace(raceJob.lease_token,{...good,shares:'3'}),
    undefined,'a fill larger than the order that authorised it was accepted');
  for (const bad of ['NaN','Infinity','-Infinity']) {
    await assert.rejects(finishRace(raceJob.lease_token,{...good,notional:bad}),
      undefined,`a notional of ${bad} was accepted into the cash ledger`);
  }
  await assert.rejects(finishRace(raceJob.lease_token,{...good,status:'working'}),
    /Invalid terminal status/,'an order was completed into a non-terminal status');

  // The clock, not the token: a lease that simply ran out.
  await db.query(`update public.paper_orders set lease_until=now()-interval '1 second'
                   where order_id=$1`,[raceOrder]);
  await assert.rejects(finishRace(raceJob.lease_token,good),/Lease lost/,
    'a worker completed an order on a lease that had already expired');

  // Put it back and finish honestly, so the desk is left consistent.
  await db.query(`update public.paper_orders set lease_until=now()+interval '5 minutes'
                   where order_id=$1`,[raceOrder]);
  await finishRace(raceJob.lease_token,good);
  assert.equal((await db.query(
    'select status from public.paper_orders where order_id=$1',[raceOrder])).rows[0].status,'filled');
  await books(single,'after a contested order was finally filled by its lease holder');

  // ======================================================================
  // MAKING, RE-POLICYING AND RETIRING A DESK.
  //
  // These three ran in production and were exercised by nothing. Every desk
  // below is created here, so none of this depends on which desks happen to
  // exist - which is the whole point: the engine has to be right for a desk
  // that does not exist yet.
  // ======================================================================
  await db.exec('reset role; set role service_role;');

  // A DESK STARTS PAUSED. "A desk that starts trading the moment it is named
  // is not a thing anyone wants to discover afterwards" - and nothing checked
  // it, on a function whose whole job is to make one.
  const fresh=(await db.query(
    "select paper_desk_create('Fresh desk',777,'automatic',null,$1::jsonb) as id",
    [JSON.stringify({cities:['london'],strategies:['s1'],min_edge:.03,max_plan_usd:10})])).rows[0].id;
  const f=(await db.query('select * from public.paper_accounts where account_id=$1',[fresh])).rows[0];
  assert.equal(f.entries_paused,true,'a newly created desk was live the moment it was named');
  assert.equal(Number(f.cash),777);
  assert.equal(Number(f.starting_cash),777);
  assert.equal(Number(f.reserved_cash),0);
  assert.equal(Number(f.policy_version),1);
  assert.equal(f.mode,'automatic');
  assert.equal(f.archived_at,null);
  // ...and it balances from its first instant, which is only true if creation
  // wrote the opening activity row.
  await books(fresh,'a desk that has just been created');

  for (const [args,why] of [
    ["'',100,'manual'",                   'a blank name'],
    [`'${'x'.repeat(101)}',100,'manual'`, 'a name over 100 characters'],
    ["'Zero cash',0,'manual'",            'zero starting cash'],
    ["'Negative',-1,'manual'",            'negative starting cash'],
    ["'Too rich',1000000000,'manual'",    'a billion in starting cash'],
    ["'Bad mode',100,'sideways'",         'a mode that is not manual, assisted or automatic'],
  ]) {
    await assert.rejects(db.query(`select paper_desk_create(${args})`), undefined,
      `paper_desk_create accepted ${why}`);
  }

  // Sub-accounts are one level deep, so a hierarchy cannot grow legs.
  const childDesk=(await db.query(
    "select paper_desk_create('Child',100,'manual',$1) as id",[fresh])).rows[0].id;
  await assert.rejects(
    db.query("select paper_desk_create('Grandchild',100,'manual',$1)",[childDesk]),
    /one level deep/, 'a sub-account was allowed to have a sub-account of its own');

  // RE-POLICYING. The policy version has to move, or a plan published against
  // the old policy cannot be told from one published against the new.
  await db.query("select paper_desk_update($1,null,null,null,null,$2::jsonb)",
    [fresh,JSON.stringify({cities:['ALL'],strategies:['s1','s4'],min_edge:.05,max_plan_usd:25})]);
  const repolicied=(await db.query(
    'select policy_version,policy from public.paper_accounts where account_id=$1',[fresh])).rows[0];
  assert.equal(Number(repolicied.policy_version),2,'the policy changed and its version did not');
  assert.equal(Number(repolicied.policy.min_edge),.05);

  await assert.rejects(
    db.query("select paper_desk_update($1)",['00000000-0000-0000-0000-000000000000']),
    /no such desk/, 'updating a desk that does not exist was allowed to pass silently');
  await assert.rejects(db.query("select paper_desk_update($1,null,null,'sideways')",[fresh]),
    /manual, assisted or automatic/);

  // THE BUDGET MOVES ONLY BEFORE THE DESK HAS TRADED. Changing it afterwards
  // makes the return meaningless, because return is measured against what the
  // desk started with.
  await db.query('select paper_desk_update($1,null,900)',[fresh]);
  assert.equal(Number((await db.query(
    'select starting_cash from public.paper_accounts where account_id=$1',[fresh])).rows[0].starting_cash),900);
  await books(fresh,'after the starting cash was changed on a desk that had not traded');

  await assert.rejects(db.query('select paper_desk_update($1,null,50)',[account]),
    /reset it before changing its starting cash/,
    'the budget of a desk that has already traded was rewritten under its own P&L');

  // ARCHIVING IS REVERSIBLE AND DELETES NOTHING.
  await db.query('select paper_desk_update($1,null,null,null,false)',[fresh]);
  await db.query('select paper_desk_archive($1,true)',[fresh]);
  const archived=(await db.query(
    'select archived_at,entries_paused from public.paper_accounts where account_id=$1',[fresh])).rows[0];
  assert.ok(archived.archived_at!==null,'archiving did not archive');
  assert.equal(archived.entries_paused,true,
    'an archived desk was left able to enter - archiving has to stop it on the way out');

  await db.query('select paper_desk_archive($1,false)',[fresh]);
  assert.equal((await db.query(
    'select archived_at from public.paper_accounts where account_id=$1',[fresh])).rows[0].archived_at,null,
    'an archived desk could not be brought back, which makes archiving a one-way door');
  assert.equal(Number((await db.query(
    'select count(*)::int as n from public.paper_accounts where account_id=$1',[fresh])).rows[0].n),1,
    'the desk row did not survive being archived and restored');
  await assert.rejects(
    db.query("select paper_desk_archive($1)",['00000000-0000-0000-0000-000000000000']),
    /no such desk/);

  // THE BOOTSTRAP MUST STILL ANSWER THE SAME DESK once several exist.
  // create_single_paper_account is what every caller without a desk id
  // resolves to, and with more than one single_desk row an unordered `limit 1`
  // answers arbitrarily - a different desk between two page loads. ad4_60
  // added `order by created_at` for exactly this; nothing checked it.
  //
  // Stated as a property rather than trusted to a mutation: heap order can
  // coincidentally return the right row, so removing the ORDER BY does not
  // reliably fail. This asserts the contract itself.
  const oldestDesk=(await db.query(
    `select account_id from public.paper_accounts
      where access_mode='single_desk' and kind is null order by created_at limit 1`)).rows[0].account_id;
  assert.equal(
    (await db.query("select create_single_paper_account('ignored',1) as id")).rows[0].id,
    oldestDesk,
    'the bootstrap resolved to a desk other than the oldest, so which desk a caller '
    +'gets depends on physical row order');

  // ======================================================================
  // THE SWITCHER'S "LIVE ORDERS" COUNTED A STATUS THAT CANNOT EXIST.
  //
  // v_paper_desks.live_orders counted paper_orders in 'pending' or 'leased'.
  // paper_orders_status_check permits queued, working, filled, partial,
  // rejected, expired and canceled - and nothing else - so the count was
  // structurally zero on every desk, including one with an order working at
  // that moment. It is the shape-versus-substance failure exactly: the column
  // existed, the view built, the number was always the same lie.
  //
  // Asserted as a property of a live order rather than against a status list,
  // so rewriting the vocabulary cannot make the test agree with itself.
  // ======================================================================
  await db.exec('reset role; set role service_role;');
  const liveOf = async (who) => Number((await db.query(
    'select live_orders from public.v_paper_desks where account_id=$1', [who])).rows[0].live_orders);
  const beforeLive = await liveOf(single);
  const countedOrder = (await db.query(
    "select submit_single_paper_order($1,$2,$3,'YES',2,.50,2,'live order count') as id",
    [single, crypto.randomUUID(), band])).rows[0].id;
  assert.equal(await liveOf(single), beforeLive + 1,
    'a desk with an order the engine will still act on reports no live orders');
  await db.query('select cancel_single_paper_order($1)', [countedOrder]);
  assert.equal(await liveOf(single), beforeLive,
    'a canceled order is still counted as live, so the reserve it released looks committed');

  // ======================================================================
  // THE DETECTOR HAS TO DETECT.
  //
  // books() above asserts v_paper_desk_integrity agrees with this harness's
  // own arithmetic after every operation. That catches a view that computes
  // the WRONG thing. It cannot catch a view that computes NOTHING: one that
  // returned ok=true unconditionally would sail through every assertion in
  // this file and through every hourly run in production, and the first
  // anyone would know of it is a desk quietly spending money it does not
  // have.
  //
  // So each identity is broken on purpose, inside a transaction that is
  // rolled back, and the view is required to name it. The breaches are the
  // contract; the note is what a person reads.
  // ======================================================================
  await db.exec('reset role; set role service_role;');

  const detects = async (what, sql, params, expected) => {
    await db.exec('begin');
    await db.query(sql, params);
    const bad = (await db.query(
      'select name, breaches, note from public.v_paper_desk_integrity where not ok')).rows;
    const verdict = (await db.query('select check_paper_desk_integrity(false) as v')).rows[0].v;
    await db.exec('rollback');
    await db.exec('reset role; set role service_role;');
    const named = new Set(bad.flatMap(r => r.breaches));
    for (const e of expected) assert.ok(named.has(e),
      `${what}: the view should have named ${e}; it named [${[...named].join(', ')}]`);
    assert.equal(verdict.ok, false,
      `${what}: check_paper_desk_integrity still reported every desk balanced`);
    assert.ok(bad.every(r => r.note && r.note.length > 20),
      `${what}: a breach was reported with no sentence explaining it`);
  };

  // Cash that moved with no event behind it. Both the ledger identity and the
  // cross-store one have to see it - if only one did, the other is asleep.
  await detects('cash moved without an activity row',
    'update public.paper_accounts set cash=cash+1 where account_id=$1', [account],
    ['cash_vs_ledger', 'cash_vs_expected']);

  // Reserved cash no live order claims. This is the one that silently eats a
  // desk's buying power, because nothing else in the engine ever recomputes it.
  await detects('reserved cash no live order claims',
    'update public.paper_accounts set reserved_cash=reserved_cash+5 where account_id=$1', [account],
    ['reserved_vs_live_orders']);

  // The open cost basis changing under the desk - a position edited by
  // anything that is not a fill.
  assert.ok(Number((await db.query(
      'select coalesce(sum(cost_basis),0) as b from public.paper_positions where account_id=$1',
      [single])).rows[0].b) > 0,
    'this contract needs a desk that is actually holding cost basis');
  await detects('open cost basis edited outside a fill',
    'update public.paper_positions set cost_basis=cost_basis+1 where account_id=$1', [single],
    ['cash_vs_expected']);

  // The two realised-P&L stores disagreeing. This is the bridge between
  // paper_positions and paper_trades dropping or double-counting a close, and
  // it is invisible in cash - the money is right, the record of why is not.
  await detects('the positions and the trades disagree about realised P&L',
    'update public.paper_positions set realized_pnl=realized_pnl+10 where account_id=$1', [single],
    ['realized_vs_positions']);

  // WHAT THE DATABASE REFUSES OUTRIGHT, WHICH IS BETTER THAN DETECTING IT.
  //
  // Four of the things the view reports cannot be produced at all: CHECK
  // constraints stop them at the write. The view keeps its columns for them
  // anyway - as a second pair of eyes on a constraint, so a migration that
  // drops one does not also remove the only thing that would notice. But the
  // contract for those four is that the write is REFUSED, not that it is
  // reported, and asserting the wrong one of those would quietly test nothing:
  // `update ... set cash=-1` raises 23514 before the view is ever consulted.
  for (const [what, sql, params] of [
    ['cash went negative',
     'update public.paper_accounts set cash=-1 where account_id=$1', [account]],
    ['reserved cash went negative',
     'update public.paper_accounts set reserved_cash=-1 where account_id=$1', [account]],
    ['the desk committed more than it holds',
     'update public.paper_accounts set reserved_cash=cash+100 where account_id=$1', [account]],
    ['a position held negative shares',
     'update public.paper_positions set shares=-1 where account_id=$1', [single]],
  ]) {
    await assert.rejects(db.query(sql, params), /violates check constraint/,
      `${what} and the database accepted it`);
  }

  // And the one everything above rests on: if paper_activity could be edited,
  // identity 1 would be unfalsifiable - cash could be made to match a ledger
  // that had been rewritten to match it.
  // And the one everything above rests on: if paper_activity could be edited,
  // identity 1 would be unfalsifiable - cash could be made to match a ledger
  // that had been rewritten to match it. TWO INDEPENDENT LOCKS, and both are
  // asserted, because either one alone is one migration away from gone.
  // Verified against production: service_role holds INSERT and SELECT on
  // paper_activity and nothing else.
  await assert.rejects(
    db.query('delete from public.paper_activity where account_id=$1', [account]),
    /permission denied/,
    'the server-side role can delete activity rows, so the ledger is only as durable as the code');
  await db.exec('reset role;');
  await assert.rejects(
    db.query('delete from public.paper_activity where account_id=$1', [account]),
    /Append-only record/,
    'the owner can delete activity rows - the grant is the only lock, and grants get re-run');
  await db.exec('set role service_role;');

  // A GAP IN THE RECORD IS NOT A BREACH.
  //
  // A desk reset before paper_desk_reset recorded the basis it wrote off is
  // missing a term of identity 3 that nothing can reconstruct. Reporting that
  // as unbalanced would put the desk permanently in the red for something
  // that happened once, months ago - and a detector that is always red is a
  // detector nobody reads. It has to say what it cannot answer and stay green
  // on everything it can.
  //
  // Reconstructed faithfully rather than asserted on a stub: a desk that puts
  // 20 into a position and is then reset the old way ends with cash back at
  // its starting balance, an account_reset row re-basing it, and NOTHING
  // saying where the 20 went. The identity is then genuinely out by 20 - so
  // the mutation that removes the unverifiable branch has something to fail
  // on, rather than this passing because the numbers happened to agree.
  await db.exec('begin');
  await db.query(`insert into public.paper_positions(account_id,band_id,side,shares,cost_basis)
                  values($1,$2,'YES',5,20)`, [fresh, band]);
  await db.query(`insert into public.paper_activity(account_id,event_type,payload,cash_delta)
                  values($1,'execution_completed','{}'::jsonb,-20)`, [fresh]);
  await db.query('update public.paper_accounts set cash=cash-20 where account_id=$1', [fresh]);
  const beforeGap = (await db.query(
    'select ok, cash from public.v_paper_desk_integrity where account_id=$1', [fresh])).rows[0];
  assert.equal(beforeGap.ok, true, 'the reconstruction is wrong before the reset even happens');

  await db.query('delete from public.paper_positions where account_id=$1', [fresh]);
  await db.query(`insert into public.paper_activity(account_id,event_type,payload,cash_delta)
                  values($1,'account_reset','{"positions_cleared":1}'::jsonb,20)`, [fresh]);
  await db.query('update public.paper_accounts set cash=cash+20 where account_id=$1', [fresh]);
  const gap = (await db.query(
    'select ok, breaches, unverifiable, note, cash, expected_cash from public.v_paper_desk_integrity where account_id=$1',
    [fresh])).rows[0];
  await db.exec('rollback');
  await db.exec('reset role; set role service_role;');
  assert.ok(Math.abs(Number(gap.cash) - Number(gap.expected_cash) + 20) < 1e-6,
    `the reconstruction should leave the identity out by exactly the 20 the reset swallowed, `
    + `it is out by ${Number(gap.expected_cash) - Number(gap.cash)}`);
  assert.equal(gap.ok, true,
    'a reset recorded before the write-off existed was reported as a breach, which puts the desk '
    + 'permanently in the red for a gap in the record');
  assert.ok(gap.unverifiable.includes('cash_vs_expected'),
    `the view did not say which identity it could no longer answer: [${gap.unverifiable}]`);
  assert.match(gap.note, /cannot be answered/,
    'the note did not tell the reader the number is missing rather than wrong');

  // ======================================================================
  // THE TWO THINGS PRODUCTION DOES THAT THE IDENTITY DID NOT SURVIVE.
  //
  // Everything above runs against a database that never loses a row. The real
  // one loses rows on purpose, twice, and under the identity as the harness
  // originally stated it BOTH of them put a desk permanently out of balance:
  //
  //   a reset deletes paper_positions, taking the open cost basis off the
  //   left of `cash = starting - basis + realised` and off nothing on the right
  //
  //   the daily export deletes closed trades thirty days after they close,
  //   taking their realised P&L off the right and off nothing on the left
  //
  // The first would have fired the moment anyone reset a desk holding
  // anything. The second was on a timer: thirty days after the first trade
  // closed, every desk would have gone red and stayed red.
  // ======================================================================

  // A trade that has been archived to the repository is still money the desk
  // made. Pick a desk that actually has one to lose.
  const victim = (await db.query(
    `select t.trade_id, t.net_pnl, t.account_id,
            (select count(*)::int from public.ledger l where l.trade_id=t.trade_id) as lineage
       from public.paper_trades t
      where t.account_id is not null and t.closed_at is not null
      order by t.closed_at limit 1`)).rows[0];
  assert.ok(victim, 'the archive contract needs a closed trade to archive');
  // WITHOUT LINEAGE BEHIND IT THIS CONTRACT IS VACUOUS. ledger.trade_id
  // references paper_trades with no ON DELETE action and every trade the
  // engine writes has ledger rows, so a trade with none would be the one case
  // the delete was always able to do - and the failure this asserts against
  // would never fire.
  assert.ok(Number(victim.lineage) > 0,
    'the trade chosen to archive has no ledger rows, so this proves nothing about the '
    + 'foreign key that stopped the prune deleting anything in production');
  const ledgerBefore = Number((await db.query(
    'select count(*)::int as n from public.ledger')).rows[0].n);
  const realizedBefore = Number((await db.query(
    'select realized from public.v_paper_desk_integrity where account_id=$1',
    [victim.account_id])).rows[0].realized);

  await db.query(
    "update public.paper_trades set closed_at=now()-interval '40 days' where trade_id=$1",
    [victim.trade_id]);
  const pruned = (await db.query(
    'select prune_exported_paper_trades(30, array[$1::uuid], false) as r',
    [victim.trade_id])).rows[0].r;
  assert.equal(Number(pruned.deleted), 1, 'the prune did not delete the trade it was given');
  assert.equal(Number(pruned.desks_credited), 1,
    'the prune deleted a desk\'s trade without writing the realised P&L anywhere');
  assert.equal(Number(pruned.lineage_rows_detached), Number(victim.lineage),
    'the prune did not release the ledger rows that reference this trade');
  assert.equal(Number((await db.query(
    'select count(*)::int as n from public.paper_trades where trade_id=$1',
    [victim.trade_id])).rows[0].n), 0, 'the trade is still in Postgres');

  // THE DECISION SURVIVES THE TRADE. Every ledger row is still there, and each
  // one that pointed at the archived trade now carries its id as data - the
  // same key the exported file is written under, so the join still exists.
  assert.equal(Number((await db.query(
    'select count(*)::int as n from public.ledger')).rows[0].n), ledgerBefore,
    'archiving a trade destroyed ledger rows - that is the decision that produced it');
  const detached = (await db.query(
    `select count(*)::int as n from public.ledger
      where trade_id is null and detail->>'archived_trade_id'=$1`,
    [victim.trade_id])).rows[0].n;
  assert.equal(Number(detached), Number(victim.lineage),
    'the ledger rows were released without recording which trade they belong to, so the '
    + 'lineage is now unjoinable to the exported file');

  const afterPrune = (await db.query(
    'select realized, realized_on_hand, realized_archived from public.v_paper_desk_integrity where account_id=$1',
    [victim.account_id])).rows[0];
  assert.ok(Math.abs(Number(afterPrune.realized_archived) - Number(victim.net_pnl)) < 1e-6,
    `the archived realised P&L is ${afterPrune.realized_archived}, the trade's was ${victim.net_pnl}`);
  assert.ok(Math.abs(Number(afterPrune.realized) - realizedBefore) < 1e-6,
    'the desk\'s realised P&L changed when a trade was moved to the repository - the money did not move, '
    + 'only the row did');
  // The assertion the whole thing is for: under the old prune this fails by
  // exactly the archived trade's net_pnl, for good.
  await books(victim.account_id, 'after a closed trade was archived to the repository and deleted');

  // ...and the same for a reset that flattens a desk still holding basis.
  const basisAtReset = Number((await db.query(
    'select coalesce(sum(cost_basis),0) as b from public.paper_positions where account_id=$1',
    [single])).rows[0].b);
  assert.ok(basisAtReset > 0,
    'the write-off contract needs a desk holding open cost basis at the moment it is reset - '
    + 'resetting an empty desk proves nothing, which is why the original reset contract passed');
  await db.query('select paper_desk_reset($1)', [single]);
  const wroteOff = (await db.query(
    `select (payload->>'basis_written_off')::numeric as w
       from public.paper_activity
      where account_id=$1 and event_type='account_reset'
      order by event_id desc limit 1`, [single])).rows[0];
  assert.ok(wroteOff && Math.abs(Number(wroteOff.w) - basisAtReset) < 1e-6,
    `the reset wrote off ${basisAtReset} of cost basis and recorded ${wroteOff && wroteOff.w}`);
  await books(single, 'after a desk holding open cost basis was reset');

  // ======================================================================
  // THE RPC THAT MAKES IT AN ALARM RATHER THAN A QUERY.
  // ======================================================================
  const healthy = (await db.query('select check_paper_desk_integrity(true) as v')).rows[0].v;
  assert.equal(healthy.ok, true, `every desk should balance here: ${JSON.stringify(healthy.detail)}`);
  assert.equal(Number(healthy.anomalies_recorded), 0, 'a balanced database recorded an anomaly');
  assert.equal(Number((await db.query(
    "select count(*)::int as n from public.anomalies where kind='paper_books_unbalanced'")).rows[0].n),
    0, 'a balanced database wrote a books anomaly');

  // A breach that persists is ONE alarm, not one an hour.
  await db.exec('begin');
  await db.query('update public.paper_accounts set cash=cash+3 where account_id=$1', [account]);
  const first  = (await db.query('select check_paper_desk_integrity(true) as v')).rows[0].v;
  const second = (await db.query('select check_paper_desk_integrity(true) as v')).rows[0].v;
  const recorded = (await db.query(
    "select detail, value from public.anomalies where kind='paper_books_unbalanced'")).rows;
  await db.exec('rollback');
  await db.exec('reset role; set role service_role;');
  assert.equal(first.ok, false);
  assert.equal(Number(first.anomalies_recorded), 1, 'the first check did not record the breach');
  assert.equal(Number(second.anomalies_recorded), 0,
    'the same breach was recorded twice within the hour, so a standing fault becomes a flood');
  assert.equal(recorded.length, 1);
  assert.equal(recorded[0].detail.account_id, account,
    'the anomaly did not name the desk it is about');
  assert.ok(String(recorded[0].detail.breaches).includes('cash_vs_ledger'),
    'the anomaly did not name what failed');

  // ======================================================================
  // THE BOOK PRUNE DECIDED TIES BY COIN FLIP.
  //
  // v_prunable_book_redundancy ranked with `order by observed_at desc` and
  // nothing else. book_snapshots has 3,177 pairs of rows sharing a band_id
  // AND an observed_at to the microsecond, so row_number() chose between
  // them arbitrarily - differently in its two window functions, and
  // differently again on each evaluation. The old view returned 53,915 rows
  // where a deterministic one returns 54,222.
  //
  // That is not a rounding difference. The archive EXPORTS this view over
  // many paginated requests and the prune COUNTS it again afterwards, and
  // the whole safety of the thing rests on those two numbers matching. A set
  // that answers differently each time it is asked is the "verified N rows
  // but prune would delete M" failure, built in.
  //
  // Ties are constructed here rather than hoped for.
  // ======================================================================
  const pruneBand='20000000-0000-0000-0000-0000000000f1';
  const otherBand='20000000-0000-0000-0000-0000000000f2';
  await db.exec(`insert into public.bands(band_id,market_id,band_index,band_label,band_lo,band_hi,open_low,open_high,token_yes,token_no,condition_id)
    values('${pruneBand}','${market}',91,'P1',1,1,false,false,'p1-yes','p1-no','p1-cond'),
          ('${otherBand}','${market}',92,'P2',2,2,false,false,'p2-yes','p2-no','p2-cond');`);
  // Day one: three snapshots, the last two TIED to the microsecond. Day two:
  // two snapshots. A second band with a single old row that must survive
  // because it is that band's newest.
  await db.exec(`insert into public.book_snapshots(snapshot_id,band_id,observed_at) values
     (9001,'${pruneBand}', timestamptz '2026-01-01 08:00:00+00'),
     (9007,'${pruneBand}', timestamptz '2026-01-01 09:00:00+00'),
     (9002,'${pruneBand}', timestamptz '2026-01-01 12:00:00+00'),
     (9003,'${pruneBand}', timestamptz '2026-01-01 12:00:00+00'),
     (9004,'${pruneBand}', timestamptz '2026-01-02 08:00:00+00'),
     (9005,'${pruneBand}', timestamptz '2026-01-02 09:00:00+00'),
     (9006,'${otherBand}', timestamptz '2026-01-01 08:00:00+00');`);

  const prunable = async () => (await db.query(
    `select snapshot_id from public.v_prunable_book_redundancy
      where snapshot_id between 9001 and 9999 order by snapshot_id`)).rows.map(r=>Number(r.snapshot_id));

  // A SNAPSHOT AN EDGE CITES IS EVIDENCE, NOT REDUNDANCY.
  //
  // edges.book_snapshot_id and signals.book_snapshot_id are foreign keys into
  // book_snapshots with ON DELETE NO ACTION. 6,075 of the 57,165 rows the
  // view used to offer were cited by a live edge, so the delete raised 23503
  // and the entire prune failed - every run, for as long as books was in the
  // archive. Cascading would have "fixed" it by destroying the lineage of a
  // trade. Refusing to offer them fixes it by deleting less.
  //
  // 9002 is redundant by every other measure - beaten by 9003 the same day -
  // and must survive purely because something points at it.
  await db.exec(`insert into public.edges(edge_id,band_id,book_snapshot_id)
    values(99001,'${pruneBand}',9002);`);
  // BOTH foreign keys, not just the one that happened to be populated.
  // signals.book_snapshot_id is null on every live row today, so a version
  // that checked edges alone would pass against production and start raising
  // 23503 the first time the signal engine records the book it read.
  await db.exec(`insert into public.signals(signal_id,action,book_snapshot_id)
    values(99002,'ENTER',9007);`);

  const firstAsk = await prunable();
  assert.deepEqual(firstAsk, await prunable(),
    'the view returned a different set the second time it was asked, so the count the '
    +'archive verifies and the count the prune checks can never be relied on to agree');

  // 9001 is beaten by 12:00 the same day. Exactly ONE of the tied pair goes -
  // not both (that would lose the day's closing book) and not neither (that
  // would keep a duplicate for ever). 9004 is beaten by 9005 the same day.
  // 9005 is its band's newest and 9006 is its band's only row: both stay.
  assert.deepEqual(firstAsk, [9001,9004],
    'the prunable set is not the rows that are both unread AND uncited - 9002 is '
    +'redundant but an EDGE points at it and 9007 is redundant but a SIGNAL does, '
    +'so deleting either would raise 23503 and take the lineage of a decision with it');

  const survivingDays = `select count(distinct (band_id,(observed_at at time zone 'UTC')::date))::int as n
                           from public.book_snapshots`;
  const daysBefore = Number((await db.query(survivingDays)).rows[0].n);
  const floorBefore = (await db.query(
    'select min(observed_at) as m from public.book_snapshots')).rows[0].m;

  // THE COUNT CONTRACT. A committed prune whose expected count does not match
  // what it is about to delete must refuse, because that number is the one
  // read back out of the uploaded archive.
  const refused = (await db.query(
    'select prune_book_redundancy(3,false,$1,$2) as v',
    ['2026-06-01T00:00:00Z', 999])).rows[0].v;
  assert.equal(refused.ok, false, 'a prune with the wrong verified count went ahead anyway');
  assert.ok(String(refused.error).includes('row count mismatch'));
  assert.equal(Number((await db.query(
    'select count(*)::int as n from public.book_snapshots where snapshot_id between 9001 and 9999'
  )).rows[0].n), 7, 'the refused prune deleted rows anyway');

  // And a committed prune with no expected count at all must refuse too: that
  // argument is the whole link between "uploaded" and "safe to delete".
  assert.equal((await db.query(
    'select prune_book_redundancy(3,false,$1,null) as v',['2026-06-01T00:00:00Z']
  )).rows[0].v.ok, false, 'a committed prune ran without a verified row count');

  const done = (await db.query(
    'select prune_book_redundancy(3,false,$1,$2) as v',
    ['2026-06-01T00:00:00Z', firstAsk.length])).rows[0].v;
  assert.equal(done.ok, true, 'the prune refused a count it had just produced itself');
  assert.equal(Number(done.deleted), firstAsk.length);

  assert.deepEqual(
    (await db.query(`select snapshot_id from public.book_snapshots
       where snapshot_id between 9001 and 9999 order by snapshot_id`)).rows.map(r=>Number(r.snapshot_id)),
    [9002,9003,9005,9006,9007],
    'the prune kept a different set than the view said it would delete');

  // THE BACKTEST WINDOW MUST NOT HAVE MOVED. This is the reason the prune is
  // written this way rather than by age, and the only way to see it is to
  // count both sides.
  assert.equal(Number((await db.query(survivingDays)).rows[0].n), daysBefore,
    'a band-day lost its last row, so min(observed_at) and the backtest window moved');
  assert.deepEqual((await db.query(
    'select min(observed_at) as m from public.book_snapshots')).rows[0].m, floorBefore,
    'the oldest snapshot on the desk was pruned, which shortens every backtest');
  assert.equal(Number(done.band_days_after), Number(done.band_days_before),
    'the function reported losing band-days and committed anyway');

  // Running it again finds nothing: what survives is not redundant.
  assert.equal(Number((await db.query(
    'select prune_book_redundancy(3,false,$1,0) as v',['2026-06-01T00:00:00Z']
  )).rows[0].v.deleted), 0, 'a second prune of the same window still found rows to delete');

  // AND IT KEEPS THE FULL-RESOLUTION WINDOW. p_before is what protects the
  // monitor's chart, so the same redundant pair must be untouchable inside
  // the window and prunable outside it. Asserting only the first half would
  // pass just as well if the pair were not redundant at all.
  await db.exec(`insert into public.book_snapshots(snapshot_id,band_id,observed_at) values
     (9101,'${pruneBand}', timestamptz '2026-08-01 08:00:00+00'),
     (9102,'${pruneBand}', timestamptz '2026-08-01 09:00:00+00');`);
  const offered = async (before) => {
    const v = (await db.query('select prune_book_redundancy(3,true,$1,null) as v',[before])).rows[0].v;
    // The zero case returns early and says `deleted`; the dry run says
    // `would_delete`. Both mean "this many rows would go".
    return Number(v.would_delete ?? v.deleted);
  };
  assert.equal(await offered('2026-06-01T00:00:00Z'), 0,
    'a row inside the full-resolution window was offered up for deletion');
  assert.equal(await offered('2026-09-01T00:00:00Z'), 1,
    'the same pair was not prunable once outside the window either, so the previous '
    +'assertion proved nothing about what the window protects');

  // ==========================================================================
  // A STRATEGY'S RECORD MUST NOT DEPEND ON WHICH DESK SUBSCRIBED TO IT.
  //
  // mark_signals_to_settlement scores every settled ENTER signal at the price
  // that justified IT - no account, no cash, no approval. The two conventions
  // are the whole reason this needs contracts: price_at_fire means a single
  // band's ask for s1/s3/s4/s5/s7 and for each LEG of s8/s9, but a
  // fee-inclusive SUM across legs for s2 and s6. One formula over both would
  // charge a five-band basket the outcome of one of its bands.
  // ==========================================================================
  const yesBand = '11111111-0000-4000-8000-000000000001';
  const noBand  = '11111111-0000-4000-8000-000000000002';
  const legHit  = '11111111-0000-4000-8000-000000000003';
  const legMiss = '11111111-0000-4000-8000-000000000004';
  const legOpen = '11111111-0000-4000-8000-000000000005';

  await db.exec(`
    insert into public.fact_band_outcome(band_id,settled_yes) values
      ('${yesBand}',true),('${noBand}',true),
      ('${legHit}',true),('${legMiss}',false);
    insert into public.strategies(strategy_id,name) values
      ('s_single','single leg'),('s_basket','summed basket'),('s_group','per-leg basket');

    -- 1 single leg, YES, band landed.      2 single leg, NO, band landed (lost).
    -- 3 summed basket, one leg landed.     4 summed basket, nothing landed.
    -- 5 summed basket with a leg that has not settled - unmarkable.
    -- 6 s8/s9 shape: band_ids AND basket_group, so it is a real single leg.
    insert into public.signals(signal_id,action,strategy_id,fired_at,payload,side,price_at_fire,status)
    values
      (7001,'ENTER','s_single',now(),'{}'::jsonb,'YES',0.30,'pending_approval'),
      (7002,'ENTER','s_single',now(),'{}'::jsonb,'NO' ,0.20,'pending_approval'),
      (7003,'ENTER','s_basket',now(),'{"band_ids":["${legHit}","${legMiss}"]}'::jsonb,'YES',0.62,'pending_approval'),
      (7004,'ENTER','s_basket',now(),'{"band_ids":["${legMiss}"]}'::jsonb,'YES',0.55,'pending_approval'),
      (7005,'ENTER','s_basket',now(),'{"band_ids":["${legHit}","${legOpen}"]}'::jsonb,'YES',0.40,'pending_approval'),
      (7006,'ENTER','s_group' ,now(),'{"band_ids":["${legHit}","${legMiss}"],"basket_group":"pair:x"}'::jsonb,'YES',0.35,'pending_approval');

    insert into public.fact_signal_outcome(signal_id,strategy_id,band_id,side,action,price_at_fire,settled_yes,signal_correct,filled)
    values
      (7001,'s_single','${yesBand}','YES','ENTER',0.30,true ,true ,false),
      (7002,'s_single','${noBand}' ,'NO' ,'ENTER',0.20,true ,false,false),
      (7003,'s_basket','${legHit}' ,'YES','ENTER',0.62,true ,true ,false),
      (7004,'s_basket','${legMiss}','YES','ENTER',0.55,false,false,false),
      (7005,'s_basket','${legHit}' ,'YES','ENTER',0.40,true ,true ,false),
      (7006,'s_group' ,'${legHit}' ,'YES','ENTER',0.35,true ,true ,false);
  `);

  // A VIEW, so there is nothing to backfill and nothing frozen: ad4_70 makes
  // these tables append-only and refused the UPDATE the first cut of this did.
  const marked = Number((await db.query(
    `select count(*) as n from public.v_signal_mark where strategy_id in ('s_single','s_basket','s_group')`
  )).rows[0].n);
  const mark = async id => (await db.query(
    'select * from public.v_signal_mark where signal_id=$1',[id])).rows[0] || {};

  // NOT ONE OF THESE SIX WAS FILLED. If the mark needed a desk, every
  // assertion below would be null and this contract would be vacuous.
  assert.equal(marked, 5, 'expected five markable signals - the sixth has an unsettled leg');

  const one = await mark(7001);
  assert.equal(one.mark_basis,'single_leg');
  assert.equal(one.mark_won,true);
  // 1 - 0.30 - (0.05 * 0.30 * 0.70)
  assert.equal(Number(one.mark_fee_per_share),0.0105,'the venue fee is shares x 0.05 x p x (1-p)');
  assert.equal(Number(one.mark_net_per_share),0.6895);

  const two = await mark(7002);
  assert.equal(two.mark_won,false,'a NO signal on a band that settled yes has lost');
  assert.equal(Number(two.mark_net_per_share),-0.208,'0 - 0.20 - (0.05 * 0.20 * 0.80)');

  // A COVER PAYS IF ANY LEG LANDS. Scored against its anchor band alone this
  // would read as one win and one loss; it is one win.
  const three = await mark(7003);
  assert.equal(three.mark_basis,'basket_fee_inclusive');
  assert.equal(three.mark_legs,2);
  assert.equal(three.mark_won,true,'a two-leg cover with one leg landing has won');
  assert.equal(Number(three.mark_fee_per_share),0,
    's2/s6 price_at_fire is already fee-inclusive, so charging the fee again double-counts it');
  assert.equal(Number(three.mark_net_per_share),0.38,'1 - 0.62, no second fee');

  const four = await mark(7004);
  assert.equal(four.mark_won,false);
  assert.equal(Number(four.mark_net_per_share),-0.55,'a basket where nothing landed loses its whole stake');

  assert.equal((await mark(7005)).mark_basis,undefined,
    'a basket with a leg that has not settled was marked anyway - its outcome is not known yet');

  // s8 and s9 emit one signal PER LEG at that leg's own ask, tied together by
  // basket_group. Treating those as a summed basket would drop the fee and
  // credit one leg with the pair's payout.
  const six = await mark(7006);
  assert.equal(six.mark_basis,'single_leg',
    'a per-leg signal carrying basket_group was treated as a summed basket');
  assert.equal(six.mark_legs,1);
  assert.equal(Number(six.mark_fee_per_share),0.011375,'0.05 * 0.35 * 0.65');

  // THE RECORD STAYS APPEND-ONLY. ad4_70 guards these tables and the mark must
  // not need an exemption: reading it writes nothing at all.
  // THE MARK MUST STAY OUT OF THE IMMUTABLE RECORD. ad4_70 makes these fact
  // tables append-only, and the first cut of this froze mark_* columns onto
  // fact_signal_outcome and backfilled them - the trigger refused the UPDATE,
  // correctly. Derived on read it cannot drift from its formula, it needs no
  // backfill, and correcting the formula corrects the history.
  assert.equal(Number((await db.query(
    `select count(*) as n from information_schema.views
      where table_schema='public' and table_name='v_signal_mark'`)).rows[0].n), 1,
    'v_signal_mark is not a view, so the mark is stored state that can go stale');
  assert.equal(Number((await db.query(
    `select count(*) as n from information_schema.columns
      where table_schema='public' and table_name='fact_signal_outcome'
        and column_name like 'mark\\_%'`)).rows[0].n), 0,
    'a derived mark was frozen into the append-only record');

  // AND THE BOARD REPORTS IT WITHOUT A SINGLE FILL.
  const board = (await db.query(
    `select strategy_id,settled_signals,correct_signals,hit_rate_pct,
            marked_signals,return_on_stake_pct,verdict,filled_all_time
       from v_strategy_board where strategy_id in ('s_single','s_basket','s_group')
      order by strategy_id`)).rows;
  const bySid = Object.fromEntries(board.map(r=>[r.strategy_id,r]));
  assert.equal(Number(bySid.s_single.filled_all_time),0,
    'the fixture filled one of these, so the next assertion would not prove desk-independence');
  assert.equal(Number(bySid.s_single.settled_signals),2,'the board cannot see signals no desk filled');
  assert.equal(Number(bySid.s_single.hit_rate_pct),50.0);
  // stake 0.30 + 0.20 = 0.50, net 0.6895 - 0.208 = 0.4815 -> 96.3%
  assert.equal(Number(bySid.s_single.return_on_stake_pct),96.3,
    'return on stake is what a hit rate cannot tell you');
  assert.match(bySid.s_single.verdict,/too few settled signals to judge/,
    'two signals is not a record, and the verdict must say so rather than quoting a rate');


  // ======================================================================
  // A RETIRED DESK STAYS RETIRED (plan v2 P0.3,
  // 20260923100000_a_retired_desk_stays_retired.sql).
  //
  // archived_at was a two-way door and nothing on the order path read it.
  // Retirement is one-way: the row is frozen, no order can be written for it
  // by any path, and queue_plan says so in plain words. Every desk here is
  // created here, so none of it depends on which desks happen to exist.
  // ======================================================================
  await db.exec('reset role; set role service_role;');
  const retiree=(await db.query(
    "select paper_desk_create('Retiree',300,'automatic',null,$1::jsonb) as id",
    [JSON.stringify({cities:['ALL'],strategies:['s1'],min_edge:.01,max_plan_usd:10,max_exposure_usd:50})])).rows[0].id;
  await assert.rejects(db.query('select paper_desk_retire($1,$2)',[retiree,'  ']),/needs a reason/,
    'a desk was retired with no reason on record');
  const retired=(await db.query("select paper_desk_retire($1,'plan v2: engine rebuild') as r",[retiree])).rows[0].r;
  assert.equal(retired.status,'retired');
  assert.equal(retired.already,false);
  assert.equal((await db.query("select paper_desk_retire($1,'again') as r",[retiree])).rows[0].r.already,true,
    'retiring twice must be a no-op, not a second retirement');
  const rrow=(await db.query('select * from public.paper_accounts where account_id=$1',[retiree])).rows[0];
  assert.equal(rrow.status,'retired');
  assert.equal(rrow.retired_reason,'plan v2: engine rebuild','the second call rewrote the reason');
  assert.ok(rrow.retired_at!==null && rrow.archived_at!==null,
    'a retired desk must also be archived, so every reader that hides archived desks hides it');
  assert.equal(rrow.entries_paused,true);
  assert.equal(Number((await db.query(
    "select count(*)::int as n from paper_activity where account_id=$1 and event_type='account_retired'",[retiree])).rows[0].n),1,
    'the retirement is a ledger event and must be written exactly once');
  await books(retiree,'after the desk was retired');

  // FROZEN: un-archiving, un-pausing, re-policying and moving cash are refused;
  // only the name may change.
  await assert.rejects(db.query('select paper_desk_archive($1,false)',[retiree]),/is retired/,
    'a retired desk was un-archived back into the desk list');
  await assert.rejects(db.query('select paper_desk_update($1,null,null,null,false)',[retiree]),/is retired/,
    'a retired desk was un-paused');
  await assert.rejects(db.query("select paper_desk_update($1,null,null,'manual')",[retiree]),/is retired/);
  await assert.rejects(db.query('select paper_desk_update($1,null,null,null,null,$2::jsonb)',
    [retiree,JSON.stringify({cities:['london'],strategies:['s1'],min_edge:.02,max_plan_usd:5})]),/is retired/,
    'a retired desk was re-policied');
  await db.exec('reset role;');
  await assert.rejects(db.query('update public.paper_accounts set cash=cash+1 where account_id=$1',[retiree]),/is retired/,
    'cash moved on a retired desk');
  await assert.rejects(db.query("update public.paper_accounts set status='active' where account_id=$1",[retiree]),/is retired/,
    'a retired desk was brought back by editing its status');
  await db.exec('set role service_role;');
  await db.query("select paper_desk_update($1,'Retiree (history)')",[retiree]);
  assert.equal((await db.query('select name from public.paper_accounts where account_id=$1',[retiree])).rows[0].name,
    'Retiree (history)','renaming a retired desk is the one change it allows');

  // NO ORDER, BY ANY PATH. queue_plan refuses first, in plain words; the
  // trigger catches a writer that never calls queue_plan.
  await db.exec(`reset role;update signals set fired_at=now() where signal_id=1;set role service_role;`);
  const retiredPlan=(await db.query('select publish_paper_plan($1,$2,1,$3,$4) as id',
    [retiree,'40000000-0000-0000-0000-00000000fe01',
     JSON.stringify([{band_id:band,side:'YES',shares:'2',limit_price:'.50',cash_ceiling:'1.05'}]),
     JSON.stringify({net_edge_per_share:'.10'})])).rows[0].id;
  await assert.rejects(db.query("select arbdesk_private.queue_plan($1,'assisted')",[retiredPlan]),/Desk retired/,
    'queue_plan queued a plan on a retired desk');
  await db.exec('reset role;');
  await assert.rejects(db.query(
    `insert into public.paper_orders(account_id,command_key,band_id,token_id,side,action,origin,strategy_id,
       shares,limit_price,cash_ceiling,policy_version,expires_at)
     values($1,gen_random_uuid(),$2,'yes','YES','BUY','manual','s1',1,.5,.5,1,now()+interval '5 minutes')`,
    [retiree,band]),/Desk retired/,'an order was written for a retired desk without going through queue_plan');
  assert.equal(Number((await db.query('select count(*)::int as n from paper_orders where account_id=$1',[retiree])).rows[0].n),0);

  // RETIREMENT NEVER STRANDS A POSITION. A desk holding shares must settle
  // or sell first; otherwise the frozen row could never be paid out.
  const holder=(await db.query("select paper_desk_create('Holder',100,'manual') as id")).rows[0].id;
  await db.query(`insert into public.paper_positions(account_id,band_id,side,shares,cost_basis,realized_pnl)
                  values($1,$2,'YES',5,2.5,0)`,[holder,band]);
  await db.exec('set role service_role;');
  await assert.rejects(db.query("select paper_desk_retire($1,'test')",[holder]),/1 open position/,
    'a desk holding shares was retired');
  await db.exec('reset role;');
  assert.equal((await db.query('select status from public.paper_accounts where account_id=$1',[holder])).rows[0].status,'active');
  await db.query('delete from public.paper_positions where account_id=$1',[holder]);

  // ONLY THE SERVICE ROLE RETIRES A DESK.
  for (const role of ['anon','authenticated']) {
    assert.equal((await db.query(
      `select has_function_privilege('${role}','public.paper_desk_retire(uuid,text)','execute') as ok`)).rows[0].ok,false,
      `${role} can retire a desk`);
  }

  // EVERY CHANGE TO A STRATEGY'S SWITCH IS ON RECORD, with who and why.
  await db.exec(`begin; set local arbdesk.change_reason='plan v2 P0.3';
    update public.strategies set enabled=false where strategy_id='s1'; commit;`);
  const hist=(await db.query(
    `select operation,reason,old_row->>'enabled' as was,new_row->>'enabled' as now
       from public.strategy_config_history where strategy_id='s1' order by history_id desc limit 1`)).rows[0];
  assert.deepEqual([hist.operation,hist.reason,hist.was,hist.now],['UPDATE','plan v2 P0.3','true','false'],
    'disabling a strategy left no record of what it was, or why');
  const histBefore=Number((await db.query('select count(*)::int as n from public.strategy_config_history')).rows[0].n);
  await db.query("update public.strategies set enabled=false where strategy_id='s1'");
  assert.equal(Number((await db.query('select count(*)::int as n from public.strategy_config_history')).rows[0].n),histBefore,
    'an update that changed nothing was recorded as a change');
  await db.query("update public.strategies set enabled=true where strategy_id='s1'");
  await db.exec('set role service_role;');
  // The same, through the RPC a script uses (a PATCH cannot carry a reason).
  await assert.rejects(db.query("select set_strategies_enabled(array['s1'],false,'')"),/needs a reason/);
  assert.equal((await db.query("select set_strategies_enabled(array['s1'],false,'rpc reason') as n")).rows[0].n,1);
  assert.equal((await db.query("select set_strategies_enabled(array['s1'],false,'rpc reason') as n")).rows[0].n,0,
    'switching off a strategy that is already off counted as a change');
  assert.equal((await db.query(
    "select reason from public.strategy_config_history where strategy_id='s1' order by history_id desc limit 1")).rows[0].reason,
    'rpc reason');
  await db.exec('reset role;');
  await db.query("update public.strategies set enabled=true where strategy_id='s1'");
  for (const role of ['anon','authenticated']) {
    assert.equal((await db.query(
      `select has_function_privilege('${role}','public.set_strategies_enabled(text[],boolean,text)','execute') as ok`)).rows[0].ok,false,
      `${role} can flip strategy switches through set_strategies_enabled`);
  }
  await db.exec('set role service_role;');
  await assert.rejects(db.query('delete from public.strategy_config_history'),/permission denied/);
  await assert.rejects(db.query("update public.strategy_config_history set reason='x'"),/permission denied/);
  await db.exec('reset role;');

  // ======================================================================
  // A BACKFILL IS NOT RESEARCH (plan v2 P1.4,
  // 20260923110000_a_backfill_is_not_research.sql).
  //
  // One UPDATE of a non-pricing column on 22 Sep copied 66,345 rows into
  // research_captures. A re-price is captured; a re-label is not; a new
  // price is not (P1.6: it is already in the table and the mirror); and a
  // deliberate backfill can switch capture off for its own transaction.
  // ======================================================================
  await db.exec('reset role;');
  const caps=async()=>Number((await db.query(
    "select count(*)::int as n from research_captures where source_relation='band_probabilities'")).rows[0].n);
  const c0=await caps();
  await db.query(`insert into public.band_probabilities(prob_id,band_id,raw_prob,calibrated_prob,centre_c,sigma_c)
                  values(60000002,$1,.30,.31,20.4,1.1)`,[band]);
  const c1=await caps();
  // P1.6 (20260924080000): a NEW price is not copied - it is in the table
  // and in the nightly mirror already. Only a revision in place is captured.
  assert.equal(c1,c0,'a new price was copied into research_captures a third time');
  // THE PLAN'S ACCEPTANCE: a non-pricing UPDATE in a transaction creates 0 captures.
  await db.exec(`begin; update public.band_probabilities set pricing_block_reason='relabel', regime_label='dry',
                   observed_floor_c=19.0 where prob_id=60000002; rollback;`);
  await db.query(`update public.band_probabilities set pricing_block_reason='relabel', regime_label='dry'
                  where prob_id=60000002`);
  assert.equal(await caps(),c1,'an update that changed no price was captured as research output');
  await db.query('update public.band_probabilities set calibrated_prob=calibrated_prob where prob_id=60000002');
  assert.equal(await caps(),c1,'an update that set a price to itself was captured');
  await db.query('update public.band_probabilities set calibrated_prob=.35 where prob_id=60000002');
  assert.equal(await caps(),c1+1,'a real re-price was NOT captured - research output was lost');
  // The switch: on for one transaction, gone after it.
  await db.exec(`begin; set local arbdesk.skip_capture = on;
                   update public.band_probabilities set calibrated_prob=.40 where prob_id=60000002;
                   insert into public.band_probabilities(prob_id,band_id,raw_prob) values(60000003,'${band}',.1);
                 commit;`);
  assert.equal(await caps(),c1+1,'a backfill that set arbdesk.skip_capture was captured anyway');
  await db.query('update public.band_probabilities set calibrated_prob=.41 where prob_id=60000002');
  assert.equal(await caps(),c1+2,'skip_capture leaked past the transaction that set it');

  // ======================================================================
  // NO SECURITY DEFINER FUNCTION IS EXECUTABLE BY PUBLIC (plan v2 P1.1,
  // 20260923120000_revoke_public_execute.sql). Checked AFTER every sql/ file
  // above has run, because those create functions after the migrations and a
  // new function is executable by PUBLIC unless something stops it.
  // ======================================================================
  await db.exec('reset role;');
  const publicX=(await db.query(`
    select p.oid::regprocedure::text as sig from pg_proc p join pg_namespace n on n.oid=p.pronamespace
     where n.nspname='public' and p.prosecdef
       and exists (select 1 from aclexplode(coalesce(p.proacl, acldefault('f', p.proowner))) a
                    where a.grantee=0 and a.privilege_type='EXECUTE')`)).rows.map(r=>r.sig);
  assert.deepEqual(publicX,[],`SECURITY DEFINER functions executable by PUBLIC: ${publicX.join(', ')}`);
  for (const sig of ['prune_trades(integer,boolean,timestamptz,bigint)',
                     'prune_exported_paper_trades(integer,uuid[],boolean)',
                     'paper_desk_reset(uuid)','paper_desk_archive(uuid,boolean)']) {
    for (const role of ['anon','authenticated']) {
      assert.equal((await db.query(`select has_function_privilege('${role}','public.${sig}','execute') as ok`)).rows[0].ok,false,
        `${role} can execute ${sig}`);
    }
    assert.equal((await db.query(`select has_function_privilege('service_role','public.${sig}','execute') as ok`)).rows[0].ok,true,
      `service_role lost ${sig}, which the archive and the board's server route call`);
  }
  // And a function created from here on gets no PUBLIC grant either.
  await db.exec(`create function public.zz_new_definer() returns int language sql security definer as 'select 1';`);
  assert.equal((await db.query("select has_function_privilege('anon','public.zz_new_definer()','execute') as ok")).rows[0].ok,false,
    'a newly created function is executable by anon again - the next migration would reopen this');
  await db.exec('drop function public.zz_new_definer();');

  // THE TRADES FLOOR HOLDS FOR p_before TOO. Before, p_before => now() was a
  // cutoff of now, and the 30-day check never looked at it.
  // Each trade ingested when it traded, so the mirror's bound is not what
  // decides here.
  await db.exec(`insert into public.trades_observed(trade_id,city_key,traded_at,ingested_at) values
      (900001,'london',now()-interval '40 days',now()-interval '40 days'),
      (900002,'london',now()-interval '2 days',now()-interval '2 days'),
      (900003,'london',now()-interval '1 hour',now()-interval '1 hour');
    insert into public.archive_daily_city_presence(dataset,day,city_key)
      select 'Trades seen',(traded_at at time zone 'UTC')::date,city_key from public.trades_observed
      on conflict do nothing;`);
  const observedBefore=(await db.query('select count(*)::int as n from public.trades_observed')).rows[0].n;
  const older=(await db.query("select count(*)::int as n from public.trades_observed where traded_at < now()-interval '30 days'")).rows[0].n;
  const dry=(await db.query('select prune_trades(30,true,now()) as r')).rows[0].r;
  assert.equal(dry.ok,true);
  assert.equal(Number(dry.would_delete),older,
    `p_before => now() reached past the 30-day floor: would delete ${dry.would_delete}, only ${older} are older than 30 days`);
  const wet=(await db.query(`select prune_trades(30,false,now(),${observedBefore}) as r`)).rows[0].r;
  assert.equal(wet.ok,false,'a committed prune with p_before => now() went through');
  assert.equal((await db.query('select count(*)::int as n from public.trades_observed')).rows[0].n,observedBefore,
    'rows inside the 30-day window were deleted');
  const legit=(await db.query("select prune_trades(30,true,now()-interval '31 days') as r")).rows[0].r;
  assert.equal(legit.ok,true,'a cutoff older than the floor must still be honoured');
  assert.equal(new Date(legit.older_than).getTime()<Date.now()-30*86400e3,true);

  // ======================================================================
  // ONE BUY PER BAND AND SIDE (plan v2 P5.0 item 1,
  // 20260924050000_one_buy_per_band.sql). Live on 24 Sep, 14 of 53
  // (desk, band, side) keys had been bought more than once - up to 5 times -
  // because a signal that fires again publishes a new plan and nothing asked
  // whether the desk already held or was already buying that band.
  // ======================================================================
  await db.exec('reset role; set role service_role;');
  const dd=(await db.query(
    "select paper_desk_create('Dedupe',300,'automatic',null,$1::jsonb) as id",
    [JSON.stringify({cities:['ALL'],strategies:['s1'],min_edge:.01,max_plan_usd:10,max_exposure_usd:50})])).rows[0].id;
  await db.exec('reset role; update public.paper_accounts set entries_paused=false where account_id=\''+dd+'\'; update signals set fired_at=now() where signal_id=1; set role service_role;');
  let ddN=0;
  const ddPlan=async(side)=>{
    const id=(await db.query('select publish_paper_plan($1,$2,1,$3,$4) as id',
      [dd,`60000000-0000-0000-0000-${String(++ddN).padStart(12,'0')}`,
       JSON.stringify([{band_id:band,side,shares:'2',limit_price:'.50',cash_ceiling:'1.05'}]),
       JSON.stringify({net_edge_per_share:'.10'})])).rows[0].id;
    return (await db.query('select status,reason from paper_trade_plans where plan_id=$1',[id])).rows[0];
  };
  const ddOrders=async()=>(await db.query(
    "select count(*)::int as n from paper_orders where account_id=$1 and status in ('queued','working')",[dd])).rows[0].n;
  const ddReserved=async()=>Number((await db.query('select reserved_cash from paper_accounts where account_id=$1',[dd])).rows[0].reserved_cash);

  assert.equal((await ddPlan('YES')).status,'queued','the first YES buy on a band must queue');
  const reservedAfterFirst=await ddReserved();
  const ddAgain=await ddPlan('YES');
  assert.equal(ddAgain.status,'blocked','a second YES buy was queued while the first order was still live');
  assert.match(ddAgain.reason,/Already holding or ordering YES on this band/);
  assert.equal(await ddOrders(),1,'the refused plan still wrote an order');
  assert.equal(await ddReserved(),reservedAfterFirst,'the refused plan still reserved cash');
  assert.equal((await ddPlan('NO')).status,'queued','the other side of the band is a different key and must still queue');

  // A finished order is not a live one; a held position is.
  await db.exec(`reset role; update public.paper_orders set status='expired' where account_id='${dd}'; set role service_role;`);
  assert.equal((await ddPlan('YES')).status,'queued','after its order ended, the band must be buyable again');
  await db.exec(`reset role; update public.paper_orders set status='expired' where account_id='${dd}';
    insert into public.paper_positions(account_id,band_id,side,shares,cost_basis,realized_pnl) values('${dd}','${band}','YES',3,1.5,0);
    set role service_role;`);
  const ddHeld=await ddPlan('YES');
  assert.equal(ddHeld.status,'blocked','a YES buy was queued on a band the desk already holds');
  assert.match(ddHeld.reason,/Already holding or ordering YES/);
  await db.exec(`reset role; update public.paper_positions set shares=0,cost_basis=0 where account_id='${dd}'; set role service_role;`);
  assert.equal((await ddPlan('YES')).status,'queued','a flat position (0 shares) is not a holding');

  // The assisted path gets the refusal back in plain words.
  await db.exec(`reset role; update public.paper_accounts set mode='assisted' where account_id='${dd}'; set role service_role;`);
  const assistedPlan=(await db.query('select publish_paper_plan($1,$2,1,$3,$4) as id',
    [dd,'60000000-0000-0000-0000-0000000000ff',
     JSON.stringify([{band_id:band,side:'YES',shares:'2',limit_price:'.50',cash_ceiling:'1.05'}]),
     JSON.stringify({net_edge_per_share:'.10'})])).rows[0].id;
  await assert.rejects(db.query("select arbdesk_private.queue_plan($1,'assisted')",[assistedPlan]),
    /Already holding or ordering YES on this band/,'an approval queued a second YES buy on a live band');
  await db.exec('reset role;');

  // ======================================================================
  // EVERY STRATEGY HAS ITS OWN LEDGER (plan v2 P5.1,
  // 20260924090000_every_strategy_has_its_own_ledger.sql). One shadow ledger
  // per registered strategy, opened by the registration itself, trading only
  // that strategy at the standard notional; one portfolio account, suspended,
  // which cannot go active until Hassan writes its bankroll (rule 6).
  // ======================================================================
  await db.exec('reset role;');
  const ledgerOf=async(sid)=>(await db.query(
    "select * from paper_accounts where kind='shadow' and strategy_id=$1 and status<>'retired'",[sid])).rows;
  // The fixture registered s1 before the migrations ran: the backfill opened it.
  const [l1]=await ledgerOf('s1');
  assert.ok(l1,'the strategy registered before the migration has no shadow ledger');
  assert.equal(l1.status,'active'); assert.equal(l1.mode,'automatic');
  assert.equal(l1.entries_paused,false,'a shadow ledger that is paused collects no evidence');
  assert.equal(Number(l1.bankroll_usd),1000,'the standard notional is $1,000 by default');
  assert.equal(Number(l1.cash),1000); assert.equal(Number(l1.starting_cash),1000);
  assert.deepEqual(l1.policy.strategies,['s1'],'a shadow ledger trades its own strategy and nothing else');
  assert.equal(Number(l1.policy.max_plan_usd),1000);
  assert.equal(Number((await db.query(
    "select sum(cash_delta) as s from paper_activity where account_id=$1",[l1.account_id])).rows[0].s),1000,
    'the opening cash is not on the activity ledger, so the desk integrity check could never balance');
  assert.equal((await db.query("select arbdesk_private.open_shadow_ledger('s1') as id")).rows[0].id,
    l1.account_id,'opening a ledger twice made a second one');

  // Registered means opened - and 'system', the name alerts are filed under, is not a strategy.
  await db.exec("insert into public.strategies(strategy_id,name) values('s_ledger_a','ledger test'),('system','alerts') on conflict do nothing;");
  assert.equal((await ledgerOf('s_ledger_a')).length,1,'registering a strategy did not open its shadow ledger');
  assert.equal((await ledgerOf('system')).length,0,'the system pseudo-strategy was given a ledger');
  await assert.rejects(db.query(
    "insert into paper_accounts(name,kind,strategy_id,bankroll_usd,starting_cash,cash,access_mode) values('dup','shadow','s_ledger_a',1000,1000,1000,'single_desk')"),
    /paper_accounts_one_shadow_per_strategy/,'a strategy got two live shadow ledgers');
  await assert.rejects(db.query(
    "insert into paper_accounts(name,kind,starting_cash,cash,access_mode,bankroll_usd) values('orphan','shadow',1000,1000,'single_desk',1000)"),
    /paper_accounts_shadow_has_a_strategy/,'a shadow ledger with no strategy was accepted');
  await assert.rejects(db.query(
    "insert into paper_accounts(name,strategy_id,starting_cash,cash,access_mode) values('plain','s1',1000,1000,'single_desk')"),
    /paper_accounts_shadow_has_a_strategy/,'a strategy was attached to an account that is not its shadow ledger');
  await assert.rejects(db.query('update paper_accounts set strategy_id=$2 where account_id=$1',[l1.account_id,'s_ledger_a']),
    /kind and strategy are fixed/,'a shadow ledger was re-pointed at another strategy');

  // The portfolio account: one, suspended, manual, paused, no bankroll.
  const pf=(await db.query("select * from paper_accounts where kind='portfolio'")).rows;
  assert.equal(pf.length,1,'there must be exactly one portfolio account');
  assert.equal(pf[0].status,'suspended'); assert.equal(pf[0].mode,'manual'); assert.equal(pf[0].entries_paused,true);
  assert.equal(pf[0].bankroll_usd,null,'the portfolio bankroll is Hassan\'s decision, not a migration default');
  await assert.rejects(db.query("update paper_accounts set status='active' where account_id=$1",[pf[0].account_id]),
    /paper_accounts_portfolio_needs_a_bankroll/,'the portfolio account went active with no bankroll');
  await assert.rejects(db.query(
    "insert into paper_accounts(name,kind,starting_cash,cash,access_mode,status) values('second','portfolio',1,1,'single_desk','suspended')"),
    /paper_accounts_one_portfolio/,'a second portfolio account was accepted');

  // The order path: a ledger queues its own strategy, refuses another's even
  // when its policy is edited to allow it, and a suspended ledger buys nothing.
  await db.exec(`insert into signals(signal_id,action,strategy_id,fired_at,reason) values
      (5101,'ENTER','s1',now(),'own strategy'),(5102,'ENTER','s_ledger_a',now(),'another strategy'),
      (5103,'ENTER','s1',now(),'while suspended');
    set role service_role;`);
  const lPlan=async(acct,cmd,sig,side)=>{
    const id=(await db.query('select publish_paper_plan($1,$2,$3,$4,$5) as id',
      [acct,cmd,sig,JSON.stringify([{band_id:band,side,shares:'2',limit_price:'.50',cash_ceiling:'1.05'}]),
       JSON.stringify({net_edge_per_share:'.10'})])).rows[0].id;
    return (await db.query('select status,reason from paper_trade_plans where plan_id=$1',[id])).rows[0];
  };
  assert.equal((await lPlan(l1.account_id,'70000000-0000-0000-0000-000000000001',5101,'YES')).status,'queued',
    'a shadow ledger could not trade its own strategy');
  await db.exec(`reset role; update paper_accounts set policy=policy||'{"strategies":["s1","s_ledger_a"]}'::jsonb where account_id='${l1.account_id}'; set role service_role;`);
  const cross=await lPlan(l1.account_id,'70000000-0000-0000-0000-000000000002',5102,'NO');
  assert.equal(cross.status,'blocked','a shadow ledger traded another strategy because its policy listed it');
  assert.match(cross.reason,/Shadow ledger of s1 trades only that strategy/);
  await db.exec(`reset role; update paper_accounts set status='suspended' where account_id='${l1.account_id}'; set role service_role;`);
  const susp=await lPlan(l1.account_id,'70000000-0000-0000-0000-000000000003',5103,'NO');
  assert.equal(susp.status,'blocked','a suspended ledger queued an entry');
  assert.match(susp.reason,/Desk suspended/);
  // Whoever writes it, no BUY order lands on a suspended desk ...
  await db.exec('reset role;');
  await assert.rejects(db.query(
    "insert into paper_orders(account_id,command_key,band_id,token_id,side,action,origin,shares,limit_price,cash_ceiling,policy_version,expires_at) "+
    "select $1,gen_random_uuid(),band_id,token_no,'NO','BUY','manual',1,.5,.5,1,now()+interval '5 minutes' from bands where band_id=$2",
    [l1.account_id,band]),/Desk suspended/,'a BUY order was written straight into a suspended desk');
  // ... but it can always sell what it holds.
  await db.exec(`insert into paper_positions(account_id,band_id,side,shares,cost_basis,realized_pnl) values('${l1.account_id}','${band}','NO',3,1.5,0);
    set role service_role;`);
  assert.ok((await db.query("select submit_single_paper_exit($1,'70000000-0000-0000-0000-000000000004',$2,'NO',3,.40) as id",
    [l1.account_id,band])).rows[0].id,'a suspended desk could not sell what it holds');
  await db.exec('reset role;');

  // ======================================================================
  // STRATEGY LIFECYCLE STATES (plan v2 P5.2,
  // 20260924100000_strategy_lifecycle.sql). One state per strategy; enabled is
  // derived from it both ways; only allowed transitions; the portfolio only by
  // a named approval; every change on an append-only record.
  // ======================================================================
  await db.exec('reset role;');
  const stateOf=async(sid)=>(await db.query('select state from strategy_state where strategy_id=$1',[sid])).rows[0]?.state;

  // THE ENGINE'S STRATEGIES ARE REGISTERED, NOT TRADING (P5.12 part 3a,
  // 20260927150000_engine_strategies_are_registered.sql): each is disabled, in
  // research, with its own shadow ledger, and a re-run changes nothing.
  const engineIds=['s10_winner','s10_growth','s10_lock','s11_ladder','s11_lock','s12_no'];
  const engineRows=async()=>(await db.query(`select s.strategy_id,s.enabled,st.state,
      (select count(*)::int from paper_accounts a where a.kind='shadow' and a.strategy_id=s.strategy_id) ledgers
      from strategies s left join strategy_state st using(strategy_id)
      where s.strategy_id = any($1) order by 1`,[engineIds])).rows;
  const engineBefore=await engineRows();
  assert.deepEqual(engineBefore.map(r=>r.strategy_id),[...engineIds].sort(),'every engine strategy is registered');
  for (const r of engineBefore) {
    assert.equal(r.enabled,false,`${r.strategy_id} must not trade yet`);
    assert.equal(r.state,'research',`${r.strategy_id} starts in research`);
    assert.equal(r.ledgers,1,`${r.strategy_id} has exactly one shadow ledger`);
  }
  await db.exec(fs.readFileSync(path.join(directory,'20260927150000_engine_strategies_are_registered.sql'),'utf8'));
  assert.deepEqual(await engineRows(),engineBefore,'re-running the registration changes nothing');

  // THE ENGINE'S ORDERS REACH ITS LEDGERS (P5.12 part 3b,
  // 20260927210000_engine_orders_reach_the_ledgers.sql): a BUY decision of a
  // switched-on strategy becomes a plan on its own shadow ledger through
  // queue_plan; nothing else does; the ledger's own claim fills it; the
  // position names the decision; the engine ledgers take no automatic exits.
  const engAcct=async(sid)=>(await db.query(
    "select account_id,policy,policy_version from paper_accounts where kind='shadow' and strategy_id=$1",[sid])).rows[0];
  for (const sid of engineIds) {
    const a=await engAcct(sid);
    assert.equal(a.policy.auto_exit_enabled,false,`${sid}: the engine exits only by its own rules`);
    assert.equal(Number(a.policy_version),2,`${sid}: the policy change is versioned once`);
  }
  assert.equal((await db.query("select count(*)::int n from paper_activity where event_type='policy_changed' and payload->>'why' like 'plan v2 P5.12 part 3b%'")).rows[0].n,
    engineIds.length,'each ledger logs the change');
  assert.equal((await db.query("select data_type from information_schema.columns where table_name='paper_positions' and column_name='entry_decision_id'")).rows[0].data_type,
    'bigint','a position names its decision by the decisions key');
  const winner=await engAcct('s10_winner');
  const decide=async(sid,action,ago='0 minutes')=>(await db.query(
    `insert into decisions(run_id,decided_at,strategy_id,city_key,resolution_date,action,reason_code,params_version)
     values(gen_random_uuid(),now()-$2::interval,$1,'london',current_date,$3,'enter','{"engine":"engine-v1"}') returning decision_id`,
    [sid,ago,action])).rows[0].decision_id;
  const engLegs=JSON.stringify([{band_id:band,side:'YES',shares:'12',limit_price:'.50',cash_ceiling:'6.17'}]);
  const engEvidence=JSON.stringify({source:'engine',net_edge_per_share:'.08',legs_p:{[`${band}:YES`]:{p:0.62}}});
  const publishEngine=(acct,dec)=>db.query('select publish_engine_plan($1,$2,$3,$4) as id',[acct,dec,engLegs,engEvidence]);
  // Research: nothing is ordered, whatever the decision says.
  const dResearch=await decide('s10_winner','BUY');
  await db.exec('set role service_role;');
  await assert.rejects(publishEngine(winner.account_id,dResearch),/Strategy s10_winner is not switched on/);
  await db.exec('reset role;');
  await db.query("select set_strategy_state('s10_winner','shadow','P5.12 part 3b contract')");
  const dBuy=await decide('s10_winner','BUY');
  const dWait=await decide('s10_winner','WAIT');
  const dOld=await decide('s10_winner','BUY','20 minutes');
  const dOther=await decide('s11_ladder','BUY');
  await db.exec('set role service_role;');
  await assert.rejects(publishEngine(winner.account_id,dWait),/An engine BUY decision is required/);
  await assert.rejects(publishEngine(winner.account_id,dOld),/Decision stale or future/);
  await assert.rejects(publishEngine(winner.account_id,dOther),/A decision of s11_ladder on the ledger of s10_winner/);
  await assert.rejects(publishEngine(l1.account_id,dBuy),/Engine orders go to a shadow ledger only|A decision of/);
  const engPlan=(await publishEngine(winner.account_id,dBuy)).rows[0].id;
  assert.equal((await publishEngine(winner.account_id,dBuy)).rows[0].id,engPlan,'one plan per decision and ledger');
  const ep=(await db.query('select status,signal_id,evidence from paper_trade_plans where plan_id=$1',[engPlan])).rows[0];
  assert.equal(ep.status,'queued','queue_plan took the engine plan');
  assert.equal(ep.signal_id,null,'an engine plan names its decision, not a signal');
  assert.equal(Number(ep.evidence.decision_id),Number(dBuy));
  // Only the ledger's own claim hands it out, and one lease at a time.
  await db.exec(`reset role;
    insert into paper_orders(account_id,command_key,band_id,token_id,side,action,origin,shares,limit_price,cash_ceiling,policy_version,expires_at)
    select account_id,gen_random_uuid(),'${band}','no','NO','BUY','manual',1,.5,.6,policy_version,now()+interval '5 minutes'
      from paper_accounts where kind='shadow' and strategy_id='s12_no';
    update paper_accounts set reserved_cash=reserved_cash+.6 where kind='shadow' and strategy_id='s12_no';
    set role service_role;`);
  const engJob=(await db.query('select claim_account_order($1) as job',[winner.account_id])).rows[0].job;
  assert.equal(engJob.plan_id,engPlan,'the ledger claims its own order');
  assert.equal(engJob.account_id,winner.account_id);
  assert.equal((await db.query('select claim_account_order($1) as job',[winner.account_id])).rows[0].job,null,
    'one working order per ledger');
  await db.query(`insert into paper_book_evidence(snapshot_id,token_id,observed_at,payload) values('engine-snap',$1,now(),'{}')`,
    [engJob.token_id]);
  await db.query('select complete_paper_order($1,$2,$3::jsonb)',[engJob.order_id,engJob.lease_token,JSON.stringify(
    {status:'filled',reason:null,shares:'12',notional:'6',fee:'.15',snapshot_id:'engine-snap',
     fills:[{shares:'12',price:'.50',notional:'6',fee:'.15'}]})]);
  const engPos=(await db.query('select entry_decision_id,params_version,p_at_entry,strategy_id,group_id from paper_positions where account_id=$1',
    [winner.account_id])).rows[0];
  assert.equal(Number(engPos.entry_decision_id),Number(dBuy),'the position names the decision that opened it');
  assert.equal(engPos.params_version,'{"engine":"engine-v1"}','and the parameter versions it used');
  assert.equal(Number(engPos.p_at_entry),0.62,'and the probability the engine believed');
  assert.equal(engPos.strategy_id,'s10_winner'); assert.equal(engPos.group_id,engPlan);
  await db.exec('reset role;set role anon;');
  await assert.rejects(db.query('select claim_account_order($1)',[winner.account_id]),/permission denied/);
  await assert.rejects(db.query('select publish_engine_plan($1,$2,$3,$4)',[winner.account_id,dBuy,engLegs,engEvidence]),/permission denied/);
  await db.exec('reset role;');
  const engPolicyBefore=await Promise.all(engineIds.map(engAcct));
  await db.exec(fs.readFileSync(path.join(directory,'20260927210000_engine_orders_reach_the_ledgers.sql'),'utf8'));
  assert.deepEqual(await Promise.all(engineIds.map(engAcct)),engPolicyBefore,'a re-run changes no ledger');
  assert.equal(Number((await db.query('select entry_decision_id from paper_positions where account_id=$1',[winner.account_id])).rows[0].entry_decision_id),
    Number(dBuy),'a re-run keeps the lineage');

  // THE ENGINE EXITS BY ITS OWN RULES (P5.12 part 3b step 3,
  // 20260927220000_the_engine_exits_by_its_own_rules.sql): a SELL or SWITCH
  // decided by the ledger's own strategy sells what the ledger holds, as an
  // automatic order naming the decision and never more than is left; its
  // trade closes as engine_exit; a SWITCH's buy half is a plan like a BUY's.
  // The re-run just above put back step 2's publish_engine_plan; live, this
  // file applies after that one, so it does here too.
  const exitsFile=fs.readFileSync(path.join(directory,'20260927220000_the_engine_exits_by_its_own_rules.sql'),'utf8');
  await db.exec(exitsFile);
  const engExit=(acct,dec,shares,limit=.70)=>db.query("select submit_engine_exit($1,$2,$3,'YES',$4,$5) as id",
    [acct,dec,band,shares,limit]);
  const dSell=await decide('s10_winner','SELL');
  const dSwitch=await decide('s10_winner','SWITCH');
  const dSellOld=await decide('s10_winner','SELL','20 minutes');
  const dSellOther=await decide('s11_ladder','SELL');
  await db.exec('set role service_role;');
  await assert.rejects(engExit(winner.account_id,dBuy,5),/An engine SELL or SWITCH decision is required/);
  await assert.rejects(engExit(winner.account_id,dSellOld,5),/Decision stale or future/);
  await assert.rejects(engExit(winner.account_id,dSellOther,5),/A decision of s11_ladder on the ledger of s10_winner/);
  await assert.rejects(engExit(winner.account_id,dSell,13),/Shares already sold or reserved for exit/,
    'never more than the ledger holds');
  await assert.rejects(engExit(l1.account_id,dSell,5),/Engine orders go to a shadow ledger only|A decision of/);
  const xOrder=(await engExit(winner.account_id,dSell,5)).rows[0].id;
  assert.equal((await engExit(winner.account_id,dSell,5)).rows[0].id,xOrder,'one exit per decision, band and side');
  await assert.rejects(engExit(winner.account_id,dSwitch,8),/Shares already sold or reserved for exit/,
    'what is already on its way out is not sold twice');
  const xo=(await db.query(`select action,origin,strategy_id,status,shares,limit_price,cash_ceiling,context,
      extract(epoch from expires_at-requested_at)::int as life from paper_orders where order_id=$1`,[xOrder])).rows[0];
  assert.deepEqual([xo.action,xo.origin,xo.strategy_id,xo.status,Number(xo.shares),Number(xo.limit_price),
    Number(xo.cash_ceiling),xo.life],['SELL','automatic','s10_winner','queued',5,.7,0,300]);
  assert.equal(Number(xo.context.decision_id),Number(dSell),'the exit names its decision');
  assert.equal(xo.context.action,'SELL');
  const xJob=(await db.query('select claim_account_order($1) as job',[winner.account_id])).rows[0].job;
  assert.equal(xJob.order_id,xOrder,'the ledger claims its own exit');
  await db.query(`insert into paper_book_evidence(snapshot_id,token_id,observed_at,payload) values('engine-exit-snap',$1,now(),'{}')`,
    [xJob.token_id]);
  await db.query('select complete_paper_order($1,$2,$3::jsonb)',[xOrder,xJob.lease_token,JSON.stringify(
    {status:'filled',reason:null,shares:'5',notional:'3.5',fee:'.0525',snapshot_id:'engine-exit-snap',
     fills:[{shares:'5',price:'.70',notional:'3.5',fee:'.0525'}]})]);
  // 12 bought for 6 plus .15 fee; 5 sold for 3.50 less .0525: basis 6.15 x 5/12.
  const xPos=(await db.query('select shares,cost_basis,realized_pnl,entry_decision_id from paper_positions where account_id=$1',
    [winner.account_id])).rows[0];
  assert.deepEqual([Number(xPos.shares),Number(xPos.cost_basis),Number(xPos.realized_pnl)],[7,3.5875,.885]);
  assert.equal(Number(xPos.entry_decision_id),Number(dBuy),'what is left still names the decision that opened it');
  const xTrades=(await db.query(
    'select shares,close_reason from paper_trades where account_id=$1 order by closed_at nulls last',[winner.account_id])).rows;
  assert.deepEqual(xTrades.map(t=>[Number(t.shares),t.close_reason]),[[5,'engine_exit'],[7,null]],
    'the shares the engine sold close as its exit; the rest stay open');
  assert.ok((await db.query("select count(*)::int n from paper_trades where account_id=$1 and close_reason='auto_exit'",
    [account])).rows[0].n>0,'every other SELL still closes as auto_exit');
  // The SWITCH: the rest goes, and the buy half is a plan on the same gate.
  const sOrder=(await engExit(winner.account_id,dSwitch,7,.40)).rows[0].id;
  assert.equal((await db.query('select context from paper_orders where order_id=$1',[sOrder])).rows[0].context.action,'SWITCH');
  const sPlan=(await publishEngine(winner.account_id,dSwitch)).rows[0].id;
  const sp=(await db.query('select status,reason,evidence from paper_trade_plans where plan_id=$1',[sPlan])).rows[0];
  assert.equal(Number(sp.evidence.decision_id),Number(dSwitch),'a SWITCH decision publishes its buy half');
  assert.equal(sp.status,'blocked','queue_plan still judges it: the band is still held and its exit still working');
  assert.match(sp.reason,/Already holding or ordering YES on this band/);
  await db.exec('reset role;set role anon;');
  await assert.rejects(db.query("select submit_engine_exit($1,$2,$3,'YES',1,.5)",[winner.account_id,dSwitch,band]),
    /permission denied/);
  await db.exec('reset role;');
  const xBefore=(await db.query('select count(*)::int n from paper_orders where account_id=$1',[winner.account_id])).rows[0].n;
  await db.exec(exitsFile);
  assert.equal((await db.query('select count(*)::int n from paper_orders where account_id=$1',[winner.account_id])).rows[0].n,
    xBefore,'a re-run changes nothing');
  const enabledOf=async(sid)=>(await db.query('select enabled from strategies where strategy_id=$1',[sid])).rows[0].enabled;
  await db.exec("insert into public.strategies(strategy_id,name,enabled) values('s_life','lifecycle test',false);");
  assert.equal(await stateOf('s_life'),'research','a registered, switched-off strategy starts in research');
  assert.equal(await stateOf('system'),undefined,'the alert channel is not a strategy and has no state');

  // enabled follows the state, and a direct flip of enabled moves the state.
  await db.query("select set_strategy_state('s_life','shadow','first shadow run')");
  assert.equal(await enabledOf('s_life'),true,'shadow must switch enabled on');
  await db.query("update public.strategies set enabled=false where strategy_id='s_life'");
  assert.equal(await stateOf('s_life'),'research','switching enabled off must move the state to research');
  assert.equal((await db.query("select set_strategies_enabled(array['s_life'],true,'switched on by reason') as n")).rows[0].n,1);
  assert.equal(await stateOf('s_life'),'shadow');
  assert.equal((await db.query("select reason from strategy_state where strategy_id='s_life'")).rows[0].reason,
    'switched on by reason','the reason given to the old switch must reach the state');

  // Only allowed transitions.
  await db.query("select set_strategy_state('s_life','suspended','losing record')");
  assert.equal(await enabledOf('s_life'),false,'a suspended strategy makes no live decisions');
  await db.query("select set_strategy_state('s_life','shadow','re-test after 14 days')");
  await db.query("select set_strategy_state('s_life','research','back to replay')");
  await assert.rejects(db.query("select set_strategy_state('s_life','suspended','no')"),
    /research -> suspended is not an allowed transition/);
  await assert.rejects(db.query("select set_strategy_state('s_life','shadow','')"),/needs a reason/);

  // The portfolio is Hassan's decision.
  await db.query("select set_strategy_state('s_life','shadow','again')");
  await assert.rejects(db.query("select set_strategy_state('s_life','portfolio','allocate it')"),/promote_strategy_to_portfolio/);
  await assert.rejects(db.query("update strategy_state set state='portfolio', reason='sneak' where strategy_id='s_life'"),
    /Hassan's decision/,'a direct update put a strategy in the portfolio');
  await assert.rejects(db.query("select promote_strategy_to_portfolio('s_life','','earned it')"),/names who approved it/);
  await db.query("select promote_strategy_to_portfolio('s_life','Hassan','P5.10 report says it earned it')");
  assert.equal(await stateOf('s_life'),'portfolio'); assert.equal(await enabledOf('s_life'),true);
  const approved=(await db.query("select changed_by from strategy_state_history where strategy_id='s_life' and to_state='portfolio'")).rows;
  assert.deepEqual(approved.map(r=>r.changed_by),['Hassan'],'the promotion must record who approved it');

  // Retired is terminal; the record is complete and append-only.
  await db.query("select set_strategy_state('s_life','retired','replaced by S10')");
  await assert.rejects(db.query("select set_strategy_state('s_life','shadow','bring it back')"),/retired -> shadow/);
  await assert.rejects(db.query("update public.strategies set enabled=true where strategy_id='s_life'"),/retired -> shadow/,
    'the old switch brought a retired strategy back');
  const lifeHist=(await db.query("select from_state,to_state from strategy_state_history where strategy_id='s_life' order by history_id")).rows
    .map(r=>`${r.from_state??'-'}>${r.to_state}`);
  assert.deepEqual(lifeHist,['->research','research>shadow','shadow>research','research>shadow','shadow>suspended',
    'suspended>shadow','shadow>research','research>shadow','shadow>portfolio','portfolio>retired']);
  await assert.rejects(db.query("delete from strategy_state_history where strategy_id='s_life'"),/append-only/);
  await assert.rejects(db.query("update strategy_state_history set reason='x' where strategy_id='s_life'"),/append-only/);

  // Only the service role changes a state.
  for (const role of ['anon','authenticated']) {
    for (const fn of ['public.set_strategy_state(text,text,text)','public.promote_strategy_to_portfolio(text,text,text)']) {
      assert.equal((await db.query(`select has_function_privilege('${role}','${fn}','execute') as ok`)).rows[0].ok,false,
        `${role} can call ${fn}`);
    }
  }
  await db.exec('reset role;');

  // ======================================================================
  // THE FIXED RAILS (plan v2 P5.9, 20260924110000_fixed_risk_rails.sql).
  // The plan's defaults, decided on Hassan's delegation 24 Sep, enforced in
  // queue_plan so they hold whoever proposed the order. close_buffer_min is
  // held at 0 except in its own case: the fixture market closes at the end of
  // today (UTC), so the real 15 minutes would fail these contracts for the
  // last quarter-hour of every day.
  // ======================================================================
  await db.exec(`reset role; update public.settings set value=value||'{"daily_loss_frac":0.05,"city_day_frac":0.03,"max_price":0.97,"close_buffer_min":0}'::jsonb where key='risk_rails';`);
  await db.exec('set role service_role;');
  const rd=(await db.query("select paper_desk_create('Rails',1000,'automatic',null,$1::jsonb) as id",
    [JSON.stringify({cities:['ALL'],strategies:['s1'],min_edge:.01,max_plan_usd:1000,max_exposure_usd:100000})])).rows[0].id;
  await db.exec(`reset role; update public.paper_accounts set entries_paused=false where account_id='${rd}';
    insert into signals(signal_id,action,strategy_id,fired_at,reason) select g,'ENTER','s1',now(),'rails' from generate_series(6101,6120) g;
    set role service_role;`);
  let rn=0;
  const rPlan=async(side,shares,price)=>{
    const ceiling=(shares*price*1.05).toFixed(4);
    const id=(await db.query('select publish_paper_plan($1,$2,$3,$4,$5) as id',
      [rd,`61000000-0000-0000-0000-${String(++rn).padStart(12,'0')}`,6100+rn,
       JSON.stringify([{band_id:band,side,shares:String(shares),limit_price:String(price),cash_ceiling:ceiling}]),
       JSON.stringify({net_edge_per_share:'.10'})])).rows[0].id;
    return (await db.query('select status,reason from paper_trade_plans where plan_id=$1',[id])).rows[0];
  };
  // 3% of a $1,000 desk is $30 on one city-day.
  assert.equal((await rPlan('YES',40,.50)).status,'queued','$21 on a city-day is inside the 3% rail');
  const over=await rPlan('NO',30,.40);
  assert.equal(over.status,'blocked','$21 held + $12.60 more on the same city-day passed the 3% rail');
  assert.match(over.reason,/Rail: .* would exceed 0\.03 of the account/);
  await db.exec(`reset role; update public.paper_orders set status='expired' where account_id='${rd}';
    update public.paper_accounts set reserved_cash=0 where account_id='${rd}'; set role service_role;`);
  const dear=await rPlan('NO',10,.98);
  assert.equal(dear.status,'blocked'); assert.match(dear.reason,/above the 0\.97 price bound/);
  // The kill switch, with its reason.
  await assert.rejects(db.query("select set_trading_halt(true,'')"),/needs a reason/);
  await db.query("select set_trading_halt(true,'contract test')");
  const halted=await rPlan('NO',10,.40);
  assert.equal(halted.status,'blocked'); assert.match(halted.reason,/trading halted \(contract test\)/);
  await db.query("select set_trading_halt(false,null)");
  assert.equal((await rPlan('NO',10,.40)).status,'queued','lifting the halt must let entries through again');
  await db.exec(`reset role; update public.paper_orders set status='expired' where account_id='${rd}';
    update public.paper_accounts set reserved_cash=0 where account_id='${rd}'; set role service_role;`);
  // A PLAN'S OWN LEGS COUNT ONCE (20260928090000). queue_plan inserts each
  // leg's order before it checks the next leg, and the city-day sum used to
  // count those orders as well as every leg of the plan: three $9 legs on one
  // market, $27 against a $30 room, were refused at the second leg (9 + 27).
  // An S11 ladder is exactly such a plan.
  const band2='20000000-0000-0000-0000-000000000102', band3='20000000-0000-0000-0000-000000000103';
  await db.exec(`reset role;
    insert into public.bands(band_id,market_id,band_index,band_label,band_lo,band_hi,open_low,open_high,token_yes,token_no,condition_id)
      values('${band2}','${market}',2,'21C',21,21,false,false,'yes2','no2','condition2'),
            ('${band3}','${market}',3,'22C',22,22,false,false,'yes3','no3','condition3');
    insert into signals(signal_id,action,strategy_id,fired_at,reason) select g,'ENTER','s1',now(),'rails' from generate_series(6121,6140) g;
    set role service_role;`);
  const legsPlan=async(legs)=>{
    const id=(await db.query('select publish_paper_plan($1,$2,$3,$4,$5) as id',
      [rd,`61000000-0000-0000-0000-${String(++rn).padStart(12,'0')}`,6100+rn,
       JSON.stringify(legs.map(([b,sh,px,side='YES'])=>({band_id:b,side,shares:String(sh),limit_price:String(px),
                                                         cash_ceiling:(sh*px).toFixed(4)}))),
       JSON.stringify({net_edge_per_share:'.10'})])).rows[0].id;
    return (await db.query('select status,reason from paper_trade_plans where plan_id=$1',[id])).rows[0];
  };
  const clearRd=()=>db.exec(`reset role; update public.paper_orders set status='expired' where account_id='${rd}';
    update public.paper_accounts set reserved_cash=0 where account_id='${rd}'; set role service_role;`);
  const ladder=await legsPlan([[band,18,.5],[band2,18,.5],[band3,18,.5]]);
  assert.equal(ladder.status,'queued',`three $9 legs on one city-day ($27 of a $30 room) were refused: ${ladder.reason}`);
  // The room itself still holds: $27 reserved plus $4 more is $31 of $30.
  const ladderOver=await legsPlan([[band,8,.5,'NO']]);
  assert.equal(ladderOver.status,'blocked','$31 on a city-day passed the 3% rail');
  assert.match(ladderOver.reason,/Rail: 31\.00 on london .* would exceed 0\.03 of the account/);
  assert.equal((await legsPlan([[band,4,.5,'NO']])).status,'queued','$29 of $30 is inside the rail');
  await clearRd();

  // THE CLUSTER-DAY RAIL (plan v2 P5.9 part 3, 20260928090000): held plus
  // reserved plus this plan on every city of the cluster, that date, at most
  // 8% of equity. Membership is the latest city_clusters fit, read only while
  // strategy_learning is on, as the engine reads it; otherwise a city is its
  // own cluster and the 3% city-day rail binds first.
  const mParis='30000000-0000-0000-0000-000000000201', mBerlin='30000000-0000-0000-0000-000000000202';
  const bParis='20000000-0000-0000-0000-000000000201', bBerlin='20000000-0000-0000-0000-000000000202';
  await db.exec(`reset role;
    insert into public.cities(city_key,display_name,unit,status,timezone,latitude,longitude)
      values('paris','Paris','C','active','UTC',49.0,2.5),('berlin','Berlin','C','active','UTC',52.5,13.3);
    insert into public.markets(market_id,closed,resolution_date,city_key,event_slug,unit)
      values('${mParis}',false,current_date,'paris','highest-temperature-in-paris','C'),
            ('${mBerlin}',false,current_date,'berlin','highest-temperature-in-berlin','C');
    insert into public.bands(band_id,market_id,band_index,band_label,band_lo,band_hi,open_low,open_high,token_yes,token_no,condition_id)
      values('${bParis}','${mParis}',1,'20C',20,20,false,false,'yes-p','no-p','condition-p'),
            ('${bBerlin}','${mBerlin}',1,'20C',20,20,false,false,'yes-b','no-b','condition-b');
    update public.settings set value=value||'{"cluster_day_frac":0.08}'::jsonb where key='risk_rails';
    set role service_role;`);
  // $27 on each of three cities, $81: over 8% ($80) as one cluster, fine as three.
  const threeCities=async()=>{
    const out=[];
    for (const b of [band,bParis,bBerlin]) out.push(await legsPlan([[b,54,.5]]));
    return out;
  };
  const alone=await threeCities();
  assert.deepEqual(alone.map(p=>p.status),['queued','queued','queued'],'with learning off every city is its own cluster');
  await clearRd();
  await db.exec(`reset role;
    insert into public.strategy_params(param,scope,version,value,n,prior,bounds,as_of)
      values('city_clusters','all','city-clusters:contract',
             '{"cluster":{"london":"berlin","paris":"berlin","berlin":"berlin"},"version":"city-clusters:contract"}',
             3,'{"rho":0.3}','[0,1]',current_date);
    update public.settings set value=value||'{"enabled":true}'::jsonb where key='strategy_learning';
    set role service_role;`);
  const clustered=await threeCities();
  assert.deepEqual(clustered.slice(0,2).map(p=>p.status),['queued','queued']);
  assert.equal(clustered[2].status,'blocked','$81 on one cluster-day passed the 8% rail');
  assert.match(clustered[2].reason,/Rail: 81\.00 on the berlin cluster .* would exceed 0\.08 of the account/);
  await clearRd();
  // Another date is another cluster-day.
  await db.exec(`reset role; update public.markets set resolution_date=current_date+1 where market_id='${mBerlin}'; set role service_role;`);
  assert.deepEqual((await threeCities()).map(p=>p.status),['queued','queued','queued'],'the rail is per cluster AND date');
  await clearRd();
  await db.exec(`reset role; update public.markets set resolution_date=current_date where market_id='${mBerlin}';
    update public.settings set value=value||'{"enabled":false}'::jsonb where key='strategy_learning';
    set role service_role;`);

  // No entry within the close buffer (the market's local day ends tonight).
  await db.exec(`reset role; update public.settings set value=value||'{"close_buffer_min":100000}'::jsonb where key='risk_rails'; set role service_role;`);
  const late=await rPlan('NO',10,.40);
  assert.equal(late.status,'blocked'); assert.match(late.reason,/within 100000 min of the local close/);
  await db.exec(`reset role; update public.settings set value=value||'{"close_buffer_min":0}'::jsonb where key='risk_rails'; set role service_role;`);
  // The daily loss rail: $60 lost today on a ~$1,000 desk is past 5%.
  await db.exec(`reset role; insert into public.paper_trades(account_id,band_id,side,opened_at,shares,avg_fill_price,closed_at,net_pnl)
    values('${rd}','${band}','YES',now(),10,.5,now(),-60); set role service_role;`);
  const lost=await rPlan('NO',10,.40);
  assert.equal(lost.status,'blocked'); assert.match(lost.reason,/daily loss limit reached/);
  // Only the service role flips the kill switch.
  for (const role of ['anon','authenticated'])
    assert.equal((await db.query(`select has_function_privilege('${role}','public.set_trading_halt(boolean,text)','execute') as ok`)).rows[0].ok,false);
  await db.exec(`reset role; update public.settings set value=value||'{"daily_loss_frac":1,"city_day_frac":1000,"max_price":0.999,"close_buffer_min":0}'::jsonb where key='risk_rails';`);

  // ======================================================================
  // THE PORTFOLIO ACCOUNT, ACTIVATED BY EVIDENCE (plan v2 P5.10,
  // 20260924120000_portfolio_by_evidence.sql). Activation names its approver,
  // happens once, from a desk with no history, and turns the placeholder cash
  // into the bankroll through one activity row, so the books still balance.
  // The nightly allocation takes only portfolio-state strategies, each within
  // the cap, never more than the whole bankroll.
  // ======================================================================
  await db.exec('reset role;');
  const gate=(await db.query("select value from settings where key='portfolio_gate'")).rows[0].value;
  assert.equal(Number(gate.bankroll_usd),10000); assert.equal(Number(gate.cap_per_strategy),0.4);
  assert.equal(Number(gate.min_settled_dates),30); assert.equal(Number(gate.min_decisions),60);
  const pfId=(await db.query("select account_id from paper_accounts where kind='portfolio'")).rows[0].account_id;
  await db.exec(`insert into public.strategies(strategy_id,name) values('s_pf_a','pf a'),('s_pf_b','pf b'),('s_pf_c','pf c'),('s_pf_x','pf x')
      on conflict do nothing;`);
  for (const sid of ['s_pf_a','s_pf_b','s_pf_c','s_pf_x'])
    await db.query("select set_strategy_state($1,'shadow','P5.10 contract')",[sid]);
  for (const sid of ['s_pf_a','s_pf_b','s_pf_c'])
    await db.query("select promote_strategy_to_portfolio($1,'Hassan (delegated to Claude, 24 Sep)','passed the gate')",[sid]);
  await db.exec('set role service_role;');
  await assert.rejects(db.query(`select set_portfolio_allocation('{"s_pf_a":0.4}'::jsonb,'v1')`),/not active/,
    'an allocation was written to a suspended portfolio');
  await assert.rejects(db.query("select activate_portfolio_account(10000,'','the gate passed')"),/names who approved it/);
  await assert.rejects(db.query("select activate_portfolio_account(10000,'Hassan','')"),/names who approved it/);
  await assert.rejects(db.query("select activate_portfolio_account(0,'Hassan','the gate passed')"),/must be above 0/);
  // A portfolio desk that has already done something cannot have its bankroll reset.
  await db.exec('reset role; begin;');
  await db.query("insert into paper_activity(account_id,event_type,payload) values($1,'plan_authorized','{}')",[pfId]);
  await assert.rejects(db.query("select activate_portfolio_account(10000,'Hassan','the gate passed')"),/has history/);
  await db.exec('rollback; set role service_role;');
  const act=(await db.query(
    "select activate_portfolio_account(10000,'Hassan (delegated to Claude, 24 Sep)','s_pf_a passed the P5.10 gate') as r")).rows[0].r;
  assert.equal(act.already,false); assert.equal(Number(act.bankroll),10000);
  await db.exec('reset role;');
  const pfa=(await db.query('select * from paper_accounts where account_id=$1',[pfId])).rows[0];
  assert.equal(pfa.status,'active'); assert.equal(pfa.mode,'automatic'); assert.equal(pfa.entries_paused,false);
  assert.equal(Number(pfa.bankroll_usd),10000); assert.equal(Number(pfa.cash),10000); assert.equal(Number(pfa.starting_cash),10000);
  assert.deepEqual(pfa.policy.strategies,[],'the portfolio trades nothing until the allocation names what');
  await books(pfId,'portfolio activated');
  const actRow=(await db.query("select payload,cash_delta from paper_activity where account_id=$1 and event_type='portfolio_activated'",[pfId])).rows;
  assert.equal(actRow.length,1); assert.equal(actRow[0].payload.approved_by,'Hassan (delegated to Claude, 24 Sep)');
  await db.exec('set role service_role;');
  assert.equal((await db.query("select activate_portfolio_account(5,'Hassan','again') as r")).rows[0].r.already,true);
  await db.exec('reset role;');
  assert.equal(Number((await db.query('select cash from paper_accounts where account_id=$1',[pfId])).rows[0].cash),10000,
    'a second activation changed the bankroll');
  await db.exec('set role service_role;');
  // The allocation.
  await assert.rejects(db.query(`select set_portfolio_allocation('{"s_pf_a":0.41}'::jsonb,'v1')`),/outside \[0, 0.4/);
  await assert.rejects(db.query(`select set_portfolio_allocation('{"s_pf_a":-0.1}'::jsonb,'v1')`),/outside/);
  await assert.rejects(db.query(`select set_portfolio_allocation('{"s_pf_x":0.2}'::jsonb,'v1')`),/s_pf_x is not in the portfolio state/);
  await db.exec(`reset role; update public.settings set value=value||'{"cap_per_strategy":0.5}'::jsonb where key='portfolio_gate'; set role service_role;`);
  await assert.rejects(db.query(`select set_portfolio_allocation('{"s_pf_a":0.5,"s_pf_b":0.4,"s_pf_c":0.2}'::jsonb,'v1')`),/more than the whole bankroll/);
  await db.exec(`reset role; update public.settings set value=value||'{"cap_per_strategy":0.4}'::jsonb where key='portfolio_gate'; set role service_role;`);
  const pv0=(await db.query('select policy_version from paper_accounts where account_id=$1',[pfId])).rows[0].policy_version;
  await db.query(`select set_portfolio_allocation('{"s_pf_a":0.4,"s_pf_b":0.25,"s_pf_c":0}'::jsonb,'thompson-v1')`);
  const pol=(await db.query('select policy,policy_version from paper_accounts where account_id=$1',[pfId])).rows[0];
  assert.deepEqual(pol.policy.strategies.sort(),['s_pf_a','s_pf_b'],'a zero weight must not be traded');
  assert.equal(pol.policy.allocation_version,'thompson-v1'); assert.equal(pol.policy_version,pv0+1);
  await db.query(`select set_portfolio_allocation('{"s_pf_a":0.4,"s_pf_b":0.25,"s_pf_c":0}'::jsonb,'thompson-v1')`);
  assert.equal((await db.query('select policy_version from paper_accounts where account_id=$1',[pfId])).rows[0].policy_version,pv0+1,
    'an unchanged allocation invalidated every pending plan');
  // The portfolio trades an allocated strategy and refuses the rest.
  await db.exec(`reset role; insert into signals(signal_id,action,strategy_id,fired_at,reason) values
      (7101,'ENTER','s_pf_a',now(),'allocated'),(7102,'ENTER','s_pf_c',now(),'weight 0'); set role service_role;`);
  const pfPlan=async(cmd,sig)=>{
    const id=(await db.query('select publish_paper_plan($1,$2,$3,$4,$5) as id',
      [pfId,cmd,sig,JSON.stringify([{band_id:band,side:'NO',shares:'2',limit_price:'.50',cash_ceiling:'1.05'}]),
       JSON.stringify({net_edge_per_share:'.10'})])).rows[0].id;
    return (await db.query('select status,reason from paper_trade_plans where plan_id=$1',[id])).rows[0];
  };
  assert.equal((await pfPlan('71000000-0000-0000-0000-000000000001',7101)).status,'queued','the portfolio could not trade an allocated strategy');
  const unalloc=await pfPlan('71000000-0000-0000-0000-000000000002',7102);
  assert.equal(unalloc.status,'blocked'); assert.match(unalloc.reason,/Strategy outside policy/);
  for (const role of ['anon','authenticated'])
    for (const fn of ['public.activate_portfolio_account(numeric,text,text)','public.set_portfolio_allocation(jsonb,text)'])
      assert.equal((await db.query(`select has_function_privilege('${role}','${fn}','execute') as ok`)).rows[0].ok,false,
        `${role} can call ${fn}`);
  await db.exec('reset role;');
  await books(pfId,'portfolio after its first plan');

  // ======================================================================
  // THE DECISION LOG (plan v2 P5.11, 20260925090000_decision_log.sql). One
  // row per run, strategy and city-day, NONE included; append-only; rows
  // leave only through prune_decisions() with the archive's verified count.
  // ======================================================================
  await db.exec('reset role;');
  await db.exec('set role service_role;');
  const dRun='a1000000-0000-0000-0000-000000000001';
  await db.query(`insert into decisions(run_id,decided_at,strategy_id,city_key,resolution_date,action,reason_code,held_usd,n_signals)
    values ($1, now()-interval '40 days','s1','london','2026-08-10','NONE','no_signal',0,0),
           ($1, now()-interval '40 days','s1','paris','2026-08-10','BUY','enter',0,1),
           ('a1000000-0000-0000-0000-000000000002', now(),'s1','london',current_date,'HOLD','holding',3.5,0)`,[dRun]);
  await assert.rejects(db.query(`insert into decisions(run_id,decided_at,strategy_id,city_key,resolution_date,action,reason_code)
    values ($1, now(),'s1','london','2026-08-10','NONE','no_signal')`,[dRun]),/decisions_one_per_run/,
    'a run decided the same strategy and city-day twice');
  await assert.rejects(db.query(`insert into decisions(run_id,decided_at,strategy_id,city_key,resolution_date,action,reason_code)
    values (gen_random_uuid(), now(),'s1','london','2026-08-10','MAYBE','no_signal')`),/decisions_action_check/);
  await assert.rejects(db.query(`insert into decisions(run_id,decided_at,strategy_id,city_key,resolution_date,action,reason_code)
    values (gen_random_uuid(), now(),'s1','london','2026-08-10','NONE','A reason, in prose.')`),/decisions_reason_code_check/,
    'the reason is a short code, not a text blob');
  // The service role cannot even ask; the owner, who can, is refused by the trigger.
  await assert.rejects(db.query("delete from decisions where run_id=$1",[dRun]),/permission denied/);
  await db.exec('reset role;');
  await assert.rejects(db.query("update decisions set action='BUY' where run_id=$1",[dRun]),/append-only/);
  await assert.rejects(db.query("delete from decisions where run_id=$1",[dRun]),/append-only/,
    'a decision left Postgres outside the archive');
  await db.exec('set role service_role;');
  // The prune: never under 14 days, never without the verified count, never a different count.
  assert.equal((await db.query("select prune_decisions(7,false,null,2) as r")).rows[0].r.ok,false);
  assert.match((await db.query("select prune_decisions(30,false) as r")).rows[0].r.error,/p_expected_rows is required/);
  const dMis=(await db.query("select prune_decisions(30,false,null,5) as r")).rows[0].r;
  assert.equal(dMis.ok,false); assert.match(dMis.error,/verified 5 rows but prune would delete 2/);
  assert.equal((await db.query("select prune_decisions(30,true) as r")).rows[0].r.would_delete,2);
  assert.equal((await db.query("select prune_decisions(30,false,null,2) as r")).rows[0].r.deleted,2);
  // Scoped to this test's strategy: the engine's own decisions (P5.12 part 3b,
  // above) are recent and stay, and are not this test's rows.
  assert.deepEqual((await db.query("select action from decisions where strategy_id='s1'")).rows.map(r=>r.action),['HOLD'],
    'the prune took a decision inside the window');
  await db.exec('reset role;');
  await assert.rejects(db.query("delete from decisions"),/append-only/,'the archive exemption outlived the prune');
  for (const role of ['anon','authenticated']) {
    assert.equal((await db.query(`select has_table_privilege('${role}','public.decisions','select') as ok`)).rows[0].ok,false);
    assert.equal((await db.query(`select has_function_privilege('${role}','public.prune_decisions(integer,boolean,timestamptz,bigint)','execute') as ok`)).rows[0].ok,false);
  }

  // ======================================================================
  // WRITES NEED AN OPERATOR (plan v2 P1.2,
  // 20260923130000_writes_need_an_operator.sql). The browser reads settings
  // as anon; it must not see who the operators are, nor the n8n webhook URLs,
  // which are all it takes to fire a job.
  // ======================================================================
  await db.exec(`reset role;
    insert into public.settings(key,value) values ('n8n_webhooks','{"P0.3":{"url":"https://n8n.example/webhook/x"}}'),
      ('bankroll','{"amount":100}') on conflict (key) do nothing;`);
  assert.deepEqual((await db.query("select value from public.settings where key='operators'")).rows[0].value,[],
    'the operator list must exist and start empty - nobody writes until someone is added');
  await db.exec('set role anon;');
  const anonKeys=(await db.query('select key from public.settings order by key')).rows.map(r=>r.key);
  assert.ok(!anonKeys.includes('operators'),'anon can read the operator list');
  assert.ok(!anonKeys.includes('n8n_webhooks'),'anon can read the n8n webhook URLs');
  assert.ok(anonKeys.includes('bankroll'),'anon lost the settings the board reads');
  await db.exec('reset role;');

  await db.close();
  console.log('PASS: authenticated and single-desk paper contracts, private research, leases, fills, approvals, exits, the paper_trades bridge, the book-redundancy prune, the desk-independent strategy mark, desk retirement, a shadow ledger per strategy and a suspended portfolio account, strategy lifecycle states, the fixed risk rails and the kill switch, the portfolio activated by evidence and its allocation, the decision log and its verified prune, research capture of prices only, no PUBLIC execute on SECURITY DEFINER functions and the settings the browser may not read');
})().catch(e=>{console.error(e);process.exit(1);});
