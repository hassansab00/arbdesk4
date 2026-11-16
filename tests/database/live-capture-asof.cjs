const {PGlite}=require('@electric-sql/pglite');
const fs=require('node:fs'),assert=require('node:assert/strict'),path=require('node:path');
(async()=>{
 const db=new PGlite(), q=async(s,a=[])=>(await db.query(s,a)).rows;
 await db.exec(`create role anon;create role authenticated;create role service_role;
 create table live_capture(capture_id bigint primary key,kind text,key text,captured_at timestamptz,written_at timestamptz,payload jsonb);
 grant select on live_capture to service_role;`);
 const stamp=h=>'2026-11-16T'+String(h).padStart(2,'0')+':00:00Z';
 const rows=[
 [1,'hourly','city_a|hour',1,1],[2,'hourly0','city_a|current',1,1],
 [3,'report','STN|report',2,2],[4,'report','STN|report',2,2],
 [5,'hourly','city_a|hour',3,3],[6,'hourly0','city_a|current',2,5],
 [7,'hourly','cityXa|wrong',3,3],[8,'models0','city_a|missing',3,null],
 [9,'models0','city_a|tie',3,4],[10,'models0','city_a|tie',2,4],
 [11,'books','city_a|excluded',3,3],[12,'hourly','city_a|future',6,6]];
 for(const [id,kind,key,at,written] of rows)await q('insert into live_capture values($1,$2,$3,$4,$5,$6)',[id,kind,key,stamp(at),written===null?null:stamp(written),JSON.stringify({id})]);
 const sql=fs.readFileSync(path.join(__dirname,'../../sql/ad4_live_capture_asof.sql'),'utf8');
 await db.exec(sql);await db.exec(sql);
 const before=await q('select * from live_capture order by capture_id');
 const call=async(times,current=true,after=0,limit=2)=>(await q('select live_capture_asof($1,$2,$3,$4,$5,$6,$7) result',
 [stamp(0),stamp(5),times.map(stamp),['city_a|','STN|'],current,after,limit]))[0].result;
 for(const role of ['anon','authenticated']){await db.exec('set role '+role);await assert.rejects(call([4]),/permission denied/);await db.exec('reset role');}
 await db.exec('set role service_role');
 for(const current of [true,false]){
  const times=[2,4],got=[];let after=0;
  for(;;){const p=await call(times,current,after);got.push(...p.rows);if(!p.has_more)break;assert.ok(p.next_after_id>after);after=p.next_after_id;}
  const expected=new Set();
  for(const t of times){
   const by=new Map();
   for(const [id,kind,key,at,written] of rows){
    if(kind==='books'||at>5||!['city_a|','STN|'].some(p=>key.startsWith(p)))continue;
    if(current&&['hourly0','models0'].includes(kind)&&written===null)continue;
    const available=current&&['hourly0','models0'].includes(kind)?Math.max(at,written):at;
    if(available>t)continue;
    const group=kind+'|'+key,old=by.get(group);
    if(!old||available>old.available||(available===old.available&&id>old.id))by.set(group,{id,available});
   }
   for(const v of by.values())expected.add(v.id);
  }
  assert.deepEqual(got.map(r=>r.capture_id),[...expected].sort((a,b)=>a-b));
  for(const r of got){assert.equal(r.payload.id,r.capture_id);assert.equal(r.first_capture_id,Math.min(...rows.filter(v=>v[1]===r.kind&&v[2]===r.key&&v[3]<=5&&(!current||!['hourly0','models0'].includes(v[1])||v[4]!==null)).map(v=>v[0])));}
 }
 assert.deepEqual((await call([4],true,0,1000)).rows.filter(r=>r.kind==='models0').map(r=>r.capture_id),[10]);
 assert.equal((await call([4],true,999)).rows.length,0);
 await assert.rejects(q("select live_capture_asof('2026-11-01','2026-11-16',array['2026-11-16']::timestamptz[],array['city_a|'])"),/Use 1-30/);
 await assert.rejects(q("select live_capture_asof('2026-11-16','2026-11-17',array['2026-11-16']::timestamptz[],array[''])"),/Use 1-30/);
 await db.exec('reset role');assert.deepEqual(await q('select * from live_capture order by capture_id'),before);
 await db.close();console.log('PASS live capture as-of: full-history winner parity, multi-instant union, late receipt, timestamp ties, literal prefixes, pagination, service-only access, no source mutation');
})().catch(e=>{console.error(e);process.exit(1)});
