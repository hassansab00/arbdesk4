const {PGlite}=require('@electric-sql/pglite');
const fs=require('node:fs'),assert=require('node:assert/strict');
const root=require('node:path').join(__dirname,'../..');
const migration=fs.readFileSync(root+'/supabase/migrations/20261112234255_disk_io_budget_repair.sql','utf8')+
 fs.readFileSync(root+'/supabase/migrations/20261113081006_cache_source_invalidation.sql','utf8');
(async()=>{
 const db=new PGlite();
 const q=async(sql,args=[])=>(await db.query(sql,args)).rows;
 await db.exec(`create role anon; create role authenticated; create role service_role;
 create table ingest_log(log_id bigserial primary key,job text,status text,detail jsonb,logged_at timestamptz default now(),finished_at timestamptz);
 create function log_ingest(text,text,int,jsonb) returns void language sql as $$insert into ingest_log(job,status,detail) values($1,$2,$4)$$;
 create table prediction_checkpoints(city_key text,target_date date,checkpoint text,decided_at timestamptz,checkpoint_id uuid);
 create table job_sla(job text primary key,cadence text,cadence_minutes int,max_age_minutes int generated always as(cadence_minutes+greatest(30,cadence_minutes/12)) stored);
 insert into job_sla values('refresh_page_cache','old',30,default);
 create schema cron;
 create table cron.job(jobid bigserial primary key,jobname text unique,schedule text,command text);
 create function cron.schedule(text,text,text) returns bigint language plpgsql as $$declare n bigint;begin
 insert into cron.job(jobname,schedule,command) values($1,$2,$3) on conflict(jobname) do update set schedule=excluded.schedule,command=excluded.command returning jobid into n;return n;end$$;
 create function cron.unschedule(bigint) returns boolean language plpgsql as $$begin delete from cron.job where jobid=$1;return found;end$$;
 insert into cron.job(jobname,schedule,command) values('old','* * * * *','VACUUM (FULL, ANALYZE) public.weather_forecasts'),('other','1 * * * *','select 1');
 create table weather_forecasts(id int primary key); insert into weather_forecasts values(1);
 create table input_data(id int primary key,value int); insert into input_data values(1,10);
 create table status_count(n int); insert into status_count values(0);
 create function refresh_page_status() returns jsonb language plpgsql as $$begin update status_count set n=n+1;return '{}'::jsonb;end$$;`);
 const fast=['mv_opportunities','mv_prediction_ladder','mv_city_ladder_edges'];
 const research=['mv_forecast_convergence_all','mv_city_hit_history','mv_prediction_hindsight','mv_edge_scaling','mv_calibration','mv_edge_realisation','mv_model_forecast_skill','mv_condition_skill','mv_checkpoint_scoreboard'];
 for(const m of [...fast,...research])await db.exec(`create materialized view ${m} as select * from input_data;create unique index ${m}_key on ${m}(id);`);
 await db.exec(migration); await db.exec(migration);
 assert.equal((await q("select count(*)::int n from cron.job where command ~* 'vacuum.*full'"))[0].n,0);
 assert.equal((await q("select count(*)::int n from cron.job where jobname='other'"))[0].n,1);
 assert.equal((await q("select cadence_minutes,max_age_minutes from job_sla"))[0].max_age_minutes,90);
 assert.equal((await q("select schedule from cron.job where jobname='ad4_refresh_research_cache'"))[0].schedule,'47 3,9,15,21 * * *');
 assert.match((await q("select reloptions::text opts from pg_class where oid='weather_forecasts'::regclass"))[0].opts,/autovacuum_enabled=true/);
 const tokenDefinition=(await q("select pg_get_functiondef('arbdesk_private.page_cache_source_token()'::regprocedure) body"))[0].body;
 assert.match(tokenDefinition,/c.relkind in \('r','p'\)/);
 await db.exec('update input_data set value=11;select pg_stat_force_next_flush();select pg_stat_clear_snapshot();');
 const tokenBefore=(await q('select arbdesk_private.page_cache_source_token() token'))[0].token;
 await q("select arbdesk_private.refresh_page_cache_group('fast')");
 await db.exec('select pg_stat_force_next_flush();select pg_stat_clear_snapshot();');
 assert.equal((await q('select arbdesk_private.page_cache_source_token() token'))[0].token,tokenBefore,
   'refreshing materialized views must not change the source token');
 // Deterministic source changes exercise invalidation without waiting for statistics.
 await db.exec(`create or replace function arbdesk_private.page_cache_source_token() returns text language sql stable as $$select value::text from input_data where id=1$$;
 update input_data set value=11;`);
 const refresh=async()=> (await q('select refresh_page_cache() result'))[0].result;
 let result=await refresh(); assert.deepEqual(Object.keys(result).sort(),fast.sort());
 for(const m of fast)assert.equal((await q(`select value from ${m}`))[0].value,11);
 for(const m of research)assert.equal((await q(`select value from ${m}`))[0].value,10);
 assert.deepEqual(await refresh(),{unchanged:true});
 assert.equal((await q('select n from status_count'))[0].n,2);
 result=(await q('select arbdesk_private.refresh_research_cache() result'))[0].result;
 assert.deepEqual(Object.keys(result).sort(),research.sort());
 for(const m of research)assert.equal((await q(`select value from ${m}`))[0].value,11);
 await db.exec('update input_data set value=12');
 assert.equal((await refresh()).unchanged,undefined);
 assert.equal((await q('select value from mv_opportunities'))[0].value,12);
 const receipt=(await q("select request_reclaim('weather_forecasts') result"))[0].result;
 assert.deepEqual(receipt,{ok:true,table:'weather_forecasts',mode:'autovacuum',completed:false});
 assert.equal((await q('select count(*)::int n from weather_forecasts'))[0].n,1);
 await assert.rejects(q("select request_reclaim('input_data')"),/not a table the archive prunes/);
 // The existing durable worker accepts unchanged work once and unschedules it.
 await db.exec(fs.readFileSync(root+'/sql/ad4_page_cache_requests.sql','utf8'));
 const id='00000000-0000-0000-0000-000000000001';
 await q('select request_page_cache_refresh($1)',[id]);
 await q('select arbdesk_private.run_page_cache_request($1)',[id]);
 const terminal=(await q('select page_cache_refresh_status($1) result',[id]))[0].result;
 assert.equal(terminal.status,'ok'); assert.deepEqual(terminal.detail.ms,{unchanged:true});
 assert.equal((await q("select count(*)::int n from cron.job where jobname like 'ad4_page_cache_%'"))[0].n,0);
 for(const role of ['anon','authenticated']){
   await db.exec('set role '+role);
   await assert.rejects(q('select * from arbdesk_private.page_cache_refresh_state'),/permission denied/);
   await assert.rejects(q("select arbdesk_private.refresh_research_cache()"),/permission denied/);
   await assert.rejects(q("select request_reclaim('weather_forecasts')"),/permission denied/);
   await db.exec('reset role');
 }
 await db.close();console.log('PASS disk IO: repeat install, grouped refresh, unchanged completion, invalidation, maintenance receipts and private access');
})().catch(e=>{console.error(e);process.exit(1)});
