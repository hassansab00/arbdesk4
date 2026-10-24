const {PGlite}=require('@electric-sql/pglite');
const fs=require('fs');
const path=require('path');
const assert=require('node:assert/strict');
const crypto=require('node:crypto');
const zlib=require('node:zlib');
const migration=fs.readFileSync(path.join(__dirname,'../../supabase/migrations/20261023171236_awc_contract_observation_receipts.sql'),'utf8');
(async()=>{
  const db=new PGlite();
  const q=async(sql,args)=>(await db.query(sql,args)).rows;
  await db.exec(`create role anon; create role authenticated; create role service_role;
    create table live_capture(capture_id bigserial primary key,kind text not null check(kind in
      ('report','hourly','hourly2','models1','models2','books','focus_book','wx_features','hourly0','models0')),
      key text not null,captured_at timestamptz not null,payload jsonb not null,written_at timestamptz default now());
    alter table live_capture enable row level security;
    grant select on live_capture to service_role;
    create table cities(city_key text,timezone text,icao text,status text default 'active');
    insert into cities(city_key,timezone,icao) values('legacy','UTC','KLGA'),('awc','UTC','KLGA');
    create table markets(city_key text,resolution_date date,rules_text text);
    insert into markets values('legacy',current_date,'https://www.weather.gov/wrh/timeseries?site=KLGA'),
      ('awc',current_date,'https://aviationweather.gov/api/data/metar?ids=KLGA&format=raw');
    create table health(city_key text,local_date date,running_max_basis text,timing_trustworthy boolean,
      observed_max_today_c numeric,stored_running_max_c numeric,latest_temp_today_c numeric,latest_temp_c numeric,
      readings_today bigint,stored_max_below_latest boolean,note text,live_source_kind text,
      latest_reading_at timestamptz,latest_reading_c numeric,settlement_max_today_c numeric);
    insert into health select city_key,current_date,'series',true,30,30,30,30,12,false,'old note','station',now(),30,30 from cities;
    create view v_city_observation_health as select * from health;
    create view v_city_running_max as select city_key,local_date,running_max_basis,timing_trustworthy,
      coalesce(settlement_max_today_c,greatest(observed_max_today_c,stored_running_max_c,latest_temp_today_c)) as running_max_c,
      observed_max_today_c,stored_running_max_c,latest_temp_today_c,latest_temp_c,readings_today,stored_max_below_latest,
      note,live_source_kind,latest_reading_at,latest_reading_c,settlement_max_today_c,
      greatest(observed_max_today_c,stored_running_max_c,latest_temp_today_c) as all_sources_max_c,
      case when settlement_max_today_c is not null then 'settlement_feed'::text
        when greatest(observed_max_today_c,stored_running_max_c,latest_temp_today_c) is not null then 'all_sources'::text end as running_max_source from health;
    grant select on v_city_running_max to anon;
    create temp table before_rows as select * from v_city_running_max;
  `);
  await db.exec(migration);
  assert.equal((await q(`select count(*)::int n from ((select * from before_rows where city_key='legacy'
    except all select * from v_city_running_max where city_key='legacy') union all
    (select * from v_city_running_max where city_key='legacy' except all select * from before_rows where city_key='legacy')) d`))[0].n,0);
  const empty=(await q("select * from v_city_running_max where city_key='awc'"))[0];
  assert.equal(empty.running_max_c,null,'new contracts cannot inherit the special-report maximum');
  assert.equal(empty.running_max_source,'awc_routine');
  const [clock]=await q('select current_date::text as "day",(now()-interval \'10 seconds\')::text received');
  const rules='https://aviationweather.gov/api/data/metar?ids=KLGA&format=raw';
  const raw='KLGA routine report retained';
  const payload={schema:'awc-routine-day-v1',city_key:'awc',date:clock.day,station:'KLGA',timezone:'UTC',
    rules_sha256:crypto.createHash('sha256').update(rules).digest('hex'),
    raw_sha256:crypto.createHash('sha256').update(raw).digest('hex'),raw_gzip:zlib.gzipSync(raw).toString('base64'),
    source_url:rules,contract_source_url:rules,qualifying_max_c:'19.4',routine_count:2,
    latest_eligible_at:clock.received,latest_eligible_c:'19.4'};
  const row={key:'awc|'+clock.day,captured_at:clock.received,payload};
  const put=async(rows)=>(await q('select capture_awc_metar($1::jsonb) r',[JSON.stringify(rows)]))[0].r;
  assert.equal((await put([row])).new,1);
  assert.equal((await put([row])).new,0,'an identical response does not duplicate');
  let live=(await q("select * from v_city_running_max where city_key='awc'"))[0];
  assert.equal(Number(live.running_max_c),19.4);
  assert.equal(Number(live.all_sources_max_c),30,'physical special-report information remains available');
  assert.equal(Number(live.latest_reading_c),19.4);
  const changed=JSON.parse(JSON.stringify(row));
  changed.captured_at=(await q("select (now()-interval '5 seconds')::text at"))[0].at;
  changed.payload.qualifying_max_c='18';changed.payload.latest_eligible_c='18';
  assert.equal((await put([changed])).new,1);
  assert.equal((await q("select count(*)::int n from live_capture"))[0].n,2,'corrections append; previous receipts remain');
  assert.equal((await q('select payload from live_capture order by capture_id limit 1'))[0].payload.qualifying_max_c,'19.4');
  await assert.rejects(put([{...row,captured_at:'2100-01-01T00:00:00Z'}]),/invalid authority/);
  await assert.rejects(put([{...row,payload:{...payload,rules_sha256:'broken'}}]),/invalid authority/);
  await assert.rejects(put([row,row]),/successive snapshots/);
  const acl=(await q(`select has_function_privilege('anon','capture_awc_metar(jsonb)','execute') a,
    has_function_privilege('authenticated','capture_awc_metar(jsonb)','execute') b,
    has_function_privilege('service_role','capture_awc_metar(jsonb)','execute') s,
    has_table_privilege('anon','live_capture','select') raw`))[0];
  assert.deepEqual(acl,{a:false,b:false,s:true,raw:false});
  await db.exec('set role anon');
  assert.equal((await q("select count(*)::int n from v_city_running_max"))[0].n,2,'existing anonymous summary stays readable');
  await db.exec('reset role');
  await db.exec(migration);
  assert.equal((await q('select count(*)::int n from live_capture'))[0].n,2,'reinstall preserves receipts');
  await db.close();
  console.log('AWC authority receipts: causal history, source-specific floor, legacy conservation, private writes and reinstall passed');
})().catch(e=>{console.error(e);process.exit(1)});
