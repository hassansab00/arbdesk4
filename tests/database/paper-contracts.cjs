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
    create table public.bands(band_id uuid primary key,market_id uuid,token_yes text,token_no text,condition_id text);
    create table public.markets(market_id uuid primary key,closed boolean,resolution_date date,city_key text);
    create table public.signals(signal_id bigint primary key,action text,strategy_id text,fired_at timestamptz,reason text);
    create table public.band_probabilities(prob_id uuid primary key,band_id uuid,computed_at timestamptz default now());
    create table public.model_versions(version_id uuid primary key,created_at timestamptz default now());
    create table public.fact_forecast_outcome(city_key text,for_date date,model text,lead_days int,captured_at timestamptz default now(),primary key(city_key,for_date,model,lead_days));
    create table public.fact_band_outcome(band_id uuid primary key,captured_at timestamptz default now());
    create table public.fact_signal_outcome(signal_id bigint primary key,captured_at timestamptz default now());
    grant select on public.bands,public.markets,public.signals to service_role;
    create view public.v_synthesis_findings as select 'finding'::text as key;
    create view public.v_learning_state as select 'learning'::text as stage;
    create view public.v_forecast_convergence as select 'city'::text as city_key;`);
  const directory = path.resolve(__dirname,'../../supabase/migrations');
  for (const file of fs.readdirSync(directory).filter(x=>x.endsWith('.sql')).sort()) {
    await db.exec(fs.readFileSync(path.join(directory,file),'utf8'));
  }
  const uid='10000000-0000-0000-0000-000000000001', other='10000000-0000-0000-0000-000000000002';
  const band='20000000-0000-0000-0000-000000000001', market='30000000-0000-0000-0000-000000000001';
  const command='40000000-0000-0000-0000-000000000001';
  await db.exec(`insert into auth.users values('${uid}'),('${other}');insert into public.desk_members(user_id) values('${uid}');
    insert into public.markets values('${market}',false,current_date,'london');insert into public.bands values('${band}','${market}','yes','no','condition');
    set role authenticated;set request.jwt.claim.sub='${uid}';`);
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
  await db.query("select capture_research_state('run1','commit1')");
  await db.query("select capture_research_state('run1','commit1')");
  await db.query("select capture_research_state('run2','commit1')");
  assert.equal((await db.query('select count(*)::int as n from research_captures')).rows[0].n,3);
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
