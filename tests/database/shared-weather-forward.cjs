const {PGlite}=require('@electric-sql/pglite');
const fs=require('fs'),path=require('path'),assert=require('node:assert/strict');
const migration=fs.readFileSync(path.join(__dirname,'../../supabase/migrations/20261103170852_isolated_shared_weather_forward.sql'),'utf8');
(async()=>{
 const db=new PGlite();const q=async(s,p)=>(await db.query(s,p)).rows;
 let offset=9-new Date().getUTCHours();if(offset< -12)offset+=24;
 const timezone=offset===0?'UTC':`Etc/GMT${offset>0?'-':'+'}${Math.abs(offset)}`;
 await db.exec(`create role anon;create role authenticated;create role service_role bypassrls;
 create table public.cities(city_key text primary key,timezone text,unit text,display_name text);
 create table public.prediction_checkpoints(checkpoint_id uuid primary key default gen_random_uuid(),city_key text,target_date date,
 checkpoint text,decided_at timestamptz default clock_timestamp(),frozen_at timestamptz default clock_timestamp(),inputs_ok boolean default true,
 probs jsonb,market jsonb,top_band_id text,predictive_context jsonb);
 create table public.fact_checkpoint_outcome(checkpoint_id uuid,city_key text,target_date date,checkpoint text,winner_band_id text,
 settled_at timestamptz,ladder_has_winner boolean);
 create table public.live_capture(capture_id bigserial primary key,kind text,key text,captured_at timestamptz,payload jsonb,written_at timestamptz);
 create table public.bands(band_id text,band_lo numeric,band_hi numeric,band_label text);
 create view public.v_canonical_bands as select * from public.bands;
 insert into public.bands values('a',null,20,'19 or below'),('b',20,null,'20 or above');`);
 await q(`insert into cities select 'city'||i,$1,'C','Synthetic '||i from generate_series(1,20) i`,[timezone]);
 const schemaSQL=`select c.relname,a.attname,a.atttypid,a.attnotnull from pg_class c join pg_attribute a on a.attrelid=c.oid
 where c.relnamespace='public'::regnamespace and c.relname in ('prediction_checkpoints','live_capture','fact_checkpoint_outcome') and a.attnum>0 order by c.relname,a.attnum`;
 const before=await q(schemaSQL);await db.exec(migration);await db.exec(migration);assert.deepEqual(await q(schemaSQL),before);
 const times=(await q(`select clock_timestamp()-interval '1 hour' started,clock_timestamp()-interval '30 minutes' available,
 (current_date-2)::text through,(clock_timestamp()-interval '2 hours')::text cutoff`))[0];
 const artifact={policy:'shared-early-weather-v1',sha256:'a'.repeat(64),changes_serving:false,eligible_for_promotion:false,
 fit_started_at:times.started,available_at:times.available,training:{dates:15,rows:1352,through:times.through,evidence_cutoff:times.cutoff}};
 const cohort=Array.from({length:20},(_,i)=>'city'+(i+1));
 await db.exec('set role anon');await assert.rejects(q('select shared_weather_register($1,$2)',[artifact,cohort]),/permission denied/);
 await assert.rejects(q('select * from arbdesk_private.shared_weather_models'),/permission denied/);
 await assert.rejects(q('select * from shared_weather_calls'),/permission denied/);
 await db.exec('reset role;set role service_role');
 await assert.rejects(q('select shared_weather_register($1,$2)',[{...artifact,training:{}},cohort]),/registration/);
 const registered=(await q('select shared_weather_register($1,$2) r',[artifact,cohort]))[0].r;
 assert.equal(registered.evaluation_last,new Date(new Date(registered.evaluation_first).valueOf()+19*86400000).toISOString().slice(0,10));
 await assert.rejects(q('select shared_weather_register($1,$2)',[{...artifact,extra:'changed'},cohort]),/identity conflict/);
 await db.exec('reset role');
 const weather=(await q(`insert into live_capture(kind,key,captured_at,payload,written_at)
 select 'hourly0','city1|snapshot',clock_timestamp()-interval '2 minutes',jsonb_build_object('schema','nwp-current-snapshot-v1','hours',
 (select jsonb_object_agg(to_char(t at time zone 'UTC','YYYY-MM-DD"T"HH24:MI'),jsonb_build_array(
 case when extract(hour from t at time zone $1)=12 then 30 else 20 end,15,30,100,10,0,1010))
 from generate_series(date_trunc('day',clock_timestamp() at time zone $1) at time zone $1,
 (date_trunc('day',clock_timestamp() at time zone $1)+interval '1 day') at time zone $1 - interval '1 hour',interval '1 hour') t)),
 clock_timestamp()-interval '1 minute' returning capture_id,payload`,[timezone]))[0];
 const call=(await q(`insert into prediction_checkpoints(city_key,target_date,checkpoint,probs,market,top_band_id,predictive_context)
 values('city1',(clock_timestamp() at time zone $1)::date,'morning','{"a":0.6,"b":0.4}',
 '{"a":{"bid":0.29,"ask":0.31},"b":{"bid":0.69,"ask":0.71}}','a',jsonb_build_object('timing_reference',jsonb_build_object(
 'city_key','city1','timezone',$1,'month',extract(month from clock_timestamp() at time zone $1),
 'computed_at',clock_timestamp()-interval '30 days','peak_hour_local',12))) returning checkpoint_id`,[timezone]))[0].checkpoint_id;
 const row={source_checkpoint_id:call,weather_capture_id:weather.capture_id,probs:{a:.4,b:.6},market:{a:.99,b:.01},
 context:{timezone,station:'TEST',candidate_label:'20 or above',incumbent_label:'19 or below'}};
 await db.exec('set role service_role');
 assert.equal((await q('select shared_weather_inputs() r'))[0].r.calls.length,1);
 assert.equal((await q('select shared_weather_freeze($1,$2) r',[artifact.sha256,[row]]))[0].r.new,1);
 const held=await q('select * from shared_weather_calls');assert.equal(held.length,1);
 assert.equal(Number(held[0].market.b),.7,'market is derived from the original quote, not the supplied fake comparison');
 assert.equal((await q('select shared_weather_freeze($1,$2) r',[artifact.sha256,[{...row,probs:{a:.9,b:.1}}]]))[0].r.new,0);
 assert.deepEqual(await q('select * from shared_weather_calls'),held);
 await assert.rejects(q(`update shared_weather_calls set probs='{}'`),/permission denied/);
 await db.exec('reset role');await assert.rejects(q(`update shared_weather_calls set probs='{}'`),/immutable/);
 await assert.rejects(q('delete from shared_weather_calls'),/immutable/);
 await q(`update live_capture set written_at=clock_timestamp()+interval '1 minute' where capture_id=$1`,[weather.capture_id]);
 await db.exec('set role service_role');await assert.rejects(q('select shared_weather_freeze($1,$2)',[artifact.sha256,[row]]),/forward evidence/);
 await db.exec('reset role');await q(`update live_capture set written_at=clock_timestamp()-interval '1 minute',payload=jsonb_set(payload,'{hours}',(payload->'hours')-(select min(key) from jsonb_each(payload->'hours'))) where capture_id=$1`,[weather.capture_id]);
 await db.exec('set role service_role');await assert.rejects(q('select shared_weather_freeze($1,$2)',[artifact.sha256,[row]]),/incomplete/);
 await db.exec('reset role');await q('update live_capture set payload=$1 where capture_id=$2',[weather.payload,weather.capture_id]);
 await q(`update prediction_checkpoints set predictive_context=jsonb_set(predictive_context,'{timing_reference,peak_hour_local}','9') where checkpoint_id=$1`,[call]);
 await db.exec('set role service_role');await assert.rejects(q('select shared_weather_freeze($1,$2)',[artifact.sha256,[row]]),/known peak/);
 await db.exec('reset role');await q(`update prediction_checkpoints set decided_at=clock_timestamp()-interval '6 minutes',frozen_at=clock_timestamp()-interval '6 minutes' where checkpoint_id=$1`,[call]);
 await db.exec('set role service_role');await assert.rejects(q('select shared_weather_freeze($1,$2)',[artifact.sha256,[row]]),/forward evidence/);
 await db.exec('reset role');
 // Synthetic past records test scoring ordering; they are never inserted by
 // the forward writer and cannot count inside its future evaluation window.
 const old=(await q(`insert into shared_weather_calls(model_sha256,city_key,target_date,checkpoint,source_checkpoint_id,decided_at,frozen_at,
 probs,incumbent,market,top_band_id,incumbent_top,context) values($1,'city1',(clock_timestamp() at time zone $2)::date-1,'morning',
 gen_random_uuid(),clock_timestamp()-interval '2 days',clock_timestamp()-interval '2 days','{"a":0.4,"b":0.6}',
 '{"a":0.6,"b":0.4}','{"a":0.3,"b":0.7}','b','a','{}') returning *`,[artifact.sha256,timezone]))[0];
 await q(`insert into fact_checkpoint_outcome values($1,'city1',$2,'morning','b',clock_timestamp()-interval '1 hour',true)`,[old.source_checkpoint_id,old.target_date]);
 await db.exec('set role service_role');assert.equal((await q('select shared_weather_score() n'))[0].n,1);
 assert.equal((await q('select shared_weather_score() n'))[0].n,0);
 const score=(await q('select * from shared_weather_scores'))[0];assert.equal(score.candidate.hit,true);assert.equal(score.incumbent.hit,false);assert.equal(score.market.hit,true);
 assert.ok(Math.abs(score.candidate.log_loss+Math.log(.6))<1e-10);
 const snapshot=(await q('select shared_weather_snapshot() s'))[0].s;assert.ok(!JSON.stringify(snapshot).includes('coefficients'));
 assert.equal(snapshot.scores.length,0,'earlier calls cannot approve this fixed future candidate');
 await db.exec('reset role;set role anon');await assert.rejects(q('select shared_weather_inputs()'),/permission denied/);
 await assert.rejects(q('select shared_weather_snapshot()'),/permission denied/);
 await db.close();console.log('PASS: isolated shared weather: private weights, unchanged source schemas, original quotes, city clocks, complete received weather, peak gates, immutable freezes and exact later scores');
})().catch(e=>{console.error(e);process.exit(1)});
