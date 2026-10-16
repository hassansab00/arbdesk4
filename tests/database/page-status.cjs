// Page samples preserve live values, age honestly, expire loudly, and keep
// privileged data private. Engines remain on the original live views.
const {PGlite}=require('@electric-sql/pglite');
const fs=require('fs');const assert=require('node:assert/strict');
const mig=fs.readFileSync(require('path').join(__dirname,'../../supabase/migrations/20261016174409_page_status_snapshots.sql'),'utf8');
(async()=>{
 const db=new PGlite();const q=async s=>(await db.query(s)).rows;
 await db.exec(`create role anon; create role authenticated; create role service_role;
 create function log_ingest(text,text,int,jsonb) returns void language sql as $$select$$;
 create table fresh(table_name text,layer text,plain_english text,rows bigint,rows_estimated boolean,newest timestamptz,age_hours numeric,fresh_hours numeric,state text);
 insert into fresh values('weather','weather','readings',5,false,now()-interval '1 hour',1,2,'ok'),('missing','weather','missing',null,null,null,null,null,'absent');
 create view v_data_freshness as select * from fresh;
 create table volume(city_key text,volume_usd numeric,n_trades int);
 insert into volume values('milan',123,2);
 create view v_city_volume as select * from volume;
 `);
 await db.exec(mig);await db.exec(mig);
 // Fresh installs apply the serialization migration after the sampler's file.
 // That later replacement must retain the sampler hook.
 await db.exec(fs.readFileSync(require('path').join(__dirname,'../../supabase/migrations/20261016174555_serialized_page_cache.sql'),'utf8'));

 for(const [live,page,omit] of [['v_data_freshness','v_page_data_freshness','sampled_at'],['v_city_volume','v_page_city_volume','sampled_at']]){
  const cols=(await q(`select attname from pg_attribute where attrelid='${live}'::regclass and attnum>0 order by attnum`)).map(x=>x.attname).join(',');
  for(const [a,b] of [[live,page],[page,live]])assert.equal((await q(`select count(*)::int n from(select ${cols} from ${a} except all select ${cols} from ${b})x`))[0].n,0);
 }
 await db.exec(`update fresh set newest=now()-interval '3 hours',state='stale' where table_name='weather'; update volume set volume_usd=321;`);
 assert.equal((await q('select volume_usd from v_page_city_volume'))[0].volume_usd,'123');
 await q('select refresh_page_cache()');
 assert.equal((await q('select volume_usd from v_page_city_volume'))[0].volume_usd,'321');
 assert.equal((await q("select state from v_page_data_freshness where table_name='weather'"))[0].state,'stale');
 for(const role of ['anon','authenticated']){
  await db.exec(`set role ${role}`);assert.equal((await q('select * from v_page_data_freshness')).length,2);
  await assert.rejects(q('select * from mv_page_data_freshness'),/permission denied/);
  await assert.rejects(q('select refresh_page_status()'),/permission denied/);
  await assert.rejects(q("select page_sample_current(now()-interval '41 minutes')"),/overdue/);
  await db.exec('reset role');
 }
 await db.close();console.log('PASS: page status samples preserve rows, refresh live data, retain clock-based age, reject overdue status, and enforce read-only access');
})().catch(e=>{console.error(e);process.exit(1)});
