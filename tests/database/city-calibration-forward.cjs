const {PGlite}=require('@electric-sql/pglite');
const fs=require('fs');const path=require('path');const assert=require('node:assert/strict');
const mig=fs.readFileSync(path.join(__dirname,'../../supabase/migrations/20261016174633_city_calibration_forward_record.sql'),'utf8');
(async()=>{
 const db=new PGlite();const q=async s=>(await db.query(s)).rows;
 await db.exec(`create role anon;create role authenticated;create role service_role;
 create table cities(city_key text primary key,timezone text);
 insert into cities values('milan','Europe/Rome');
 create table prediction_checkpoints(checkpoint_id int primary key,city_key text,target_date date,checkpoint text,decided_at timestamptz,priced_from text,inputs_ok boolean);
 create table fact_checkpoint_outcome(checkpoint_id int,settled_at timestamptz,hit boolean,brier_model numeric,log_loss_model numeric);
 insert into prediction_checkpoints values
 (1,'milan',current_date-1,'noon',now()-interval '2 days','city-blend-width:2026-01-01:abc',true),
 (2,'milan',current_date-1,'noon',now()-interval '1 day','city-blend-width:2026-01-01:def',true),
 (3,'milan',current_date-1,'morning',now()-interval '2 days','legacy-width',true),
 (4,'milan',current_date-1,'morning',now()-interval '1 day','city-blend-width:2026-01-01:abc',true),
 (5,'milan',current_date+1,'noon',now(),'city-blend-width:2026-01-01:abc',true),
 (6,'milan','2025-12-31','noon',now()-interval '2 days','city-blend-width:2026-01-01:abc',true),
 (7,'milan',current_date-2,'noon',now()-interval '3 days','city-blend-width:2026-01-01:abc',false);
 insert into fact_checkpoint_outcome values(1,now(),true,.1,.2),(2,now(),false,.9,2),(4,now(),false,.8,1),(5,now(),true,0,0),(6,now(),true,0,0),(7,now(),true,0,0);`);
 await db.exec(mig);await db.exec(mig);
 const rows=await q('select * from v_city_calibration_forward');
 assert.equal(rows.length,1,'later captures and pre-fit/invalid calls are excluded');
 const r=rows[0];assert.equal(r.calls,2);assert.equal(r.settled_calls,1);assert.equal(r.pending_calls,1,'a future local day cannot be graded even with an outcome row');
 assert.equal(r.settled_dates,1);assert.equal(Number(r.hit_rate),1);assert.equal(Number(r.brier),.1);assert.equal(Number(r.log_loss),.2);
 await db.exec('set role anon');assert.equal((await q('select * from v_city_calibration_forward')).length,1);
 await assert.rejects(q('select * from prediction_checkpoints'),/permission denied/);
 await db.close();console.log('PASS: per-city forward scores use the original call across versions, exclude training/invalid calls, wait for completed local days and venue confirmation, and separate pending calls');
})().catch(e=>{console.error(e);process.exit(1)});
