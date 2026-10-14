// The historical checkpoint scores use the page cache without changing scores,
// access, dependent views, or the first-call rule. Real scoreboard definition.
const { PGlite } = require('@electric-sql/pglite');
const fs = require('fs');
const path = require('path');
const assert = require('node:assert/strict');
const ROOT = path.join(__dirname, '..', '..');
const read = (p) => fs.readFileSync(path.join(ROOT, p), 'utf8');
const MIG = read('supabase/migrations/20261014230442_the_checkpoint_scoreboard_reads_stored_rows.sql');
const INSTALL = read('sql/ad4_89_page_cache.sql');
const refreshBody = (s) => s.match(/create or replace function public\.refresh_page_cache\(\)[\s\S]*?end \$\$;/)[0];
assert.equal(refreshBody(MIG), refreshBody(INSTALL), 'migration and reinstall refresh every existing copy and the scoreboard identically');
const FIRST = read('supabase/migrations/20261004140000_one_call_per_checkpoint.sql');
const match = FIRST.match(/execute \$v\$(create or replace view public\.v_checkpoint_scoreboard[\s\S]*?;)\$v\$/);
assert.ok(match, 'read the actual first-call scoreboard definition');
const DEFINITION = match[1];

(async () => {
  const db = new PGlite();
  const exec = (s) => db.exec(s);
  const rows = async (s) => (await db.query(s)).rows;
  const one = async (s) => (await rows(s))[0];
  await exec(`create role anon; create role authenticated; create role service_role;
    create table ingest_log (job text, status text, n int, detail jsonb);
    create function log_ingest(text,text,int,jsonb) returns void language sql as
      $$insert into ingest_log values ($1,$2,$3,$4)$$;`);
  await exec(MIG); // The partial contracts fixture has no scoreboard.
  assert.equal((await one("select to_regclass('mv_checkpoint_scoreboard') is null as missing")).missing, true);
  await exec(`create table calls (
    checkpoint text, checkpoint_order int, after_peak boolean, city_key text,
    hit boolean, called_prob numeric, market_hit boolean, market_complete boolean,
    brier_model numeric, log_loss_model numeric, brier_uniform numeric,
    brier_market numeric, log_loss_market numeric, for_date date, first_call boolean);
    create view v_checkpoint_calls as select * from calls;
    ${DEFINITION}
    grant select on v_checkpoint_scoreboard to anon, authenticated, service_role;
    create view scoreboard_reader as select * from v_checkpoint_scoreboard;
    grant select on scoreboard_reader to anon;
    insert into calls values
      ('noon',3,false,'nyc',true,.6,true,true,.1,.5,.5,.2,.6,current_date-2,true),
      ('noon',3,false,'nyc',false,.7,null,false,.4,1,.5,null,null,current_date-1,true),
      ('noon',3,false,'paris',false,.4,false,true,.3,.8,.5,.4,.9,current_date-1,true),
      ('postpeak_1h',7,true,'paris',true,.9,true,true,.02,.1,.5,.03,.2,current_date-1,true),
      ('noon',3,false,'nyc',true,1,true,true,0,0,.5,0,0,current_date-1,false);
    create table before_scores as select * from v_checkpoint_scoreboard;`);
  const shape = () => rows(`select attname, atttypid, atttypmod from pg_attribute
    where attrelid='v_checkpoint_scoreboard'::regclass and attnum>0 order by attnum`);
  const oid = async (rel) => (await one(`select '${rel}'::regclass::oid::int as o`)).o;
  const wrapperOid = await oid('v_checkpoint_scoreboard');
  const readerOid = await oid('scoreboard_reader');
  const beforeShape = await shape();
  const diff = async (a,b) => (await one(`select count(*)::int as n from
    (select * from ${a} except all select * from ${b}) x`)).n;
  await exec(MIG);
  const cacheOid = await oid('mv_checkpoint_scoreboard');
  await exec(MIG);
  assert.equal(await oid('mv_checkpoint_scoreboard'), cacheOid, 'repeat preserves the copy');
  assert.equal(await oid('v_checkpoint_scoreboard'), wrapperOid);
  assert.equal(await oid('scoreboard_reader'), readerOid);
  assert.deepEqual(await shape(), beforeShape);
  for (const rel of ['v_checkpoint_scoreboard','scoreboard_reader','v_checkpoint_scoreboard_live']) {
    assert.equal(await diff(rel,'before_scores'), 0);
    assert.equal(await diff('before_scores',rel), 0);
  }
  const noon = async (rel) => one(`select * from ${rel} where checkpoint='noon' and city_key='all'`);
  assert.equal((await noon('v_checkpoint_scoreboard')).city_days, 3, 'later captures stay excluded');
  assert.equal((await noon('v_checkpoint_scoreboard')).market_scored, 2, 'incomplete ladders stay excluded');
  for (const role of ['anon','authenticated']) {
    await exec(`set role ${role}`);
    assert.ok((await rows('select * from v_checkpoint_scoreboard')).length > 0);
    await assert.rejects(db.query('select * from v_checkpoint_scoreboard_live'), /permission denied/);
    await assert.rejects(db.query('select * from mv_checkpoint_scoreboard'), /permission denied/);
    await assert.rejects(db.query('select refresh_page_cache()'), /permission denied/);
    await exec('reset role');
  }
  await exec(`insert into calls select checkpoint,checkpoint_order,after_peak,'tokyo',
    hit,called_prob,market_hit,market_complete,brier_model,log_loss_model,brier_uniform,
    brier_market,log_loss_market,for_date,first_call from calls where city_key='paris' and checkpoint='noon'`);
  assert.equal((await noon('v_checkpoint_scoreboard_live')).city_days, 4);
  assert.equal((await noon('v_checkpoint_scoreboard')).city_days, 3, 'page waits for its refresh');
  const refreshed = (await one('select refresh_page_cache() as r')).r;
  assert.deepEqual(Object.keys(refreshed), ['mv_checkpoint_scoreboard']);
  assert.equal((await noon('v_checkpoint_scoreboard')).city_days, 4);
  assert.equal(await diff('v_checkpoint_scoreboard','v_checkpoint_scoreboard_live'),0);
  assert.equal(await diff('v_checkpoint_scoreboard_live','v_checkpoint_scoreboard'),0);
  assert.equal((await one("select detail->'ms' ? 'mv_checkpoint_scoreboard' as logged from ingest_log")).logged,true);

  // The canonical installer also wraps it and repairs a later reinstallation.
  await exec(DEFINITION);
  await assert.rejects(exec(MIG), /is live but a stored copy exists/);
  await exec(INSTALL);
  assert.equal(await oid('v_checkpoint_scoreboard'), wrapperOid);
  assert.match((await one("select pg_get_viewdef('v_checkpoint_scoreboard'::regclass,true) as d")).d,/FROM mv_checkpoint_scoreboard/);
  assert.equal(await diff('v_checkpoint_scoreboard','v_checkpoint_scoreboard_live'),0);
  const installedOid = await oid('mv_checkpoint_scoreboard');
  await exec(INSTALL);
  assert.equal(await oid('mv_checkpoint_scoreboard'), installedOid);
  await exec('set role anon');
  assert.equal((await rows('select * from scoreboard_reader')).length, 6);
  await exec('reset role');
  await db.close();
  console.log('PASS: checkpoint-score-cache: identical scores, first calls, grants and dependents; refresh, rerun and reinstall');
})().catch((e) => { console.error(e); process.exit(1); });
