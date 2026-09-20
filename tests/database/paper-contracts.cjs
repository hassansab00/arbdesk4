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
    create table public.markets(market_id uuid primary key,closed boolean,resolution_date date,city_key text,
      event_slug text,unit text,condition_id text,last_seen_at timestamptz default now());
    -- created_at/updated_at are NOT NULL in production (both default now()).
    -- A migration that stamps updated_at fails here without them, and the
    -- 2026-09-19 retirement migration does exactly that. See CLAUDE.md: the
    -- fixture has to match the live shape or the contracts pass against a
    -- database that does not exist.
    create table public.cities(city_key text primary key,display_name text,unit text,status text,
      timezone text,latitude numeric,longitude numeric,
      created_at timestamptz not null default now(),
      updated_at timestamptz not null default now());
    create table public.weather_observations(obs_id bigint primary key,city_key text,valid_at timestamptz,
      observed_at timestamptz);
    create table public.weather_forecasts(forecast_id bigint primary key,city_key text,model text,run_at timestamptz,
      for_date date,lead_days int default 0,forecast_max_c numeric default 0);
    create table public.book_snapshots(snapshot_id bigint primary key,band_id uuid,observed_at timestamptz,
      market_state text default 'LIVE',tradeable boolean default true);
    create table public.trades_observed(trade_id bigint primary key,city_key text,traded_at timestamptz);
    -- signals.payload carries the decision snapshot that
    -- 20260919180000_trade_decision_lineage.sql stamps onto every trade, so
    -- the column has to exist here or that migration's backfill fails on a
    -- table shape production does not have.
    -- regime_label is created by sql/ad4_rpc.sql, which this harness never
    -- applies, and 20260920220000 stamps it onto the trade at fill. Nullable
    -- text, matching the live column. Appended last so the positional inserts
    -- below keep working.
    create table public.signals(signal_id bigint primary key,action text,strategy_id text,
      fired_at timestamptz,reason text,payload jsonb,regime_label text);
    -- ledger is created by sql/ad4_00_preflight.sql, which this harness never
    -- applies. Column types and the two NOT NULLs match the live table.
    create table public.ledger(entry_id bigserial primary key,
      recorded_at timestamptz not null default now(),trade_id uuid,signal_id bigint,
      event_type text not null,payload jsonb not null,stage text,strategy_id text,
      deployment_id uuid,band_id uuid,regime_label text,forecast_version text,
      calibration_version text,cost_version text,detail jsonb);
    -- strategies is created by sql/ad4_rpc.sql. Only v_paper_desks reads it,
    -- and only to count the enabled ones, but the column types and NOT NULLs
    -- match the live table so the view is built against the real shape.
    create table public.strategies(strategy_id text primary key,name text not null,side text,
      origin text,config jsonb not null default '{}'::jsonb,conflict_class text,
      enabled boolean not null default true,created_at timestamptz not null default now(),
      universe jsonb,regime_filter jsonb,capital_cap_pct numeric,max_concurrent integer,extra jsonb);
    insert into public.strategies(strategy_id,name) values('s1','price entry');
    create table public.band_probabilities(prob_id uuid primary key,band_id uuid,computed_at timestamptz default now());
    create table public.edges(edge_id bigint primary key,band_id uuid,computed_at timestamptz default now(),
      side text,tradeable boolean default false);
    create table public.live_weather(city_key text primary key,updated_at timestamptz,observed_at timestamptz);
    create table public.ingest_log(log_id bigint primary key,job text,started_at timestamptz,finished_at timestamptz,
      status text,rows_written integer,detail jsonb,rows integer,logged_at timestamptz default now());
    create table public.model_versions(version_id uuid primary key,created_at timestamptz default now());
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
    await db.exec(fs.readFileSync(path.join(directory,file),'utf8'));
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
  const uid='10000000-0000-0000-0000-000000000001', other='10000000-0000-0000-0000-000000000002';
  const band='20000000-0000-0000-0000-000000000001', market='30000000-0000-0000-0000-000000000001';
  const command='40000000-0000-0000-0000-000000000001';
  await db.exec(`insert into auth.users values('${uid}'),('${other}');insert into public.desk_members(user_id) values('${uid}');
    insert into public.cities(city_key,display_name,unit,status,timezone,latitude,longitude)
      values('london','London','C','active','Europe/London',51.47,-0.45);
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
      values('60000000-0000-0000-0000-000000000001','${band}',now());
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
  //   3  cash = starting_cash - open cost basis + realised P&L + every reset.
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
                          and event_type='account_reset'),0)                    as rebased
        from public.paper_accounts a where a.account_id=$1`, [who])).rows[0];
    const n = x => Number(x);
    const near = (got, want, what) => assert.ok(Math.abs(got - want) < 1e-6,
      `${where}: ${what} - got ${got}, expected ${want}`);
    near(n(r.cash), n(r.ledger),
      'cash is not the sum of its activity rows, so money moved without an event');
    near(n(r.reserved_cash), n(r.live_ceilings),
      'reserved cash is not what live orders claimed');
    near(n(r.cash), n(r.starting_cash) - n(r.basis) + n(r.realized) + n(r.rebased),
      'cash != starting cash - open cost basis + realised P&L + resets');
    assert.ok(n(r.cash) >= 0, `${where}: cash went negative`);
    assert.ok(n(r.reserved_cash) >= 0, `${where}: reserved cash went negative`);
    assert.ok(n(r.cash) - n(r.reserved_cash) >= -1e-9,
      `${where}: the desk committed more than it holds (cash ${r.cash}, reserved ${r.reserved_cash})`);
    assert.ok(n(r.thinnest) >= 0, `${where}: a position holds negative shares`);
  }

  const account=(await db.query(`select create_paper_account('Test account',100) as id`)).rows[0].id;
  const submit=()=>db.query(`select submit_paper_order($1,$2,$3,'YES',10,0.55,6,'test') as id`,[account,command,band]);
  const order=(await submit()).rows[0].id;
  assert.equal((await submit()).rows[0].id,order,'Retry returns the original command');
  assert.equal(Number((await db.query('select reserved_cash from paper_accounts')).rows[0].reserved_cash),6);
  await db.exec(`set request.jwt.claim.sub='${other}';`);
  assert.equal((await db.query('select * from paper_accounts')).rows.length,0,'Nonowner cannot read account');
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
  const balance=(await db.query('select cash,reserved_cash from paper_accounts')).rows[0];
  assert.equal(Number(balance.cash),94.57575);assert.equal(Number(balance.reserved_cash),0);
  assert.equal(Number((await db.query('select shares from paper_positions')).rows[0].shares),10);
  assert.equal((await db.query("select count(*)::int as n from paper_activity where event_type='execution_completed'")).rows[0].n,1);
  const researchBefore=Number((await db.query('select count(*)::int as n from research_captures')).rows[0].n);
  await db.query("select capture_research_state('run1','commit1')");
  await db.query("select capture_research_state('run1','commit1')");
  await db.query("select capture_research_state('run2','commit1')");
  assert.equal((await db.query('select count(*)::int as n from research_captures')).rows[0].n,researchBefore+3);
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
  const legs=[{band_id:band,side:'YES',shares:'4',limit_price:'.50',cash_ceiling:'2.10'}];
  const plan=(await db.query('select publish_paper_plan($1,$2,1,$3,$4) as id',
    [account,'40000000-0000-0000-0000-000000000002',JSON.stringify(legs),JSON.stringify({net_edge_per_share:'.10'})])).rows[0].id;
  await db.exec(`reset role;set role authenticated;set request.jwt.claim.sub='${other}';`);
  assert.equal((await db.query('select * from paper_trade_plans')).rows.length,0);
  await assert.rejects(db.query('select approve_paper_plan($1)',[plan]),/Account access denied/);
  await db.exec(`set request.jwt.claim.sub='${uid}';`);
  await db.query('select approve_paper_plan($1)',[plan]);
  await db.query('select approve_paper_plan($1)',[plan]);
  assert.equal(Number((await db.query('select reserved_cash from paper_accounts')).rows[0].reserved_cash),2.10);
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
  assert.equal(Number((await db.query('select reserved_cash from paper_accounts')).rows[0].reserved_cash),0);
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
  assert.equal(Number((await db.query('select cash from paper_accounts')).rows[0].cash),96.92775);
  await db.exec(`reset role;set role authenticated;set request.jwt.claim.sub='${uid}';`);
  await db.query("select set_paper_policy($1,'automatic',true,$2)",[account,JSON.stringify(policy)]);
  await db.query('select set_paper_exit_policy($1,true,.25,.15)',[account]);
  await books(account,'after the exit policy changes');
  const version=(await db.query('select policy_version from paper_accounts')).rows[0].policy_version;
  await db.exec('reset role;set role service_role;');
  const preview={status:'filled',shares:'6',notional:'4.80',fee:'.048',snapshot_id:'snapshot',
    fills:[{shares:'6',price:'.80',notional:'4.80',fee:'.048'}]};
  const auto=(await db.query("select queue_automatic_paper_exit($1,$2,$3,'YES',.80,$4,$5) as id",
    [account,'40000000-0000-0000-0000-000000000005',band,JSON.stringify(preview),version])).rows[0].id;
  assert.ok(auto,'Entry pause leaves explicitly enabled exits available');
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
  assert.equal(Number((await db.query('select reserved_cash from paper_accounts')).rows[0].reserved_cash),0);
  assert.equal((await db.query("select count(*)::int as n from paper_activity where event_type='order_expired'")).rows[0].n,1);
  await db.exec(`reset role;set role authenticated;set request.jwt.claim.sub='${uid}';`);
  const settleOrder=(await db.query("select submit_paper_order($1,$2,$3,'YES',2,.50,1.10,'settlement test') as id",
    [account,'40000000-0000-0000-0000-000000000007',band])).rows[0].id;
  await db.exec('reset role;set role service_role;');
  const settleClaim=(await db.query('select claim_paper_order() as job')).rows[0].job;
  await db.query('select complete_paper_order($1,$2,$3)',[settleOrder,settleClaim.lease_token,JSON.stringify({status:'filled',shares:'2',
    notional:'1',fee:'.025',snapshot_id:'snapshot',fills:[{shares:'2',price:'.50',notional:'1',fee:'.025'}]})]);
  const beforeSettlement=Number((await db.query('select cash from paper_accounts')).rows[0].cash);
  const gamma={conditionId:'condition',closed:true,umaResolutionStatus:'resolved'};
  const clob={condition_id:'condition',closed:true,accepting_orders:false,tokens:[{token_id:'yes',winner:true},{token_id:'no',winner:false}]};
  await db.query('insert into paper_resolution_evidence(proof_id,condition_id,token_yes,token_no,winning_token,gamma,clob,source_urls) values($1,$2,$3,$4,$5,$6,$7,$8)',
    ['resolution','condition','yes','no','yes',JSON.stringify(gamma),JSON.stringify(clob),'[]']);
  assert.equal((await db.query("select settle_paper_inventory($1,'resolution') as n",[band])).rows[0].n,1);
  assert.equal((await db.query("select settle_paper_inventory($1,'resolution') as n",[band])).rows[0].n,0);
  await books(account,'after venue settlement, and after it was replayed');
  assert.equal(Number((await db.query('select cash from paper_accounts')).rows[0].cash),beforeSettlement+2);
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
  await db.exec("reset role;insert into signals values(2,'ENTER','s1',now(),'single strategy');set role service_role;");
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
      where access_mode='single_desk' order by created_at limit 1`)).rows[0].account_id;
  assert.equal(
    (await db.query("select create_single_paper_account('ignored',1) as id")).rows[0].id,
    oldestDesk,
    'the bootstrap resolved to a desk other than the oldest, so which desk a caller '
    +'gets depends on physical row order');

  await db.close();
  console.log('PASS: authenticated and single-desk paper contracts, private research, leases, fills, approvals, exits and the paper_trades bridge');
})().catch(e=>{console.error(e);process.exit(1);});
