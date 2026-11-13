const {PGlite}=require('@electric-sql/pglite');
const fs=require('node:fs'),assert=require('node:assert/strict');
const root=require('node:path').join(__dirname,'../..');
(async()=>{
 const db=new PGlite();const q=async(sql,args=[])=>(await db.query(sql,args)).rows;
 await db.exec(`create role anon;create role authenticated;create role service_role bypassrls;
 create table markets(market_id text primary key,city_key text,resolution_date date);
 create table bands(band_id text primary key,market_id text);
 create table v_venue_band_resolution(band_id text primary key,market_id text,resolution_state text,settled_yes boolean);
 create table fact_band_outcome(band_id text primary key,city_key text,for_date date,settled_yes boolean,excluded boolean default false);
 create view v_fact_band_outcome_clean with(security_invoker=true) as select * from fact_band_outcome where not excluded;
 create view v_band_outcome_coherence with(security_invoker=true) as select city_key,for_date,count(*)filter(where settled_yes)=1 coherent from fact_band_outcome group by city_key,for_date;
 create view v_venue_market_resolution with(security_invoker=true) as
 select m.market_id,case when count(b.band_id)=0 then 'missing_bands'
 when count(*)filter(where r.resolution_state='disputed')>0 then 'disputed'
 when count(*)filter(where r.resolution_state='confirmed')=count(b.band_id) and count(*)filter(where r.resolution_state='confirmed' and r.settled_yes)=1 then 'confirmed'
 when count(*)filter(where r.resolution_state='confirmed')>0 then 'partial' else 'unverified' end resolution_state
 from markets m left join bands b using(market_id) left join v_venue_band_resolution r using(band_id) group by m.market_id;
 create view v_verified_fact_band_outcome with(security_invoker=true) as select f.* from v_fact_band_outcome_clean f
 join bands b using(band_id) join v_venue_band_resolution br using(band_id)
 join v_venue_market_resolution mr on mr.market_id=b.market_id join v_band_outcome_coherence c on c.city_key=f.city_key and c.for_date=f.for_date
 where br.resolution_state='confirmed' and mr.resolution_state='confirmed' and c.coherent and f.settled_yes is not distinct from br.settled_yes;
 create table fact_forecast_outcome(id int,verified boolean);
 create view v_verified_fact_forecast_outcome with(security_invoker=true) as select * from fact_forecast_outcome where verified;
 create table paper_resolution_evidence(captured_at timestamptz);
 create table weather_resolution_evidence(captured_at timestamptz);
 insert into fact_forecast_outcome values(1,true),(2,false);
 insert into paper_resolution_evidence values('2026-01-01'),('2026-01-02'),('2026-01-03');
 insert into weather_resolution_evidence values('2026-01-01'),('2026-01-04');
 grant select on all tables in schema public to anon,authenticated,service_role;`);
 const seed=fs.readFileSync(root+'/supabase/migrations/20260913100000_phase2a_verified_outcome_truth.sql','utf8');
 const start=seed.indexOf('create or replace view public.v_outcome_evidence_health');
 const legacy=seed.slice(start,seed.indexOf('-- Existing proprietary facts',start)).replace('public.v_outcome_evidence_health','public.expected_health');
 await db.exec(legacy);
 const migration=fs.readFileSync(root+'/supabase/migrations/20261113103335_outcome_health_shared_resolution.sql','utf8');
 for(let i=1;i<=7;i++){
  await q("insert into markets values($1,$1,'2026-01-01')",['m'+i]);
  if(i===5)continue;
  for(let j=1;j<=2;j++){
   const id='b'+i+j,city='m'+i;
   await q('insert into bands values($1,$2)',[id,city]);
   await q("insert into fact_band_outcome values($1,$2,'2026-01-01',$3,$4)",[id,city,i===3?true:i===6?j===2:j===1,i===7&&j===1]);
   if((i===2||i===4)&&j===2)continue;
   await q('insert into v_venue_band_resolution values($1,$2,$3,$4)',[id,city,i===4?'disputed':'confirmed',i===3?true:j===1]);
  }
 }
 await db.exec(migration);await db.exec(migration);
 const result=(await q('select * from v_outcome_evidence_health'))[0];
 assert.equal(Number(result.verified_band_facts),3);
 assert.equal(Number(result.mismatched_band_facts),2);
 assert.equal(Number(result.confirmed_markets),3);
 assert.equal(Number(result.raw_band_facts),12);
 assert.equal(Number(result.verified_forecast_facts),1);
 const parity=async()=>{
  assert.equal((await q('select count(*)::int n from(select * from expected_health except all select * from v_outcome_evidence_health)x'))[0].n,0);
  assert.equal((await q('select count(*)::int n from(select * from v_outcome_evidence_health except all select * from expected_health)x'))[0].n,0);
 };
 await parity();
 await db.exec(`alter table fact_band_outcome enable row level security;create policy visible on fact_band_outcome for select to anon,authenticated using(city_key<>'m6');
 grant select on expected_health,v_outcome_evidence_health to anon,authenticated,service_role;`);
 for(const role of ['anon','authenticated','service_role']){await db.exec('set role '+role);await parity();await db.exec('reset role');}
 await db.exec("update v_venue_band_resolution set resolution_state='confirmed' where band_id='b41';insert into v_venue_band_resolution values('b42','m4','confirmed',false);");
 await parity();assert.equal(Number((await q('select confirmed_markets from v_outcome_evidence_health'))[0].confirmed_markets),4);
 assert.equal((await q('select count(*)::int n from fact_band_outcome'))[0].n,12);
 await db.close();console.log('PASS outcome health: two-way parity, missing/disputed/two-winner/excluded/mismatched evidence, repeat install and invoker RLS');
})().catch(e=>{console.error(e);process.exit(1)});
