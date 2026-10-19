const {PGlite}=require('@electric-sql/pglite');
const fs=require('node:fs'),assert=require('node:assert/strict');
const root=require('node:path').join(__dirname,'../..');
const migration=fs.readFileSync(root+'/supabase/migrations/20261019083757_page_cache_background_receipts.sql','utf8');
(async()=>{
 const db=new PGlite();const q=async(sql,args=[])=>(await db.query(sql,args)).rows;
 await db.exec(`create role anon;create role authenticated;create role service_role;
 create table ingest_log(log_id bigserial primary key,job text,status text,detail jsonb,logged_at timestamptz default now());
 create function log_ingest(text,text,int,jsonb) returns void language sql as $$insert into ingest_log(job,status,detail) values($1,$2,$4)$$;
 create schema cron;create table cron.job(jobid bigserial primary key,jobname text unique,schedule text,command text);
 create function cron.schedule(text,text,text) returns bigint language plpgsql as $$declare n bigint;begin
 insert into cron.job(jobname,schedule,command) values($1,$2,$3) on conflict(jobname) do update set command=excluded.command returning jobid into n;return n;end$$;
 create function cron.unschedule(bigint) returns boolean language plpgsql as $$begin delete from cron.job where jobid=$1;return found;end$$;
 create table control(mode text,n int);insert into control values('normal',0);
 create function refresh_page_cache() returns jsonb language plpgsql as $$declare m text;begin
 select mode into m from control;
 if m='busy' then return '{"skipped":"refresh already running"}'::jsonb;end if;
 if m='broken' then raise exception 'bad view';end if;
 if m='timeout' then raise exception 'refresh timed out' using errcode='57014';end if;
 if m='empty' then return null;end if;
 update control set n=n+1;return '{"mv":42}'::jsonb;end$$;`);
 const before=(await q("select pg_get_functiondef('refresh_page_cache()'::regprocedure) body"))[0].body;
 await db.exec(migration);await db.exec(migration);
 assert.equal((await q("select pg_get_functiondef('refresh_page_cache()'::regprocedure) body"))[0].body,before);
 const ids=Array.from({length:6},(_,i)=>`00000000-0000-0000-0000-${String(i+1).padStart(12,'0')}`);
 for(const role of ['anon','authenticated']){
  await db.exec('set role '+role);
  await assert.rejects(q('select request_page_cache_refresh($1)',[ids[0]]),/permission denied/);
  await assert.rejects(q('select page_cache_refresh_status($1)',[ids[0]]),/permission denied/);
  await assert.rejects(q('select * from ingest_log'),/permission denied/);
  await db.exec('reset role');
 }
 const status=async id=>(await q('select page_cache_refresh_status($1) receipt',[id]))[0].receipt;
 const request=async id=>q('select request_page_cache_refresh($1)',[id]);
 const run=async id=>q('select arbdesk_private.run_page_cache_request($1)',[id]);
 await db.exec('set role service_role');await request(ids[0]);await request(ids[0]);
 assert.equal((await status(ids[0])).status,'queued');
 await assert.rejects(run(ids[0]),/permission denied/);await db.exec('reset role');
 assert.equal((await q('select count(*)::int n from cron.job'))[0].n,1);
 assert.equal((await q('select count(*)::int n from ingest_log'))[0].n,1);
 await run(ids[0]);assert.deepEqual((await status(ids[0])).detail.ms,{mv:42});
 assert.equal((await status(ids[0])).status,'ok');await request(ids[0]);await run(ids[0]);
 assert.equal((await q('select count(*)::int n from cron.job'))[0].n,0);
 assert.equal((await q('select n from control'))[0].n,1);
 await db.exec("update control set mode='busy'");await request(ids[1]);
 for(let i=0;i<3;i++){await run(ids[1]);assert.equal((await status(ids[1])).status,'waiting');}
 await run(ids[1]);assert.equal((await status(ids[1])).status,'error');
 assert.equal((await q('select count(*)::int n from cron.job'))[0].n,0);
 for(const [mode,id] of [['broken',ids[2]],['timeout',ids[3]],['empty',ids[4]]]){
  await q('update control set mode=$1',[mode]);await request(id);await run(id);
  assert.equal((await status(id)).status,'error',mode);
  assert.equal((await q('select count(*)::int n from cron.job'))[0].n,0);
 }
 await request(ids[5]);await q("update ingest_log set logged_at=now()-interval '11 minutes' where job=$1",['page_cache_request:'+ids[5]]);
 await run(ids[5]);assert.equal((await status(ids[5])).status,'error');
 assert.equal((await q('select n from control'))[0].n,1);
 assert.equal((await q('select count(*)::int n from cron.job'))[0].n,0);
 await db.close();console.log('PASS: background refresh receipts are idempotent, bounded, private, self-unscheduled, and acknowledge only completed refreshes');
})().catch(e=>{console.error(e);process.exit(1)});
