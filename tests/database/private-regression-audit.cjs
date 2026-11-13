const {PGlite}=require('@electric-sql/pglite');
const fs=require('node:fs'),assert=require('node:assert/strict');
const root=require('node:path').join(__dirname,'../..');
(async()=>{
 const db=new PGlite();const q=async(sql,args=[])=>(await db.query(sql,args)).rows;
 await db.exec(`create role anon;create role authenticated;create role service_role;
 create table derived_city_prediction_validation(city_key text,checkpoint text,scope text,as_of timestamptz,report jsonb,primary key(city_key,checkpoint,scope));
 grant select on derived_city_prediction_validation to service_role;`);
 const report={_payload_sha256:'source-proof',regression:{flags:['hit_rate_drop','market_outperforms','correction_hurt'],hit_rate_change:-1,comparable_dates_ready:true,
 recent:{n:2,dates:2,verified:2,pending:1,calls:3,start:'2026-01-10',end_exclusive:'2026-01-13',log_loss:1,versions:{'private-version-one':1,'private-version-two':1}},
 prior:{n:3,dates:3,verified:3,pending:0,log_loss:2,versions:{old:3}}},
 early_adaptation:[{policy:'early',phase:'d1_eve',decision:'held',reason:'insufficient older dates',train_dates:0,validation_dates:12,readiness:{sample_ready:false},sensitive_coefficient:123}],
 recent_dates:[{date:'2026-01-10',n:1,hits:0,pending:0},{date:'2026-01-11',n:1,hits:0,pending:0},{date:'2026-01-12',n:0,hits:0,pending:1},{date:'2026-01-13',n:1,hits:1,pending:0}]};
 for(const city of ['alpha','beta','gamma'])await q("insert into derived_city_prediction_validation values($1,'d1_eve','served','2026-01-13',$2)",[city,JSON.stringify(city==='gamma'?{...report,regression:{...report.regression,flags:[]}}:report)]);
 await q("insert into derived_city_prediction_validation values('alpha','d1_eve','wxrd3','2026-01-13',$1)",[JSON.stringify(report)]);
 const migration=fs.readFileSync(root+'/supabase/migrations/20261113103919_private_predictive_regression_audit.sql','utf8');
 await db.exec(migration);await db.exec(migration);
 const before=await q('select * from derived_city_prediction_validation order by city_key,scope');
 const call=async(sql='select predictive_regression_audit() result',args=[])=>(await q(sql,args))[0].result;
 for(const role of ['anon','authenticated']){await db.exec('set role '+role);await assert.rejects(call(),/permission denied/);await db.exec('reset role');}
 await db.exec('set role service_role');
 const page=await call('select predictive_regression_audit(null,null,1) result');
 assert.equal(page.rows.length,1);assert.equal(page.has_more,true);assert.deepEqual(page.next_cursor,{city_key:'alpha',checkpoint:'d1_eve'});
 const row=page.rows[0];assert.equal(row.source_report_fingerprint,'source-proof');assert.equal(row.recent.version_count,2);
 assert.equal(row.daily_details_complete,true);assert.equal(row.retained_daily_details.length,3);
 assert.equal(row.causal_attribution_confirmed,false);assert.equal(row.learning[0].train_dates,0);
 assert.equal(row.findings.length,4); // divergence, market, correction, pending
 assert.equal(JSON.stringify(page).includes('private-version-one'),false);assert.equal(JSON.stringify(page).includes('sensitive_coefficient'),false);
 const second=await call("select predictive_regression_audit(null,null,1,'alpha','d1_eve') result");
 assert.equal(second.rows[0].city_key,'beta');assert.equal(second.has_more,false);
 assert.equal((await call('select predictive_regression_audit(null,null,100,null,null,false) result')).rows.length,3);
 assert.equal((await call("select predictive_regression_audit('missing') result")).rows.length,0);
 for(const sql of ["select predictive_regression_audit(null,null,0)","select predictive_regression_audit(null,null,101)","select predictive_regression_audit(null,null,1,'alpha')"])
  await assert.rejects(q(sql),/Use limit/);
 await db.exec('reset role');assert.deepEqual(await q('select * from derived_city_prediction_validation order by city_key,scope'),before);
 const shortened=structuredClone(report);shortened.recent_dates=shortened.recent_dates.slice(1);
 await q("update derived_city_prediction_validation set report=$1 where city_key='alpha' and scope='served'",[JSON.stringify(shortened)]);
 assert.equal((await call("select predictive_regression_audit('alpha') result")).rows[0].daily_details_complete,false);
 shortened.early_adaptation[0].reason='x'.repeat(530000);
 await q("update derived_city_prediction_validation set report=$1 where city_key='alpha' and scope='served'",[JSON.stringify(shortened)]);
 await assert.rejects(call("select predictive_regression_audit('alpha') result"),/exceeds 512 KiB/);
 await db.close();console.log('PASS regression audit: private read-only bounded pagination, matched source windows, incomplete details and unchanged training gates');
})().catch(e=>{console.error(e);process.exit(1)});
