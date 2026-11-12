/** Offline private restore and EXCEPT ALL in both directions. Never prunes. */
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const {PGlite}=require('../tests/database/node_modules/@electric-sql/pglite');
async function restore(document){
 const hash=s=>crypto.createHash('sha256').update(s).digest('hex');
 if(hash(document.payload)!==document.sha256)throw Error('private archive data hash mismatch');
 const data=JSON.parse(document.payload);
 if(hash(data.manifest.payload)!==data.manifest.sha256)throw Error('private archive membership hash mismatch');
 const manifest=JSON.parse(data.manifest.payload);
 if(manifest.schema_version!=='20261111214539')throw Error('private archive schema version mismatch');
 const sql=fs.readFileSync(path.join(__dirname,'../supabase/migrations/20261111214539_original_call_side_evidence.sql'),'utf8');
 const ddl=sql.slice(0,sql.indexOf('-- This feature never feeds'));
 const db=new PGlite();let rows=0;
 try{
  await db.exec('create schema arbdesk_private;');await db.exec(ddl);
  for(const entity of manifest.entities){
   if(!/^original_side_[a-z_]+$/.test(entity.table))throw Error('unexpected private archive table');
   const packets=data.packets.find(x=>x.table===entity.table)?.rows;
   if(!packets||packets.length!==entity.rows)throw Error('private archive entity count mismatch');
   const ids=new Set();for(const packet of packets){
    const key=String(JSON.parse(packet)[entity.pk]);if(ids.has(key))throw Error('duplicate private archive key');ids.add(key);
    if(entity.immutable_ids&&!entity.immutable_ids.includes(key))throw Error('packet outside frozen membership');
    await db.query(`insert into arbdesk_private.${entity.table} overriding system value select * from jsonb_populate_record(null::arbdesk_private.${entity.table},$1::jsonb)`,[packet]);
   }
   const differences=(await db.query(`select count(*) n from(
    (select to_jsonb(t) packet from arbdesk_private.${entity.table} t except all select value::jsonb from jsonb_array_elements_text($1::jsonb))
    union all (select value::jsonb from jsonb_array_elements_text($1::jsonb) except all select to_jsonb(t) from arbdesk_private.${entity.table} t)
   ) d`,[JSON.stringify(packets)])).rows[0].n;
   if(Number(differences)!==0)throw Error('restored private receipt differs');rows+=packets.length;
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
