const {PGlite}=require('@electric-sql/pglite');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
(async()=>{
 const db=new PGlite();const q=async(s,p)=>(await db.query(s,p)).rows;
 await db.exec(`create role anon;create role authenticated;create role service_role;
 create table derived_forecast_skill(city_key text,lead_days int,n_days int,mae_c numeric,bias_c numeric,
 computed_at timestamptz,evidence_scope text,primary key(city_key,computed_at,lead_days));
 create table derived_forecast_skill_model(city_key text,model text,lead_days int,n_days int,mae_c numeric,bias_c numeric,
 computed_at timestamptz,evidence_scope text,primary key(city_key,model,computed_at,lead_days));
 alter table derived_forecast_skill enable row level security;
 alter table derived_forecast_skill_model enable row level security;
 grant select on derived_forecast_skill,derived_forecast_skill_model to service_role;
 create policy skill_read on derived_forecast_skill for select to service_role using(true);
 create policy model_skill_read on derived_forecast_skill_model for select to service_role using(true);`);
 const sql=fs.readFileSync(path.join(__dirname,'../../sql/ad4_forecast_skill_snapshot.sql'),'utf8');
 await db.exec(sql);await db.exec(sql);
 const snap=async()=>(await q('select forecast_skill_snapshot() s'))[0].s;
 assert.deepEqual((await snap()).pooled,[]);
 await db.exec(`insert into derived_forecast_skill values
 ('a',1,30,1.2,-.1,'2026-11-04','verified_outcomes_v1'),
 ('a',1,30,9,0,'2026-11-06',null),
 ('a',1,30,1.1,-.2,'2026-11-05','verified_outcomes_v1'),
 ('a',2,0,null,null,'2026-11-05',null),
 ('b',1,2,2,0,'2026-11-04','legacy'),('b',1,3,3,0,'2026-11-05','legacy');
 insert into derived_forecast_skill_model values
 ('a','nws',1,30,1.3,0,'2026-11-04','verified_outcomes_v1'),
 ('a','nws',1,30,8,0,'2026-11-06',null),
 ('a','other',1,1,4,0,'2026-11-06',null);`);
 const before=(await q(`select jsonb_build_object('pooled',(select jsonb_agg(to_jsonb(t)) from derived_forecast_skill t),
 'models',(select jsonb_agg(to_jsonb(t)) from derived_forecast_skill_model t)) b`))[0].b;
 await db.exec('set role service_role');const s=await snap();
 assert.equal(s.policy,'latest-forecast-skill-v1');assert.equal(s.pooled.length,3);assert.equal(s.models.length,2);
 assert.equal(s.pooled.find(r=>r.city_key==='a'&&r.lead_days===1).mae_c,1.1);
 assert.equal(s.pooled.find(r=>r.city_key==='b').mae_c,3);
 assert.equal(s.pooled.find(r=>r.city_key==='a'&&r.lead_days===2).mae_c,null);
 assert.equal(s.models.find(r=>r.model==='nws').mae_c,1.3);
 await db.exec('reset role');
 const after=(await q(`select jsonb_build_object('pooled',(select jsonb_agg(to_jsonb(t)) from derived_forecast_skill t),
 'models',(select jsonb_agg(to_jsonb(t)) from derived_forecast_skill_model t)) b`))[0].b;
 assert.deepEqual(before,after);
 for(const role of ['anon','authenticated']){
  await db.exec('set role '+role);await assert.rejects(snap(),/permission denied/);await db.exec('reset role');
 }
 assert.equal((await q(`select prosecdef from pg_proc where oid='public.forecast_skill_snapshot()'::regprocedure`))[0].prosecdef,false);
 await db.exec(`insert into derived_forecast_skill select 'synthetic_'||i,1,30,1,0,'2026-11-06','verified_outcomes_v1' from generate_series(1,1100)i`);
 assert.equal((await snap()).pooled.length,1103); // scalar JSON is not a paginated 1,000-row result
 await db.close();
 console.log('forecast-skill-snapshot: verified-first/newest equivalence, complete payload, original history, invoker and private RPC checks passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
