// A city lookup must retain the venue's exact proof rules without grouping
// the whole ledger. Compare against the original view, including adversarial
// outcomes not currently present in production, and as its actual reader roles.
const {PGlite} = require('@electric-sql/pglite');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const root = path.join(__dirname, '../..');
const read = name => fs.readFileSync(path.join(root, 'supabase/migrations', name), 'utf8');
const original = read('20260913100000_phase2a_verified_outcome_truth.sql');
const oldView = original.slice(original.indexOf('create or replace view public.v_venue_market_resolution'),
  original.indexOf('create or replace view public.v_verified_fact_band_outcome'));
const bandView = read('20260924030000_the_verdicts_are_kept.sql').split('create or replace view public.v_venue_band_resolution')[1];
const migration = read('20261025195719_scoped_market_resolution.sql');
const id = n => `00000000-0000-0000-0000-${String(n).padStart(12, '0')}`;

(async () => {
  const db = new PGlite();
  await db.exec(`
    create role anon; create role authenticated; create role service_role bypassrls;
    create table markets(market_id uuid primary key, city_key text, resolution_date date);
    create table bands(band_id uuid primary key, market_id uuid, condition_id text, token_yes text, token_no text);
    create table resolution_verdicts(condition_id text,token_yes text,token_no text,
      winning_token text,captured_at timestamptz,source text,proof_id text,
      primary key(condition_id,token_yes,token_no,captured_at));
    create index on bands(market_id);
    grant select on markets,bands to anon,authenticated,service_role;
    grant select(condition_id,token_yes,token_no,winning_token,captured_at,proof_id) on resolution_verdicts to anon,authenticated;
    grant select on resolution_verdicts to service_role;
    alter table markets enable row level security;
    alter table bands enable row level security;
    alter table resolution_verdicts enable row level security;
    create policy visible_markets on markets for select to anon,authenticated using(city_key <> 'hidden');
    create policy visible_bands on bands for select to anon,authenticated using(true);
    create policy visible_verdicts on resolution_verdicts for select to anon,authenticated using(true);
  `);
  const states = ['missing_bands','unverified','partial','confirmed','disputed','partial','partial','unverified','confirmed'];
  for (let n=1; n<=states.length; n++) {
    await db.query('insert into markets values($1,$2,$3)',[id(n), n===9?'hidden':`city-${n}`, '2026-10-25']);
    if (n===1) continue;
    for (let side=0; side<2; side++) {
      const key = `${n}-${side}`;
      await db.query('insert into bands values($1,$2,$3,$4,$5)',[id(n*10+side),id(n),key,`y${key}`,`n${key}`]);
      if (n===2 || (n===3 && side===1)) continue;
      const token = n===6 || side===1 ? `n${key}` : `y${key}`;
      const proof = async (winner, time, yes=`y${key}`, no=`n${key}`) => db.query(
        'insert into resolution_verdicts(condition_id,token_yes,token_no,winning_token,captured_at,source) values($1,$2,$3,$4,$5,$6)',[key,yes,no,winner,time,'private source']);
      if (n===8) {
        // Same condition, different YES or NO token is not this band's proof.
        await proof(token,'2026-10-26 00:00Z',`other-${key}`);
        await proof(token,'2026-10-26 01:00Z',`y${key}`,`other-no-${key}`);
        continue;
      }
      await proof(n===7?`y${key}`:token,'2026-10-26 00:00Z');
      if (n===4) await proof(token,'2026-10-26 01:00Z'); // repeats are not disputes
      if (n===5 && side===0) await proof(`n${key}`,'2026-10-26 02:00Z');
    }
  }
  await db.exec('create or replace view public.v_venue_band_resolution'+bandView);
  await db.exec(oldView);
  await db.exec('grant select on v_venue_market_resolution,v_venue_band_resolution to anon,authenticated,service_role');
  // Copy both original definitions. Referencing either replaced view would
  // follow the new definition and produce a meaningless equivalence check.
  await db.exec('create view public.old_band_resolution'+bandView);
  await db.exec(oldView.replace('public.v_venue_market_resolution','public.old_market_resolution')
    .replace('public.v_venue_band_resolution','public.old_band_resolution'));
  await db.exec('grant select on old_market_resolution,old_band_resolution to anon,authenticated,service_role');
  const before = (await db.query('select * from v_venue_market_resolution order by market_id')).rows;
  const bandsBefore = (await db.query('select * from v_venue_band_resolution order by band_id')).rows;
  await db.exec(migration);
  await db.exec(migration); // re-runnable, grants preserved
  const after = (await db.query('select * from v_venue_market_resolution order by market_id')).rows;
  assert.deepEqual(after, before);
  assert.deepEqual((await db.query('select * from v_venue_band_resolution order by band_id')).rows,bandsBefore);
  assert.deepEqual(after.map(r=>r.resolution_state), states);
  assert.equal(after[3].confirmed_at.toISOString(),'2026-10-26T01:00:00.000Z');
  assert.equal(after[3].winning_band_id,id(40));
  assert.equal(after[4].disputed_bands,1);
  assert.equal(after[5].yes_winners,0);
  assert.equal(after[6].yes_winners,2);
  assert.equal(after[7].confirmed_bands,0);
  for (const role of ['anon','authenticated','service_role']) {
    await db.exec(`set role ${role}`);
    const result = (await db.query(`select
      (select count(*)::int from (select * from old_market_resolution except all select * from v_venue_market_resolution) d) old_only,
      (select count(*)::int from (select * from v_venue_market_resolution except all select * from old_market_resolution) d) new_only,
      (select count(*)::int from v_venue_market_resolution) n`)).rows[0];
    assert.deepEqual(result,{old_only:0,new_only:0,n:role==='service_role'?9:8});
    assert.deepEqual((await db.query(`select
      (select count(*)::int from (select * from old_band_resolution except all select * from v_venue_band_resolution) d) old_only,
      (select count(*)::int from (select * from v_venue_band_resolution except all select * from old_band_resolution) d) new_only`)).rows[0],{old_only:0,new_only:0});
    await db.exec('reset role');
  }
  assert.deepEqual((await db.query("select reloptions from pg_class where oid='v_venue_market_resolution'::regclass")).rows[0].reloptions,['security_invoker=true']);
  assert.equal((await db.query('select count(*)::int n from resolution_verdicts')).rows[0].n,18);
  await db.close();
  console.log('PASS: scoped market resolution preserves all proof states, exact token identity, repeats, timestamps, grants and invoker RLS');
})().catch(e=>{console.error(e);process.exit(1);});
