const {PGlite}=require('@electric-sql/pglite');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
(async()=>{
 const db=new PGlite();const q=async(s,p)=>(await db.query(s,p)).rows;
 let offset=9-new Date().getUTCHours();if(offset< -12)offset+=24;
 const zone=offset===0?'UTC':`Etc/GMT${offset>0?'-':'+'}${Math.abs(offset)}`;
 await db.exec(`create role anon;create role authenticated;create role service_role bypassrls;
 create table cities(city_key text primary key,timezone text,unit text,display_name text,status text,icao text,latitude numeric,longitude numeric);
 create table prediction_checkpoints(checkpoint_id uuid primary key default gen_random_uuid(),city_key text,target_date date,checkpoint text,
 decided_at timestamptz,frozen_at timestamptz,inputs_ok boolean,probs jsonb,market jsonb,top_band_id text,predictive_context jsonb,raw_forecast_c numeric,sigma_c numeric);
 create table fact_checkpoint_outcome(checkpoint_id uuid,city_key text,target_date date,checkpoint text,winner_band_id text,settled_at timestamptz,ladder_has_winner boolean);
 create table live_capture(capture_id bigserial primary key,kind text,key text,captured_at timestamptz,payload jsonb,written_at timestamptz);
 create table markets(market_id uuid primary key,city_key text,resolution_date date,unit text,closed boolean default false,rules_text text,rules_fetched_at timestamptz);
 create view v_canonical_markets as select * from markets;
 create table bands(band_id uuid primary key,market_id uuid,band_lo numeric,band_hi numeric,band_label text);
 create view v_canonical_bands as select * from bands;
 create table market_rule_receipts(receipt_id bigserial primary key,market_id uuid,received_at timestamptz,rules_text text);
 create table derived_weather_peak(city_key text,month int,computed_at timestamptz,peak_hour_local numeric);
 create table band_probabilities(prob_id bigserial primary key,band_id uuid,computed_at timestamptz,forecast_max_c numeric,sigma_c numeric,
 calibrated_prob numeric,input_forecast_run timestamptz,pricing_eligible boolean);
 create table current_ladders(city_key text,target_date date,market_id uuid,published_at timestamptz,priced_at timestamptz,ladder jsonb);
 create table book_snapshots(snapshot_id bigserial primary key,band_id uuid,observed_at timestamptz,best_bid numeric,best_ask numeric);
 create table venue_bands(band_id uuid,market_id uuid,confirmed_at timestamptz,settled_yes boolean,resolution_state text);
 create view v_venue_band_resolution as select * from venue_bands;
 create table weather_resolution_evidence(evidence_id text primary key,city_key text,for_date date,captured_at timestamptz,
 parser_version text,record_status text,raw_payload jsonb);
 create view v_verified_weather_outcomes as select * from (select e.*,row_number() over(partition by city_key,for_date order by captured_at desc,evidence_id desc) rn from weather_resolution_evidence e) q where rn=1 and record_status='verified';
 `);
 await q(`insert into cities select 'city'||i,$1,'C','Synthetic '||i,'active','TEST',0,0 from generate_series(1,20) i`,[zone]);
 const root=path.join(__dirname,'../..');
 await db.exec(fs.readFileSync(path.join(root,'sql/ad4_shared_weather_forward.sql'),'utf8'));
 const migration=fs.readFileSync(path.join(root,'sql/ad4_temperature_forward.sql'),'utf8');
 const before=await q(`select table_name,column_name,data_type from information_schema.columns where table_schema='public' and table_name in ('prediction_checkpoints','live_capture','markets') order by table_name,ordinal_position`);
 await db.exec(migration);await db.exec(migration);
 await db.exec(fs.readFileSync(path.join(root,'supabase/migrations/20261106102043_temperature_score_empty_guard.sql'),'utf8'));
 const receiptMigration=fs.readFileSync(path.join(root,'supabase/migrations/20261106214644_temperature_exact_comparison_receipts.sql'),'utf8');
 await db.exec(receiptMigration);await db.exec(receiptMigration);
 // With no closed, unscored research calls, settlement history is irrelevant.
 // A missing venue surface must not stop the first capture/learning heartbeat.
 await db.exec('drop view v_venue_band_resolution');
 assert.equal((await q('select temperature_score() n'))[0].n,0);
 await db.exec('create view v_venue_band_resolution as select * from venue_bands');
 assert.deepEqual(await q(`select table_name,column_name,data_type from information_schema.columns where table_schema='public' and table_name in ('prediction_checkpoints','live_capture','markets') order by table_name,ordinal_position`),before);
 const times=(await q(`select (clock_timestamp()-interval '1 hour')::text started,(clock_timestamp()-interval '30 minutes')::text available`))[0];
 const artifact={policy:'shared-temperature-normal-v1',sha256:'b'.repeat(64),changes_serving:false,eligible_for_promotion:false,
 fit_started_at:times.started,available_at:times.available,state:'unfitted_reference',training:null};
 const protocol=(await q('select arbdesk_private.temperature_protocol() p'))[0].p;
 const cohort=Array.from({length:20},(_,i)=>'city'+(i+1));
 await db.exec('set role anon');await assert.rejects(q('select temperature_register($1,$2,$3)',[artifact,cohort,protocol]),/permission denied/);
 await assert.rejects(q('select * from arbdesk_private.temperature_models'),/permission denied/);
 await db.exec('reset role;set role service_role');
 await assert.rejects(q('select temperature_register($1,$2,$3)',[{...artifact,state:'fitted_candidate',training:{}},cohort,protocol]),/registration/);
 const registered=(await q('select temperature_register($1,$2,$3) r',[artifact,cohort,protocol]))[0].r;
 assert.equal(registered.state,'unfitted_reference');
 await assert.rejects(q('select temperature_register($1,$2,$3)',[{...artifact,extra:'different'},cohort,protocol]),/identity conflict/);
 await db.exec('reset role');
 const market=(await q(`insert into markets values(gen_random_uuid(),'city1',(clock_timestamp() at time zone $1)::date,'C',false,'synthetic dated rules',clock_timestamp()-interval '2 hours') returning market_id`,[zone]))[0].market_id;
 const a=(await q(`insert into bands values(gen_random_uuid(),$1,null,22,'low') returning band_id`,[market]))[0].band_id;
 const b=(await q(`insert into bands values(gen_random_uuid(),$1,22,null,'high') returning band_id`,[market]))[0].band_id;
 const probs={[a]:.4,[b]:.6};
 await q(`insert into derived_weather_peak values('city1',extract(month from clock_timestamp() at time zone $1)::int,clock_timestamp()-interval '2 days',14)`,[zone]);
 const weather=(await q(`insert into live_capture(kind,key,captured_at,payload,written_at)
 select 'hourly0','city1|snapshot',clock_timestamp()-interval '2 minutes',jsonb_build_object('schema','nwp-current-snapshot-v1',
 'request_location',jsonb_build_object('latitude',0,'longitude',0,'timezone',$1::text),'hours',
 (select jsonb_object_agg(to_char(t at time zone 'UTC','YYYY-MM-DD"T"HH24:MI'),jsonb_build_array(case when extract(hour from t at time zone $1)=14 then 23 else 20 end,15,30,100,10,0,1010))
 from generate_series(date_trunc('day',clock_timestamp() at time zone $1) at time zone $1,
 (date_trunc('day',clock_timestamp() at time zone $1)+interval '1 day') at time zone $1-interval '1 hour',interval '1 hour')t)),
 clock_timestamp()-interval '1 minute' returning capture_id,payload,captured_at,written_at`,[zone]))[0];
 await q(`insert into band_probabilities(band_id,computed_at,forecast_max_c,sigma_c,calibrated_prob,input_forecast_run,pricing_eligible)
 select band_id,now()-interval '1 hour',23,1.5,case when band_id=$2 then .4 else .6 end,now()-interval '2 hours',true from bands where market_id=$1`,[market,a]);
 await q(`insert into book_snapshots(band_id,observed_at,best_bid,best_ask) select band_id,clock_timestamp()-interval '1 minute',case when band_id=$2 then .19 else .49 end,case when band_id=$2 then .21 else .51 end from bands where market_id=$1`,[market,a]);
 await db.exec('set role service_role');
 const inputs=(await q('select temperature_inputs() r'))[0].r;
 assert.equal(inputs.packets.length,1);const packet=inputs.packets[0];
 assert.equal(packet.checkpoint,'morning');assert.equal(packet.references.incumbent_source,'stored_pricing');
 assert.equal(packet.references.raw_forecast_c,23);assert.ok(packet.references.market);
 const rulesHash=(await q("select encode(sha256(convert_to('synthetic dated rules','UTF8')),'hex') h"))[0].h;
 const row={record:{policy:'shared-temperature-normal-v1',city:'city1',date:packet.market.resolution_date,market_id:market,
 checkpoint:'morning',station:'TEST',timezone:zone,unit:'C',contract:'awc-routine-v1',rules_sha256:rulesHash,
 rules_received_at:packet.rules.received_at,as_of:packet.as_of,weather_capture_id:weather.capture_id,
 band_ids:[a,b],bounds:[[null,22],[22,null]],floor_c:null,peak_reference:packet.peak_reference,
 hourly:{hours:Object.entries(weather.payload.hours).sort().map(([utc,values])=>({utc:new Date(utc+'Z').valueOf()/1000,values,captured_at:weather.captured_at,written_at:weather.written_at}))}},
 prediction:{centre_c:23,sigma_c:1.5,median_c:23,q10_c:21,q90_c:25,probs},references:packet.references,hourly_reference:probs,raw_normal_reference:probs};
 // Numeric normalization produces recurring decimals. A JSON number decoded
 // through JS/Python cannot be an exact PostgreSQL numeric receipt.
 await assert.rejects(q('select temperature_freeze($1,$2)',[artifact.sha256,[row]]),/receipt changed/);
 assert.equal(typeof packet.references_receipt,'string');
 const exactReceipt=packet.references_receipt;
 delete row.references;row.references_receipt=exactReceipt;
 assert.equal((await q('select temperature_freeze($1,$2) r',[artifact.sha256,[row]]))[0].r.new,1);
 assert.equal((await q("select comparisons - 'hourly_reference' - 'raw_normal_reference' = $1::jsonb exact from temperature_calls",[exactReceipt]))[0].exact,true);

 assert.equal((await q('select temperature_freeze($1,$2) r',[artifact.sha256,[row]]))[0].r.new,0);
 const held=await q('select * from temperature_calls');
 assert.equal(held.length,1);assert.deepEqual(held[0].comparisons.incumbent,probs);
 await assert.rejects(q('update temperature_calls set prediction=\'{}\''),/permission denied/);
 await db.exec('reset role');await assert.rejects(q('delete from temperature_calls'),/immutable/);
 await assert.rejects(q('truncate temperature_calls cascade'),/immutable/);
 await db.exec('set role service_role');
 const attempt=async(change,why)=>{const x=structuredClone(row);change(x);await assert.rejects(q('select temperature_freeze($1,$2)',[artifact.sha256,[x]]),why)};
 await attempt(x=>x.record.rules_sha256='c'.repeat(64),/live evidence/);
 await attempt(x=>x.record.bounds[0][1]=21,/geometry/);
 await attempt(x=>x.record.peak_reference.peak_hour_local=9,/peak proof/);
 await attempt(x=>x.record.as_of=new Date(Date.now()-360000).toISOString(),/live evidence/);
 await attempt(x=>x.prediction.probs[a]=.99,/normalized/);
 await attempt(x=>{const r=JSON.parse(x.references_receipt);r.incumbent[a]=.99;x.references_receipt=JSON.stringify(r)},/receipt changed/);
 await attempt(x=>x.references_receipt=JSON.stringify(packet.references),/receipt changed/);
 await attempt(x=>x.references_receipt=packet.references,/receipt changed/);
 await attempt(x=>x.record.hourly.hours[0].values[0]=50,/differs from received/);
 await db.exec('reset role');
 await q(`update live_capture set payload=jsonb_set(payload,'{hours}',(payload->'hours')-(select min(key) from jsonb_each(payload->'hours'))) where capture_id=$1`,[weather.capture_id]);
 await db.exec('set role service_role');await assert.rejects(q('select temperature_freeze($1,$2)',[artifact.sha256,[row]]),/incomplete/);
 await db.exec('reset role');await q('update live_capture set payload=$1 where capture_id=$2',[weather.payload,weather.capture_id]);
 await q('insert into venue_bands values($1,$2,clock_timestamp(),true,\'confirmed\')',[b,market]);
 await db.exec('set role service_role');await assert.rejects(q('select temperature_freeze($1,$2)',[artifact.sha256,[row]]),/live evidence/);
 assert.equal((await q('select temperature_score() n'))[0].n,0,'today cannot be scored before its local end');
 assert.deepEqual(await q('select * from temperature_calls'),held);
 assert.equal((await q('select temperature_learning_inputs() r'))[0].r.calls.length,1);
 assert.equal((await q('select temperature_fit_report(null,$1) r',[{state:'insufficient_legitimate_history',changes_serving:false}]))[0].r.stored_fit,false);
 await assert.rejects(q('select * from arbdesk_private.temperature_fits'),/permission denied/);
 // Synthetic past-only source, inserted as owner to test settlement ordering.
 // It is outside the registered future trial and can never approve the model.
 await db.exec('reset role');
 const oldMarket=(await q(`insert into markets values(gen_random_uuid(),'city2',current_date-1,'C',false,'synthetic dated rules',clock_timestamp()-interval '3 days') returning market_id`))[0].market_id;
 const aa=(await q(`insert into bands values(gen_random_uuid(),$1,null,22,'low') returning band_id`,[oldMarket]))[0].band_id;
 const bb=(await q(`insert into bands values(gen_random_uuid(),$1,22,null,'high') returning band_id`,[oldMarket]))[0].band_id;
 const oldPrediction={centre_c:23,sigma_c:1.5,probs:{[aa]:.4,[bb]:.6}};
 const oldRecord={unit:'C',rules_sha256:rulesHash,bounds:[[null,22],[22,null]]};
 const old=(await q(`insert into temperature_calls(model_sha256,city_key,target_date,market_id,checkpoint,decided_at,frozen_at,record,prediction,comparisons)
 values($1,'city2',current_date-1,$2,'morning',clock_timestamp()-interval '2 days',clock_timestamp()-interval '2 days',$3,$4,'{}') returning call_id`,[artifact.sha256,oldMarket,oldRecord,oldPrediction]))[0].call_id;
 await q(`insert into venue_bands values($1,$2,clock_timestamp()-interval '1 hour',true,'confirmed')`,[bb,oldMarket]);
 await db.exec('set role service_role');
 assert.equal((await q('select temperature_score() n'))[0].n,1);
 assert.equal((await q('select temperature_score() n'))[0].n,0);
 const score=(await q('select * from temperature_scores where call_id=$1',[old]))[0];
 assert.equal(score.metrics.candidate.hit,true);assert.deepEqual(score.winner_interval,{lo:22,hi:null,unit:'C'});
 assert.ok(Math.abs(score.metrics.candidate.log_loss+Math.log(.6))<1e-10);
 await db.exec('reset role');
 await q(`insert into weather_resolution_evidence values('original','city2',current_date-1,clock_timestamp()-interval '1 hour','weather-outcomes-v6','verified','{}')`);
 await db.exec('set role service_role');assert.equal((await q('select temperature_export($1) r',[artifact.sha256]))[0].r.points.length,1);
 await db.exec('reset role');
 await q(`insert into weather_resolution_evidence values('review','city2',current_date-1,clock_timestamp(),'awc-evidence-review-v1','disputed','{}')`);
 await db.exec('set role service_role');assert.equal((await q('select temperature_export($1) r',[artifact.sha256]))[0].r.points.length,0,'superseded point cannot leak through the export');
 const snapshot=(await q('select temperature_snapshot() r'))[0].r;assert.equal(snapshot.calls,2);assert.ok(!JSON.stringify(snapshot).includes('mean_coefficients'));
 await db.exec('reset role;set role anon');await assert.rejects(q('select temperature_inputs()'),/permission denied/);
 await assert.rejects(q('select * from temperature_calls'),/permission denied/);
 await db.close();console.log('temperature-forward: separate registry, immutable live calls, original receipts, matched baselines, local clocks, peak and completeness guards passed');
})().catch(e=>{console.error(e);process.exit(1)});
