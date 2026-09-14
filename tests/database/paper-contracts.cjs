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
    create table public.cities(city_key text primary key,display_name text,unit text,status text,
      timezone text,latitude numeric,longitude numeric);
    create table public.weather_observations(obs_id bigint primary key,city_key text,valid_at timestamptz,
      observed_at timestamptz);
    create table public.weather_forecasts(forecast_id bigint primary key,city_key text,model text,run_at timestamptz,
      for_date date,lead_days int default 0,forecast_max_c numeric default 0);
    create table public.book_snapshots(snapshot_id bigint primary key,band_id uuid,observed_at timestamptz,
      market_state text default 'LIVE',tradeable boolean default true);
    create table public.trades_observed(trade_id bigint primary key,city_key text,traded_at timestamptz);
    create table public.signals(signal_id bigint primary key,action text,strategy_id text,fired_at timestamptz,reason text);
    create table public.band_probabilities(prob_id uuid primary key,band_id uuid,computed_at timestamptz default now());
    create table public.edges(edge_id bigint primary key,band_id uuid,computed_at timestamptz default now(),
      side text,tradeable boolean default false);
    create table public.live_weather(city_key text primary key,updated_at timestamptz,observed_at timestamptz);
    create table public.ingest_log(log_id bigint primary key,job text,started_at timestamptz,finished_at timestamptz,
      status text,rows_written integer,detail jsonb,rows integer,logged_at timestamptz default now());
    create table public.model_versions(version_id uuid primary key,created_at timestamptz default now());
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
    create table public.fact_signal_outcome(signal_id bigint primary key,captured_at timestamptz default now());
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
  await db.query('select cancel_paper_order($1)',[queued[0].order_id]);
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
  assert.equal(Number((await db.query('select shares from paper_positions')).rows[0].shares),0);
  await db.exec(`reset role;set role authenticated;set request.jwt.claim.sub='${uid}';`);
  const expiring=(await db.query("select submit_paper_order($1,$2,$3,'YES',2,.50,1.10,'expiry test') as id",
    [account,'40000000-0000-0000-0000-000000000006',band])).rows[0].id;
  await db.exec('reset role;set role service_role;');
  await db.query("update paper_orders set expires_at=now()-interval '1 second' where order_id=$1",[expiring]);
  await db.query('select expire_paper_commands()');await db.query('select expire_paper_commands()');
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
  assert.equal(Number((await db.query('select cash from paper_accounts')).rows[0].cash),beforeSettlement+2);
  assert.equal(Number((await db.query('select shares from paper_positions')).rows[0].shares),0);
  assert.equal((await db.query('select count(*)::int as n from paper_position_settlements')).rows[0].n,1);

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
  await db.close();
  console.log('PASS: authenticated and single-desk paper contracts, private research, leases, fills, approvals and exits');
})().catch(e=>{console.error(e);process.exit(1);});
