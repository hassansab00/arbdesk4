const {PGlite}=require('@electric-sql/pglite');
const fs=require('fs'); const path=require('path'); const assert=require('node:assert/strict');
const migration=fs.readFileSync(path.join(__dirname,'../../supabase/migrations/20261112205125_bounded_signal_inputs.sql'),'utf8');
(async()=>{
 const db=new PGlite();
 await db.exec(`create role anon; create role authenticated; create role service_role;
 create table cities(city_key text primary key,status text,timezone text);
 create table v_canonical_markets(market_id int,city_key text,resolution_date date,unit text);
 create table v_canonical_bands(band_id int,market_id int,band_label text,band_lo numeric,band_hi numeric,
 open_low boolean,open_high boolean,token_yes text,token_no text);
 create table edges(edge_id bigint,band_id int,side text,model_prob numeric,market_price numeric,edge_net_pp numeric,
 tradeable boolean,block_reason text,confidence numeric,regime_label text,fillable_usd_5c numeric,
 computed_at timestamptz,prob_id bigint,book_snapshot_id bigint);
 create table book_snapshots(snapshot_id bigint,band_id int,observed_at timestamptz,market_state text,spread numeric);
 insert into cities values('active','active','UTC'),('inactive','inactive','UTC');
 insert into v_canonical_markets values(1,'active',current_date,'C'),(2,'inactive',current_date,'C'),(3,'active',current_date-1,'C');
 insert into v_canonical_bands select x,x,'20C',20,20,false,false,'yes','no' from generate_series(1,3)x;
 insert into edges select x,x,'YES',.6,.5,10,true,null,.9,'NORMAL',100,now()-interval '1 minute',1,1 from generate_series(1,3)x;
 insert into edges values(4,1,'YES',.7,.6,10,true,null,.8,'NORMAL',120,now(),2,2),
 (5,1,'NO',.3,.4,-10,false,'negative_edge',.8,'NORMAL',80,now(),2,2);
 insert into book_snapshots values(1,1,now()-interval '1 minute','OLD',.1),(2,1,now(),'LIVE',.02);`);
 const before=(await db.query('select to_jsonb(e) row from edges e order by edge_id')).rows;
 await db.exec(migration); await db.exec(migration);
 const rows=(await db.query('select * from v_signal_inputs order by side')).rows;
 assert.equal(rows.length,2); assert.deepEqual(rows.map(x=>x.side),['NO','YES']);
 assert.deepEqual(rows.map(x=>x.edge_id),[5,4]);
 assert.ok(rows.every(x=>x.edge_prob_id===2 && x.edge_book_snapshot_id===2 && x.market_state==='LIVE' && Number(x.spread)===.02));
 assert.equal(rows[0].block_reason,'negative_edge'); assert.equal(rows[0].tradeable,false);
 assert.deepEqual((await db.query('select to_jsonb(e) row from edges e order by edge_id')).rows,before);
 assert.deepEqual((await db.query(`select has_table_privilege('anon','v_signal_inputs','select') a,
 has_table_privilege('authenticated','v_signal_inputs','select') b,has_table_privilege('service_role','v_signal_inputs','select') c`)).rows[0],{a:false,b:false,c:true});
 await db.exec('grant usage on schema public to service_role; set role service_role');
 assert.equal((await db.query('select count(*)::int n from v_signal_inputs')).rows[0].n,2);
 await db.close(); console.log('signal live inputs: exact stored side receipts, active local dates, latest books, private grants and repeat preservation');
})().catch(e=>{console.error(e);process.exit(1)});
