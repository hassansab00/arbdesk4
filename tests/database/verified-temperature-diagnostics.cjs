const {PGlite}=require('@electric-sql/pglite');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const columns={"v_city_hit_history": [["city_key", "text"], ["display_name", "text"], ["unit", "text"], ["for_date", "date"], ["ladder_bands", "bigint"], ["model_bands", "bigint"], ["observed_max_c", "numeric"], ["forecast_max_c", "numeric"], ["error_c", "numeric"], ["sigma_c", "numeric"], ["confidence", "numeric"], ["regime_label", "text"], ["winner", "text"], ["model_call", "text"], ["model_call_prob", "numeric"], ["model_prob_on_winner", "numeric"], ["model_hit", "boolean"], ["brier_model", "numeric"], ["brier_uniform", "numeric"], ["called_at", "timestamptz"], ["hours_before_day", "numeric"], ["head_to_head", "boolean"], ["bands_scored", "bigint"], ["market_call", "text"], ["market_call_price", "numeric"], ["market_prob_on_winner", "numeric"], ["market_hit", "boolean"], ["brier_model_common", "numeric"], ["brier_market", "numeric"], ["brier_uniform_common", "numeric"], ["centre_c", "numeric"], ["centre_error_c", "numeric"]], "v_prediction_hindsight": [["city_key", "text"], ["for_date", "date"], ["predicted_band", "text"], ["predicted_pct", "numeric"], ["actual_band", "text"], ["observed_max_c", "numeric"], ["forecast_max_c", "numeric"], ["forecast_error_c", "numeric"], ["hit", "boolean"], ["stated_sigma_c", "numeric"], ["regime_label", "text"], ["outcome_source", "text"], ["called_when", "text"], ["call_order", "integer"], ["after_peak", "boolean"], ["called_at", "timestamptz"], ["market_band", "text"], ["market_hit", "boolean"], ["scheduled_local", "timestamp"], ["engine_version", "text"], ["prob_on_winner", "numeric"]]};

(async()=>{
 const db=new PGlite();const q=async(s,p)=>(await db.query(s,p)).rows;
 await db.exec('create role anon;create role authenticated;create role service_role bypassrls');
 for(const [view,cols] of Object.entries(columns)){
  const table=view.replace('v_','mv_');
  await db.exec('create table '+table+'('+cols.map(([n,t])=>n+' '+t).join(',')+');create view '+view+' as select * from '+table+';grant select on '+view+' to anon,authenticated,service_role');
 }
 await db.exec(`create table weather_resolution_evidence(evidence_id text primary key,city_key text,for_date date,observed_max_c numeric,unit text,parser_version text,record_status text,captured_at timestamptz);
 create view v_verified_weather_outcomes as select * from(select distinct on(city_key,for_date) * from weather_resolution_evidence order by city_key,for_date,captured_at desc,evidence_id desc) e where record_status='verified'`);
 const cities=['denver','chicago','legacy','disputed','missing','wrongunit'];
 for(const city of cities){
  await q(`insert into mv_city_hit_history(city_key,unit,for_date,observed_max_c,forecast_max_c,error_c,centre_c,centre_error_c,model_call,winner,model_hit,called_at,model_call_prob) values($1,'F','2026-11-06',14.4,13.9,-.5,13.9,-.5,'56-57','60-61',false,'2026-11-05T12:00Z',.4)`,[city]);
  await q(`insert into mv_prediction_hindsight(city_key,for_date,observed_max_c,forecast_max_c,forecast_error_c,predicted_band,actual_band,hit,called_when,called_at,predicted_pct) values($1,'2026-11-06',14.4,13.9,.5,'56-57','60-61',false,'day_ahead','2026-11-05T12:00Z',40)`,[city]);
 }
 await db.exec(`insert into weather_resolution_evidence values
 ('denver-v6','denver','2026-11-06',15.555556,'C','weather-outcomes-v6','verified','2026-11-07T08:00Z'),
 ('chicago-v6','chicago','2026-11-06',8.333333,'C','weather-outcomes-v6','verified','2026-11-07T08:00Z'),
 ('legacy-v5','legacy','2026-11-06',25,'C','weather-outcomes-v5','verified','2026-11-07T08:00Z'),
 ('disputed-v6','disputed','2026-11-06',25,'C','weather-outcomes-v6','verified','2026-11-07T08:00Z'),
 ('dispute','disputed','2026-11-06',25,'C','review-v1','disputed','2026-11-07T09:00Z'),
 ('bad-unit','wrongunit','2026-11-06',60,'F','weather-outcomes-v6','verified','2026-11-07T08:00Z')`);
 const caches=await q(`select jsonb_agg(to_jsonb(h) order by city_key) hit_cache from mv_city_hit_history h`);
 const original=await q('select * from v_prediction_hindsight order by city_key');
 const sql=fs.readFileSync(path.join(__dirname,'../../sql/ad4_verified_temperature_diagnostics.sql'),'utf8');
 await db.exec(sql);await db.exec(sql);
 const rows=await q('select * from v_prediction_hindsight order by city_key');
 assert.equal(rows.length,original.length);
 for(let i=0;i<rows.length;i++){
  const strip=r=>Object.fromEntries(Object.entries(r).filter(([k])=>!['observed_max_c','forecast_error_c'].includes(k)));
  assert.deepEqual(strip(rows[i]),strip(original[i]));
 }
 const denver=rows.find(x=>x.city_key==='denver');assert.equal(Number(denver.observed_max_c),15.555556);assert.equal(Number(denver.forecast_error_c),1.7);
 const history=(await q("select * from v_city_hit_history where city_key='denver'"))[0];assert.equal(Number(history.error_c),-1.66);assert.equal(Number(history.centre_error_c),-1.66);
 for(const city of ['legacy','disputed','missing','wrongunit'])assert.equal(rows.find(x=>x.city_key===city).observed_max_c,null);
 await db.exec(`insert into weather_resolution_evidence values('new-review','denver','2026-11-06',15.555556,'C','review-v1','disputed','2026-11-07T10:00Z')`);
 assert.equal((await q("select observed_max_c from v_prediction_hindsight where city_key='denver'"))[0].observed_max_c,null,'a later dispute hides stale cached point truth immediately');
 assert.deepEqual(await q(`select jsonb_agg(to_jsonb(h) order by city_key) hit_cache from mv_city_hit_history h`),caches);
 await db.exec('set role anon');assert.equal(Number((await q('select count(*) n from v_prediction_hindsight'))[0].n),6);
 await assert.rejects(q('select * from mv_city_hit_history'),/permission denied/);
 await assert.rejects(q('select * from weather_resolution_evidence'),/permission denied/);
 await db.close();console.log('verified temperature diagnostics: current point truth, missing/disputed refusal, unchanged cached grades, error signs, repeat install and existing public/private boundaries passed');
})().catch(e=>{console.error(e);process.exit(1)});
