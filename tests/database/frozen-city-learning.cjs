const {PGlite}=require('@electric-sql/pglite');
const fs=require('node:fs'); const assert=require('node:assert/strict');
const root=require('node:path').join(__dirname,'../..');
const migration=['20261017170738_frozen_city_learning.sql','20261017174142_frozen_city_shadow_retry.sql','20261018084700_city_predictor_selection.sql'].map(p=>fs.readFileSync(root+'/supabase/migrations/'+p,'utf8')).join('\n');
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
  // A completed, previously receipted baseline choice retains its raw alternative.
  await db.exec(`alter table prediction_checkpoints add column frozen_at timestamptz,
    add column input_as_of timestamptz,add column input_sha256 text,add column raw_probs jsonb,
    add column baseline_probs jsonb,add column predictive_context jsonb;
    insert into cities values('paired-seed','UTC','C');
    insert into prediction_checkpoints(city_key,target_date,checkpoint,local_decision_time,engine_version,model_path,probs,top_band_id,top_prob,priced_from,frozen_at,input_as_of,input_sha256,raw_probs,baseline_probs,predictive_context)
      values('paired-seed',current_date-1,'noon',(current_date-1)+time '12:00','seed','forecast','{"a":0.2,"b":0.8}','b',.8,'baseline:seed',(current_date-1)+time '12:01',(current_date-1)+time '12:00','seed','{"a":0.8,"b":0.2}','{"a":0.2,"b":0.8}','{"raw_model_version":"wxrd3:v3:seed"}');
    insert into fact_checkpoint_outcome select checkpoint_id,'b',current_date::timestamptz,clock_timestamp() from prediction_checkpoints where city_key='paired-seed';`);
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
  const dayAhead=fs.readFileSync(root+'/supabase/migrations/20261110223147_seven_source_day_ahead_checkpoint.sql','utf8');
  await db.exec('create temp table before_day_ahead as select * from prediction_checkpoints');
  await db.exec(dayAhead); await db.exec(dayAhead);
  assert.equal((await db.query(`select count(*)::int n from (
    (select * from before_day_ahead except all select * from prediction_checkpoints)
    union all (select * from prediction_checkpoints except all select * from before_day_ahead)) d`)).rows[0].n,0);
  const earlyOffset=(new Date().getUTCHours()-9+12)%24-12;
  const earlyZone=earlyOffset===0?'UTC':`Etc/GMT${earlyOffset>0?'+':''}${earlyOffset}`;
  await db.query("insert into cities values('new-day-ahead',$1,'C')",[earlyZone]);
  const realEarly=(await db.query(`insert into prediction_checkpoints(city_key,target_date,checkpoint,local_decision_time,engine_version,model_path,probs,top_band_id,top_prob)
    select 'new-day-ahead', (clock_timestamp() at time zone $1)::date+1,'day_ahead',
      (clock_timestamp() at time zone $1)::date+time '09:00','new','forecast','{"a":0.8,"b":0.2}','a',.8 returning frozen_at,target_date`,[earlyZone])).rows[0];
  assert.ok(realEarly.frozen_at);
  await refused(db,"update prediction_checkpoints set top_prob=.9 where city_key='new-day-ahead'");
  const paired=(await db.query("select model_version,probs,raw_probs,baseline_probs from v_city_predictor_pairs where city_key='paired-seed'")).rows[0];
  assert.equal(paired.model_version,'wxrd3:v3:seed');
  assert.deepEqual(paired.probs,{a:.2,b:.8}); assert.deepEqual(paired.raw_probs,{a:.8,b:.2});
  assert.deepEqual(paired.baseline_probs,paired.probs);
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
  await db.query('insert into cities values ($1,$2,$3)',['selected',zone,'C']);
  const choice={policy:'city-horizon-selector-v1',checkpoint:'noon',selected:'baseline',active:true,
    fit_at:now,validated_through:new Date(Date.parse(day+'T00:00Z')-86400000).toISOString().slice(0,10)};
  const selectedCtx={...ctx,raw_model_version:'wxrd3:v3:x',model_selection:choice};
  const selectedPacket={...payload,row:{city_key:'selected',date:day}};
  await db.query("insert into live_capture(kind,key,captured_at,payload) values('wx_features','selected-key',now(),$1::jsonb)",[JSON.stringify(selectedPacket)]);
  const selectedIns=`insert into prediction_checkpoints(city_key,target_date,checkpoint,local_decision_time,engine_version,model_path,probs,top_band_id,top_prob,priced_from,input_key,input_sha256,input_as_of,raw_probs,baseline_probs,baseline_model,predictive_context)
    values('selected',$1::date,'noon',$1::date+time '12:00','v','forecast',$2::jsonb,'b',.8,$3,'selected-key','digest',$4::timestamptz,'{"a":0.8,"b":0.2}','{"a":0.2,"b":0.8}','priced_from:baseline:test',$5::jsonb)`;
  await refused(db,selectedIns,[day,'{"a":0.1,"b":0.8,"c":0.1}','baseline:test',now,JSON.stringify(selectedCtx)]);
  await refused(db,selectedIns,[day,'{"a":0.2,"b":0.8}','wxrd3:v3:x',now,JSON.stringify(selectedCtx)]);
  await refused(db,selectedIns,[day,'{"a":0.2,"b":0.8}','baseline:test',now,JSON.stringify({...selectedCtx,model_selection:{...choice,active:false}})]);
  await refused(db,selectedIns,[day,'{"a":0.2,"b":0.8}','baseline:test',now,JSON.stringify({...selectedCtx,raw_model_version:'wrong-version'})]);
  await refused(db,selectedIns,[day,'{"a":0.2,"b":0.8}','baseline:test',now,JSON.stringify({...selectedCtx,model_selection:{...choice,validated_through:day}})]);
  await db.query(selectedIns,[day,'{"a":0.2,"b":0.8}','baseline:test',now,JSON.stringify(selectedCtx)]);
  assert.equal((await db.query("select priced_from from prediction_checkpoints where city_key='selected'")).rows[0].priced_from,'baseline:test');
  // Product releases must pass the ACTUAL native trigger, not only build_row.
  await db.exec('alter table prediction_checkpoints add column if not exists station text');
  const production=fs.readFileSync(root+'/supabase/migrations/20261111152417_validated_product_releases.sql','utf8');
  const frozenBefore=(await db.query('select jsonb_agg(to_jsonb(p) order by checkpoint_id) v from prediction_checkpoints p')).rows[0].v;
  await db.exec(production);await db.exec(production);
  assert.deepEqual((await db.query('select jsonb_agg(to_jsonb(p) order by checkpoint_id) v from prediction_checkpoints p')).rows[0].v,frozenBefore);
  const d=n=>new Date(Date.now()+n*86400000).toISOString().slice(0,10);
  const productProof={policy:'validated-product-v1',original_frozen_calls:true,as_of:d(0),fit_last:d(-35),validation_first:d(-16),validation_last:d(-3),training_dates:20,dates:14,calls:14,expected_calls:14,candidate_hits:10,incumbent_hits:7,market_hits:8,candidate_brier:.2,incumbent_brier:.3,market_brier:.25,candidate_nll:.3,incumbent_nll:.5,market_nll:.4,incumbent_gain_lower90:.1,market_gain_lower90:.05};
  let sequence=0;
  async function releaseFor(city,checkpoint,kind,identity,payload={}) {
    const release={policy:'validated-product-v1',kind,city_key:city,checkpoint,station:'TEST',unit:'C',identity,
      release_id:(++sequence).toString(16).padStart(64,'0'),code_sha256:'a'.repeat(64),payload_sha256:'b'.repeat(64),proof_sha256:'c'.repeat(64),payload,proof:productProof};
    const id=(await db.query('select publish_predictive_product($1) id',[release])).rows[0].id;
    const record=(await db.query('select receipt_id,recorded_at::text stamp from arbdesk_private.predictive_product_releases where receipt_id=$1',[id])).rows[0];
    return {release,id,stamp:record.stamp};
  }
  async function productCall(city,selected='wxrd3',contract='awc-routine-v1') {
    await db.query('insert into cities values($1,$2,$3)',[city,zone,'C']);
    const rawVersion='wxrd3:v3:product',original=selected==='baseline'?{a:.6,b:.4}:{a:.8,b:.2};
    const saved=await releaseFor(city,'noon','current_forecast',city+'|TEST|'+(contract||'legacy')+'|'+rawVersion);
    const stamp=new Date().toISOString(),key=city+'|features';
    const packet={policy:'wxrd3-horizons-v1',version:rawVersion,model_fit_through:d(-35),row_sha256:'digest',as_of:stamp,band_ids:['a','b'],
      current_forecasts_sha256:'d'.repeat(64),current_receipts_sha256:'e'.repeat(64),
      row:{city_key:city,date:day,station:'TEST',unit:'C',observation_contract:contract}};
    await db.query("insert into live_capture(kind,key,captured_at,payload) values('wx_features',$1,clock_timestamp(),$2)",[key,packet]);
    const ctx={band_ids:['a','b'],model_fit_through:packet.model_fit_through,raw_model_version:rawVersion,
      current_forecasts_sha256:packet.current_forecasts_sha256,current_receipts_sha256:packet.current_receipts_sha256,
      model_selection:{policy:'city-horizon-selector-v1',checkpoint:'noon',selected,active:selected==='baseline',fit_at:stamp,validated_through:d(-1)},
      product_release:{policy:'validated-product-v1',city_key:city,target_date:day,checkpoint:'noon',receipt_id:saved.id,release_id:saved.release.release_id,
        kind:'current_forecast',recorded_at:saved.stamp,incumbent_probs:original,incumbent_priced_from:selected==='baseline'?'baseline:test':rawVersion,
        served_probs:{a:.2,b:.8},payload_sha256:saved.release.payload_sha256,proof_sha256:saved.release.proof_sha256}};
    return {city,saved,stamp,key,ctx,station:'TEST',label:'predictive-product:current_forecast:'+saved.release.release_id};
  }
  async function insertProduct(x) {
    return db.query(`insert into prediction_checkpoints(city_key,target_date,checkpoint,local_decision_time,engine_version,model_path,probs,top_band_id,top_prob,priced_from,input_key,input_sha256,input_as_of,raw_probs,baseline_probs,baseline_model,predictive_context,station)
      values($1,$2::date,'noon',$2::date+time '12:00','product','wxpredict','{"a":0.2,"b":0.8}','b',.8,$3,$4,'digest',$5::timestamptz,'{"a":0.8,"b":0.2}','{"a":0.6,"b":0.4}','priced_from:baseline:test',$6,$7) returning *`,
      [x.city,day,x.label,x.key,x.stamp,x.ctx,x.station]);
  }
  for(const [selected,contract,suffix] of [['wxrd3','awc-routine-v1','raw'],['baseline','awc-routine-v1','baseline'],['wxrd3','','legacy']]) {
    const x=await productCall('product-'+suffix,selected,contract);
    await db.exec('set role service_role');
    const row=(await insertProduct(x)).rows[0];
    await db.exec('reset role');
    assert.deepEqual(row.probs,{a:.2,b:.8});assert.deepEqual(row.predictive_context.product_release.incumbent_probs,x.ctx.product_release.incumbent_probs);
    assert.equal(row.predictive_context.raw_model_version,'wxrd3:v3:product');assert.ok(row.frozen_at);
  }
  for(const fault of ['missing','wrong_receipt','wrong_label','wrong_station','wrong_incumbent','wrong_current_hash','revoked','expired','settled']) {
    const x=await productCall('product-bad-'+fault,'baseline');
    if(fault==='missing')delete x.ctx.product_release;
    if(fault==='wrong_receipt')x.ctx.product_release.receipt_id=999999;
    if(fault==='wrong_label')x.ctx.product_release.incumbent_priced_from='different:predictor';
    if(fault==='wrong_station')x.station='WRONG';
    if(fault==='wrong_incumbent')x.ctx.product_release.incumbent_probs={a:.5,b:.5};
    if(fault==='wrong_current_hash')x.ctx.current_receipts_sha256='f'.repeat(64);
    if(fault==='revoked')await db.query('select revoke_predictive_product($1,$2)',[x.saved.id,'Synthetic rollback test']);
    if(fault==='expired')await db.query("update arbdesk_private.predictive_product_releases set expires_at=clock_timestamp()-interval '1 second' where receipt_id=$1",[x.saved.id]);
    if(fault==='settled')await db.query("insert into venue values($1,$2,'confirmed')",[x.city,day]);
    await assert.rejects(()=>insertProduct(x),/Freeze refused/);
  }
  // Early source calls have no primary selector; require their exact pinned
  // trial, candidate and original source/rule receipts instead.
  const earlyCity='product-early',earlyDay=realEarly.target_date.toISOString().slice(0,10),earlyIdentity='early-seven-source-v1:awc-routine-v1:TEST:C';
  await db.query('insert into cities values($1,$2,$3)',[earlyCity,earlyZone,'C']);
  const trial={id:'f'.repeat(64),decision:'approved',weights_sha256:'d'.repeat(64),proof:productProof};
  await db.query('insert into derived_wx_city_calibration(model_version,city_key,phase,as_of,version,state) values($1,$2,$3,current_date,$4,$5)',[earlyIdentity,earlyCity,'day_ahead','synthetic',{production_trial:trial}]);
  await db.query('select record_predictive_product_trial($1,$2,$3,$4,$5)',[earlyCity,'day_ahead',earlyIdentity,trial,'e'.repeat(64)]);
  const er=await releaseFor(earlyCity,'day_ahead','early_ensemble',earlyIdentity,{production_trial:trial}),es=new Date().toISOString();
  const earlyContext={product_release:{policy:'validated-product-v1',city_key:earlyCity,target_date:earlyDay,checkpoint:'day_ahead',receipt_id:er.id,release_id:er.release.release_id,kind:'early_ensemble',recorded_at:er.stamp,
    incumbent_probs:{a:.8,b:.2},incumbent_priced_from:'forecast:original',served_probs:{a:.2,b:.8},payload_sha256:er.release.payload_sha256,proof_sha256:er.release.proof_sha256},
    ensemble_early:{policy:'early-seven-source-v1',model_version:earlyIdentity,production_trial_id:trial.id,production_weights_sha256:trial.weights_sha256,
      production_candidate_probs:{a:.2,b:.8},incumbent_probs:{a:.8,b:.2},station:'TEST',unit:'C',captured_at:es,input_oldest:es,input_newest:es,rules_received_at:es,rules_sha256:'a'.repeat(64)}};
  earlyContext.product_ensemble_serving=JSON.parse(JSON.stringify(earlyContext.ensemble_early));
  earlyContext.ensemble_early.production_reference_probs={a:.2,b:.8};
  const earlyInsert=`insert into prediction_checkpoints(city_key,target_date,checkpoint,local_decision_time,engine_version,model_path,probs,top_band_id,top_prob,priced_from,predictive_context,station)
    values($1,$2::date,'day_ahead',$2::date-1+time '09:00','product','forecast','{"a":0.2,"b":0.8}','b',.8,$3,$4,'TEST') returning frozen_at`;
  const wrongEarly=JSON.parse(JSON.stringify(earlyContext));wrongEarly.product_ensemble_serving.production_weights_sha256='0'.repeat(64);
  await assert.rejects(()=>db.query(earlyInsert,[earlyCity,earlyDay,'predictive-product:early_ensemble:'+er.release.release_id,wrongEarly]),/Freeze refused/);
  await db.exec('set role service_role');
  assert.ok((await db.query(earlyInsert,[earlyCity,earlyDay,'predictive-product:early_ensemble:'+er.release.release_id,earlyContext])).rows[0].frozen_at);
  await db.exec('reset role');

  await db.query("insert into venue values('valid',$1,'confirmed')",[day]);
  await refused(db,ins,[day,'post-settlement','{"a":0.8,"b":0.2}','a',.8,new Date().toISOString()]);
  await db.query("insert into derived_wx_city_calibration values('v','valid','before_peak',current_date,'fit','{}','2000-01-01')");
  assert.ok((await db.query('select computed_at from derived_wx_city_calibration')).rows[0].computed_at.getUTCFullYear()>=2026);
  await db.query("insert into derived_city_prediction_validation values('valid','noon','served',now(),'{\"n\":0}')");
  await db.exec('set role anon');
  assert.equal((await db.query('select count(*)::int n from v_city_prediction_validation')).rows[0].n,1);
  await refused(db,'select * from v_wx_frozen_learning');
  await refused(db,'select * from v_day_ahead_frozen_validation');
  await refused(db,'select * from v_city_predictor_pairs');
  await refused(db,"insert into derived_city_prediction_validation values('x','noon','served',now(),'{}')");
  await db.close(); console.log('frozen-city-learning: server receipt, first-call identity, late insert refusal, immutable history, paired predictor selection, health equivalence and role contracts passed');
})().catch(e=>{console.error(e);process.exit(1)});
