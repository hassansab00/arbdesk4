const {PGlite}=require('@electric-sql/pglite');
const fs=require('node:fs'); const assert=require('node:assert/strict');
const root=require('node:path').join(__dirname,'../..');
const migration=fs.readFileSync(root+'/supabase/migrations/20261017170738_frozen_city_learning.sql','utf8')+'\n'+fs.readFileSync(root+'/supabase/migrations/20261017174142_frozen_city_shadow_retry.sql','utf8');
async function refused(db,sql,params=[]){await assert.rejects(()=>db.query(sql,params));}
(async()=>{
  const db=new PGlite();
  await db.exec(`create role anon; create role authenticated; create role service_role bypassrls;
    create schema arbdesk_private;
    create function arbdesk_private.immutable_record() returns trigger language plpgsql as $$begin raise exception 'immutable'; end$$;
    create table cities(city_key text primary key,timezone text,unit text);
    create table derived_weather_peak(city_key text,month int,peak_hour_local numeric);
    create table venue(city_key text,resolution_date date,resolution_state text);
    create view v_venue_market_resolution as select * from venue;
    create table live_capture(capture_id bigserial primary key,kind text,key text,captured_at timestamptz,payload jsonb,written_at timestamptz default clock_timestamp());
    create table data_freshness_spec(table_name text primary key,ts_column text,fresh_hours numeric,layer text,plain_english text);
    create view v_data_freshness with(security_barrier=true) as select 'old'::text table_name,'model'::text layer,'Old rows.'::text plain_english,7::bigint rows,false rows_estimated,now() newest,0::numeric age_hours,36::numeric fresh_hours,'ok'::text state;
    grant select on v_data_freshness to anon;
    create view v_prediction_hindsight_live as select 'old'::text city_key,current_date for_date,now() called_at,true hit,50::numeric predicted_pct,.5::numeric prob_on_winner,'old'::text engine_version,1::numeric forecast_error_c,'day_ahead'::text called_when;`);
  await db.exec(fs.readFileSync(root+'/supabase/migrations/20260924000000_prediction_checkpoints.sql','utf8'));
  await db.exec(`alter table prediction_checkpoints add column priced_from text;
    create table fact_checkpoint_outcome(checkpoint_id uuid primary key,winner_band_id text,settled_at timestamptz,banked_at timestamptz);
    insert into prediction_checkpoints(city_key,target_date,checkpoint,local_decision_time,engine_version,model_path,probs,top_band_id,top_prob)
      values('legacy','2026-09-01','noon','2026-09-01 12:00','old','forecast','{"a":0.8,"b":0.2}','a',.8);`);
  const before=(await db.query("select to_jsonb(p) p from prediction_checkpoints p where city_key='legacy'")).rows[0].p;
  await db.exec(`create table variant_shadow_checkpoints(city_key text,target_date date,checkpoint text,decided_at timestamptz default now());
    create table s10_shadow_checkpoints(city_key text,target_date date,checkpoint text,decided_at timestamptz default now());`);
  const utcHour=new Date().getUTCHours(),shift=utcHour-12;
  const retryZone=shift===0?'UTC':`Etc/GMT${shift>0?'+':''}${shift}`;
  const retryDay=new Date(Date.now()-shift*3600000).toISOString().slice(0,10);
  await db.query("insert into cities values('retry',$1,'C'),('too-old',$1,'C')",[retryZone]);
  await db.query(`insert into prediction_checkpoints(city_key,target_date,checkpoint,local_decision_time,engine_version,model_path,probs,top_band_id,top_prob,decided_at)
    values('retry',$1::date,'morning',$1::date+time '09:00','old','forecast','{"a":0.8,"b":0.2}','a',.8,clock_timestamp()-interval '1 hour'),
          ('too-old',$1::date,'morning',$1::date+time '09:00','old','forecast','{"a":0.8,"b":0.2}','a',.8,clock_timestamp()-interval '7 hours')`,[retryDay]);
  await db.exec(migration); await db.exec(migration);
  const legacy=(await db.query("select to_jsonb(p)-array['frozen_at','input_as_of','input_key','input_sha256','raw_probs','baseline_probs','baseline_model','calibration','predictive_context'] p,frozen_at from prediction_checkpoints p where city_key='legacy'")).rows[0];
  assert.deepEqual(legacy.p,before); assert.equal(legacy.frozen_at,null);
  assert.equal((await db.query('select count(*)::int n from v_data_freshness')).rows[0].n,3);
  assert.deepEqual((await db.query("select rows,rows_estimated,state from v_data_freshness where table_name='old'")).rows[0],{rows:7,rows_estimated:false,state:'ok'});
  assert.deepEqual((await db.query("select reloptions from pg_class where oid='v_data_freshness'::regclass")).rows[0].reloptions,['security_barrier=true']);
  for(const table of ['variant_shadow_checkpoints','s10_shadow_checkpoints']) {
    await db.query(`insert into ${table}(city_key,target_date,checkpoint,decided_at) select city_key,target_date,checkpoint,decided_at from prediction_checkpoints where city_key='retry'`);
    await refused(db,`insert into ${table}(city_key,target_date,checkpoint) values('retry',$1,'morning')`,[retryDay]);
    await refused(db,`insert into ${table}(city_key,target_date,checkpoint,decided_at) select city_key,target_date,checkpoint,decided_at from prediction_checkpoints where city_key='too-old'`);
  }
  const hour=new Date().getUTCHours(); const offset=hour-12;
  const zone=offset===0?'UTC':`Etc/GMT${offset>0?'+':''}${offset}`;
  const day=new Date(Date.now()-offset*3600000).toISOString().slice(0,10);
  await db.query('insert into cities values ($1,$2,$3)',['valid',zone,'C']);
  const ins=`insert into prediction_checkpoints(city_key,target_date,checkpoint,local_decision_time,engine_version,model_path,probs,top_band_id,top_prob,decided_at,frozen_at)
    values('valid',$1::date,'noon',$1::date+time '12:00',$2,'forecast',$3::jsonb,$4,$5,$6::timestamptz,'2000-01-01') returning *`;
  const now=new Date().toISOString();
  await refused(db,ins,[day,'early','{"a":0.8,"b":0.2}','a',.8,'2000-01-01']);
  await refused(db,ins,[day,'badtop','{"a":0.8,"b":0.2}','b',.2,now]);
  await refused(db,ins,[day,'negative','{"a":1.2,"b":-0.2}','a',1.2,now]);
  const row=(await db.query(ins,[day,'first','{"a":0.8,"b":0.2}','a',.8,now])).rows[0];
  assert.ok(new Date(row.frozen_at).getTime()>Date.parse('2026-01-01'));
  assert.deepEqual(row.frozen_at,row.decided_at);
  const duplicate=await db.query(ins,[day,'replacement','{"a":0.01,"b":0.99}','b',.99,new Date().toISOString()]);
  assert.equal(duplicate.rows.length,0);
  await refused(db,'update prediction_checkpoints set top_prob=.99');
  await refused(db,'delete from prediction_checkpoints'); await refused(db,'truncate prediction_checkpoints');
  for(const table of ['variant_shadow_checkpoints','s10_shadow_checkpoints']) {
    await db.query(`insert into ${table}(city_key,target_date,checkpoint) values('valid',$1,'noon')`,[day]);
    await refused(db,`insert into ${table}(city_key,target_date,checkpoint) values('valid',$1,'morning')`,[day]);
  }
  await db.query('insert into cities values ($1,$2,$3)',['packet',zone,'C']);
  const packetIns=`insert into prediction_checkpoints(city_key,target_date,checkpoint,local_decision_time,engine_version,model_path,probs,top_band_id,top_prob,priced_from,input_key,input_sha256,input_as_of,raw_probs,baseline_probs,predictive_context)
    values('packet',$1::date,'noon',$1::date+time '12:00','v','wxpredict','{"a":0.8,"b":0.2}','a',.8,'wxrd3:v3:x','key','digest',$2::timestamptz,'{"a":0.8,"b":0.2}','{"a":0.6,"b":0.4}',$3::jsonb)`;
  const ctx={band_ids:['a','b'],model_fit_through:'2026-01-01'};
  await refused(db,packetIns,[day,now,JSON.stringify(ctx)]);
  const payload={policy:'wxrd3-horizons-v1',version:'wxrd3:v3:x',model_fit_through:'2026-01-01',row_sha256:'digest',as_of:now,band_ids:['a','b'],row:{city_key:'packet',date:day}};
  await db.query("insert into live_capture(kind,key,captured_at,payload) values('wx_features','key',now(),$1::jsonb)",[JSON.stringify(payload)]);
  await db.query(packetIns,[day,now,JSON.stringify(ctx)]);
  assert.equal((await db.query("select count(*)::int n from prediction_checkpoints where city_key='packet'")).rows[0].n,1);
  await db.query("insert into venue values('valid',$1,'confirmed')",[day]);
  await refused(db,ins,[day,'post-settlement','{"a":0.8,"b":0.2}','a',.8,new Date().toISOString()]);
  await db.query("insert into derived_wx_city_calibration values('v','valid','before_peak',current_date,'fit','{}','2000-01-01')");
  assert.ok((await db.query('select computed_at from derived_wx_city_calibration')).rows[0].computed_at.getUTCFullYear()>=2026);
  await db.query("insert into derived_city_prediction_validation values('valid','noon','served',now(),'{\"n\":0}')");
  await db.exec('set role anon');
  assert.equal((await db.query('select count(*)::int n from v_city_prediction_validation')).rows[0].n,1);
  await refused(db,'select * from v_wx_frozen_learning');
  await refused(db,'select * from v_day_ahead_frozen_validation');
  await refused(db,"insert into derived_city_prediction_validation values('x','noon','served',now(),'{}')");
  await db.close(); console.log('frozen-city-learning: server receipt, first-call identity, late insert refusal, immutable history, health equivalence and role contracts passed');
})().catch(e=>{console.error(e);process.exit(1)});
