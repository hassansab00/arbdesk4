const {PGlite}=require('@electric-sql/pglite');
const fs=require('fs');const path=require('path');const assert=require('node:assert/strict');
const migration=fs.readFileSync(path.join(__dirname,'../../supabase/migrations/20261112205301_clock_startup_recovery.sql'),'utf8');
(async()=>{
 const db=new PGlite();
 await db.exec(`create role anon;create role authenticated;create role service_role;
 create schema arbdesk_private;create schema net;create schema vault;
 create table settings(key text,value jsonb);
 insert into settings values('workflow_schedules','{"P6.1_clock":{"mode":"auto"}}');
 create table vault.decrypted_secrets(name text,decrypted_secret text);
 insert into vault.decrypted_secrets values('github_actions_token','fixture-only');
 create table ingest_log(log_id bigint generated always as identity,job text,status text,detail jsonb,logged_at timestamptz default now());
 create table net.requests(id bigint generated always as identity,url text,body jsonb,headers jsonb);
 create table net._http_response(id bigint,status_code int,timed_out boolean,content text);
 create function net.http_get(url text,params jsonb default '{}',headers jsonb default '{}',timeout_milliseconds int default 5000)
 returns bigint language sql as $$insert into net.requests(url,headers) values(url,headers) returning id$$;
 create function net.http_post(url text,body jsonb default '{}',params jsonb default '{}',headers jsonb default '{}',timeout_milliseconds int default 5000)
 returns bigint language sql as $$insert into net.requests(url,body,headers) values(url,body,headers) returning id$$;
 create function public.clock_due(p_at timestamptz) returns table(file text,inputs jsonb) language sql as $$select 'tick.yml'::text,'{"dry_run":"false"}'::jsonb$$;
 create function public.should_run(text,text) returns jsonb language sql as $$select '{"run":true}'::jsonb$$;
 create function public.log_ingest(text,text,int,jsonb) returns void language sql as $$insert into ingest_log(job,status,detail) values($1,$2,$4)$$;`);
 await db.exec(migration);await db.exec(migration);
 const run=async()=> (await db.query('select arbdesk_private.recover_clock_startups() r')).rows[0].r;
 const add=async()=>{
  const id=(await db.query('select gen_random_uuid()::text id')).rows[0].id;
  await db.query(`insert into ingest_log(job,status,detail,logged_at) values('P6.1_clock','ok',jsonb_build_object('clock','supabase','dry_run',false,'dispatched',jsonb_build_array(jsonb_build_object('file','tick.yml','clock_dispatch_id',$1::text))),now()-interval '5 minutes')`,[id]);
  return id;
 };
 const cases=['startup','failure','cancelled','attempt2','wrong_branch','wrong_receipt','ambiguous','malformed'];
 const ids=[];for(const kind of cases)ids.push(await add());
 assert.equal((await run()).checks_enqueued,8);
 const checks=(await db.query('select * from arbdesk_private.clock_startup_checks order by dispatch_log_id')).rows;
 for(let i=0;i<cases.length;i++){
  const kind=cases[i],r=checks[i];
  const record={id:100+i,status:'completed',conclusion:'startup_failure',run_attempt:1,
   head_branch:'main',event:'workflow_dispatch',display_title:'AD4 clock '+r.dispatch_id,created_at:r.dispatched_at};
  if(kind==='failure')record.conclusion='failure';if(kind==='cancelled')record.conclusion='cancelled';
  if(kind==='attempt2')record.run_attempt=2;if(kind==='wrong_branch')record.head_branch='other';
  if(kind==='wrong_receipt')record.display_title='AD4 clock 00000000-0000-0000-0000-000000000000';
  const body=kind==='malformed'?'bad-json':JSON.stringify({workflow_runs:kind==='ambiguous'?[record,record]:[record]});
  await db.query('insert into net._http_response values($1,200,false,$2)',[r.request_id,body]);
 }
 assert.equal((await run()).submitted,1);
 const posts=()=>db.query("select * from net.requests where url like '%/rerun'");
 assert.equal((await posts()).rows.length,1);assert.ok((await posts()).rows[0].url.endsWith('/100/rerun'));
 const states=(await db.query('select state from arbdesk_private.clock_startup_checks order by dispatch_log_id')).rows.map(x=>x.state);
 assert.deepEqual(states,['retry_submitted','checked_no_retry','checked_no_retry','checked_no_retry','held_ambiguous_run','held_ambiguous_run','held_ambiguous_run','held_invalid_response']);
 const retry=(await db.query("select request_id from arbdesk_private.clock_startup_checks where state='retry_submitted'")).rows[0];
 await db.query("insert into net._http_response values($1,201,false,'{}')",[retry.request_id]);
 await run();await run();assert.equal((await posts()).rows.length,1);
 assert.equal((await db.query("select state from arbdesk_private.clock_startup_checks where run_id=100")).rows[0].state,'retry_accepted_not_completion');
 // A duplicate API run ID cannot cause another queued POST, even with a new dispatch receipt.
 const duplicate=await add();await run();
 const dup=(await db.query('select * from arbdesk_private.clock_startup_checks where dispatch_id=$1',[duplicate])).rows[0];
 await db.query('insert into net._http_response values($1,200,false,$2)',[dup.request_id,JSON.stringify({workflow_runs:[{id:100,status:'completed',conclusion:'startup_failure',run_attempt:1,head_branch:'main',event:'workflow_dispatch',display_title:'AD4 clock '+duplicate,created_at:dup.dispatched_at}]})]);
 await run();assert.equal((await posts()).rows.length,1);
 // Expired work and paused clocks never retry.
 await db.exec("update arbdesk_private.clock_startup_checks set state='checking',dispatched_at=now()-interval '31 minutes' where dispatch_id='"+duplicate+"'");
 await run();assert.equal((await db.query('select state from arbdesk_private.clock_startup_checks where dispatch_id=$1',[duplicate])).rows[0].state,'held_expired');
 await db.exec(`update settings set value='{"P6.1_clock":{"mode":"off"}}'`);
 assert.equal((await run()).state,'clock_paused');
 assert.deepEqual((await db.query(`select has_table_privilege('anon','arbdesk_private.clock_startup_checks','select') a,
 has_function_privilege('service_role','arbdesk_private.recover_clock_startups()','execute') b`)).rows[0],{a:false,b:false});
 const tick=(await db.query('select public.clock_tick() r')).rows[0].r;
 const receipt=tick.dispatched[0].clock_dispatch_id;
 const dispatch=(await db.query("select body from net.requests where url like '%/dispatches' order by id desc limit 1")).rows[0].body;
 assert.equal(dispatch.ref,'main');assert.equal(dispatch.inputs.dry_run,'false');assert.equal(dispatch.inputs.clock_dispatch_id,receipt);
 const stored=(await db.query('select to_jsonb(c)::text data from arbdesk_private.clock_startup_checks c')).rows;
 assert.ok(stored.every(x=>!x.data.includes('fixture-only')&&!x.data.includes('workflow_runs')));
 await db.close();console.log('clock startup recovery: exact UUID, single original-run retry, no ordinary/ambiguous/stale retry, private retained receipts');
})().catch(e=>{console.error(e);process.exit(1)});
