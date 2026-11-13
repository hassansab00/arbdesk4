/** Offline private restore and EXCEPT ALL in both directions. Never prunes. */
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const {PGlite}=require('../tests/database/node_modules/@electric-sql/pglite');
// This schema's complete restore contract. An omitted empty table still means
// the exporter did not prove a complete snapshot; packet hashes alone cannot
// establish membership or the frozen mutable account/order projections.
const ENTITIES={original_side_owners:'owner_id',original_side_rule_receipts:'rules_sha256',
 original_side_ladder_receipts:'ladder_sha256',original_side_calls:'call_id',original_side_books:'snapshot_id',
 original_side_quotes:'attempt_id',original_side_evaluations:'evaluation_id',original_side_policies:'policy_id',
 original_side_policy_events:'event_id',original_side_accounts:'account_id',original_side_allocation_plans:'plan_id',
 original_side_allocations:'allocation_id',original_side_decisions:'decision_id',original_side_orders:'order_id',
 original_side_execution_events:'order_id',original_side_resolution_receipts:'proof_hash',
 original_side_settlements:'settlement_id',original_side_ledger:'entry_id',original_side_stage_events:'event_id'};
const MUTABLE=new Set(['original_side_accounts','original_side_orders']);
function validateMembership(manifest,packets){
 if(!Array.isArray(manifest.entities)||!Array.isArray(packets)
  ||manifest.entities.length!==Object.keys(ENTITIES).length||packets.length!==manifest.entities.length)
  throw Error('complete private archive table membership required');
 const tables=new Set(),packetMap=new Map();
 for(const packet of packets){
  if(!packet||!Object.hasOwn(ENTITIES,packet.table)||packetMap.has(packet.table)||!Array.isArray(packet.rows))
   throw Error('unexpected or duplicate private archive packet table');
  packetMap.set(packet.table,packet.rows);
 }
 for(const entity of manifest.entities){
  if(!entity||!Object.hasOwn(ENTITIES,entity.table)||tables.has(entity.table)||entity.pk!==ENTITIES[entity.table])
   throw Error('unexpected or duplicate private archive entity');
  tables.add(entity.table);
  const mutable=MUTABLE.has(entity.table),members=mutable?entity.mutable_snapshot:entity.immutable_ids;
  if(!Number.isInteger(entity.rows)||entity.rows<0||entity.rows>(mutable?2000:50000)
   ||!Array.isArray(members)||members.length!==entity.rows
   ||Object.hasOwn(entity,mutable?'immutable_ids':'mutable_snapshot')
   ||!members.every(x=>typeof x==='string'))throw Error('invalid private archive frozen membership');
  if(!mutable&&new Set(members).size!==members.length)throw Error('duplicate private archive membership key');
  const rows=packetMap.get(entity.table);
  if(!rows||rows.length!==entity.rows||!rows.every(x=>typeof x==='string'))throw Error('private archive entity count mismatch');
 }
 return packetMap;
}
async function restore(document){
 const hash=s=>crypto.createHash('sha256').update(s).digest('hex');
 if(hash(document.payload)!==document.sha256)throw Error('private archive data hash mismatch');
 const data=JSON.parse(document.payload);
 if(hash(data.manifest.payload)!==data.manifest.sha256)throw Error('private archive membership hash mismatch');
 const manifest=JSON.parse(data.manifest.payload);
 if(manifest.schema_version!=='20261111214539')throw Error('private archive schema version mismatch');
 const packetMap=validateMembership(manifest,data.packets);
 const sql=fs.readFileSync(path.join(__dirname,'../supabase/migrations/20261111214539_original_call_side_evidence.sql'),'utf8');
 const ddl=sql.slice(0,sql.indexOf('-- This feature never feeds'));
 const db=new PGlite();let rows=0;
 try{
  await db.exec('create schema arbdesk_private;');await db.exec(ddl);
  for(const entity of manifest.entities){
   if(!/^original_side_[a-z_]+$/.test(entity.table))throw Error('unexpected private archive table');
   const packets=packetMap.get(entity.table);
   if(!packets||packets.length!==entity.rows)throw Error('private archive entity count mismatch');
   const ids=new Set();for(const packet of packets){
    const parsed=JSON.parse(packet);
    if(!parsed||typeof parsed!=='object'||Array.isArray(parsed)||parsed[entity.pk]==null)throw Error('private archive packet key required');
    const key=String(parsed[entity.pk]);if(ids.has(key))throw Error('duplicate private archive key');ids.add(key);
    if(entity.immutable_ids&&!entity.immutable_ids.includes(key))throw Error('packet outside frozen membership');
    await db.query(`insert into arbdesk_private.${entity.table} overriding system value select * from jsonb_populate_record(null::arbdesk_private.${entity.table},$1::jsonb)`,[packet]);
   }
   const differences=(await db.query(`select count(*) n from(
    (select to_jsonb(t) packet from arbdesk_private.${entity.table} t except all select value::jsonb from jsonb_array_elements_text($1::jsonb))
    union all (select value::jsonb from jsonb_array_elements_text($1::jsonb) except all select to_jsonb(t) from arbdesk_private.${entity.table} t)
   ) d`,[JSON.stringify(packets)])).rows[0].n;
   if(Number(differences)!==0)throw Error('restored private receipt differs');rows+=packets.length;
   if(MUTABLE.has(entity.table)){
    const frozenDifferences=(await db.query(`select count(*) n from(
     (select to_jsonb(t) packet from arbdesk_private.${entity.table} t except all select value::jsonb from jsonb_array_elements_text($1::jsonb))
     union all (select value::jsonb from jsonb_array_elements_text($1::jsonb) except all select to_jsonb(t) from arbdesk_private.${entity.table} t)
    ) d`,[JSON.stringify(entity.mutable_snapshot)])).rows[0].n;
    if(Number(frozenDifferences)!==0)throw Error('restored private mutable projection differs from frozen snapshot');
   }
  }
  const unbalanced=(await db.query(`select count(*) n from arbdesk_private.original_side_accounts a
   left join lateral (select coalesce(sum(cash_delta),0) cash,
    coalesce(sum(reserved_delta),0) reserved from arbdesk_private.original_side_ledger l
    where l.account_id=a.account_id) ledger on true
   where a.cash is distinct from ledger.cash or a.reserved_cash is distinct from ledger.reserved`)).rows[0].n;
  if(Number(unbalanced)!==0)throw Error('restored private account does not reconcile with immutable ledger');
  return {verified:true,tables:manifest.entities.length,rows,sha256:document.sha256};
 }finally{await db.close();}
}
module.exports={restore};
if(require.main===module){restore(JSON.parse(fs.readFileSync(process.argv[2],'utf8'))).then(r=>console.log(JSON.stringify(r))).catch(e=>{console.error(e.message);process.exit(1);});}
