// ===========================================================================
// THE MASTHEAD READS STORED ROWS AND INDEXED TIMES (plan v2 P6.5; WXPredict
// build 2.E; 20261009210000_the_masthead_reads_stored_rows_and_indexed_times).
//
// 1. v_data_freshness's max() columns get the index ad4_44 would build, on
//    the tables the migration names, taking the column from
//    data_freshness_spec: never a second index where one already leads with
//    the column, never on a table or column that is not there, never on a
//    table it leaves out.
// 2. v_opportunities becomes a wrapper over a stored copy of its own
//    definition, replaced in place: same view, same columns, same grants, the
//    view built on it untouched and reading the same rows. The strategies'
//    v_opportunities_live and the copy are the service role's only. The
//    wrapper hides a market whose date has passed in its city's time zone,
//    keeps the live order, and refresh_page_cache() carries the copy.
//    Re-runnable, and sql/ad4_89 rebuilds it after v_opportunities is
//    reinstalled live.
// ===========================================================================
const { PGlite } = require('@electric-sql/pglite');
const fs = require('fs');
const path = require('path');
const assert = require('node:assert/strict');

const ROOT = path.join(__dirname, '..', '..');
const MIG = fs.readFileSync(path.join(ROOT, 'supabase', 'migrations',
  '20261009210000_the_masthead_reads_stored_rows_and_indexed_times.sql'), 'utf-8');
const AD4_89 = fs.readFileSync(path.join(ROOT, 'sql', 'ad4_89_page_cache.sql'), 'utf-8');

const ROLES = `create role anon; create role authenticated; create role service_role;`;
const LOG = `
  create table public.ingest_log (job text, status text, rows int, detail jsonb);
  create function public.log_ingest(p_job text, p_status text, p_rows int, p_detail jsonb) returns void
    language sql as $$ insert into public.ingest_log values (p_job, p_status, p_rows, p_detail) $$;`;

(async () => {
  // A database with neither (the paper contracts' fixture): nothing happens.
  const bare = new PGlite();
  await bare.exec(ROLES + LOG);
  await bare.exec(MIG);
  assert.equal((await bare.query(`select to_regclass('public.mv_opportunities') is null as none`)).rows[0].none, true);

  const db = new PGlite();
  const q = async (sql) => (await db.query(sql)).rows;
  const one = async (sql) => (await q(sql))[0];
  await db.exec(ROLES + LOG + `
    create table public.data_freshness_spec (table_name text primary key, ts_column text,
                                             fresh_hours numeric, layer text not null, plain_english text);
    insert into public.data_freshness_spec values
      ('band_probabilities',      'computed_at',  12, 'model',    ''),
      ('resolution_verdicts',     'captured_at',  null, 'trading', ''),
      ('anomalies',               'detected_at',  null, 'health',  ''),
      ('ingest_log',              'logged_at',    3,  'health',   ''),
      ('prediction_checkpoints',  '-',            2,  'model',    ''),
      ('derived_band_day_volume', 'computed_at',  12, 'market',   ''),
      ('markets',                 'last_seen_at', 26, 'market',   '');
    create table public.band_probabilities (prob_id bigserial primary key, band_id int, computed_at timestamptz);
    create table public.resolution_verdicts (condition_id text, captured_at timestamptz);
    create index rv_already on public.resolution_verdicts (captured_at);
    create table public.prediction_checkpoints (checkpoint_id bigserial primary key, decided_at timestamptz);
    create table public.derived_band_day_volume (band_id int, trade_date date, computed_at timestamptz);
    create table public.markets (market_id int primary key, last_seen_at timestamptz);

    create table public.cities (city_key text primary key, timezone text);
    insert into public.cities values ('nyc', 'America/New_York'), ('tokyo', 'Asia/Tokyo'), ('kiri', 'Pacific/Kiritimati');
    create table public.opp_rows (edge_id int, band_id int, side text, city_key text,
                                  resolution_date date, score numeric, tradeable boolean);
    -- The live definition's shape, without its date condition, so the stored
    -- copy can hold a market that has closed - as a copy refreshed before a
    -- city's midnight does.
    create view public.v_opportunities as
      select o.edge_id, o.side, o.tradeable, o.band_id, o.city_key, o.resolution_date, c.timezone, o.score
        from public.opp_rows o join public.cities c on c.city_key = o.city_key
       order by o.score desc nulls last;
    revoke all on public.v_opportunities from public;
    grant select on public.v_opportunities to anon, authenticated, service_role;
    create view public.v_trade_plan as
      select band_id, side, city_key, score from public.v_opportunities where tradeable;
    grant select on public.v_trade_plan to anon, authenticated, service_role;
  `);
  // Open markets in three time zones, both sides, a null score; one market
  // two days gone.
  let e = 0;
  for (const [city, tz] of [['nyc', 'America/New_York'], ['tokyo', 'Asia/Tokyo'], ['kiri', 'Pacific/Kiritimati']]) {
    for (const ahead of [0, 1, 2]) {
      const band = e + 1;
      for (const side of ['YES', 'NO']) {
        e += 1;
        const score = side === 'NO' && ahead === 2 ? 'null' : (e * 0.37) % 5;
        await db.exec(`insert into public.opp_rows values (${e}, ${band}, '${side}', '${city}',
          (now() at time zone '${tz}')::date + ${ahead}, ${score}, ${e % 3 !== 0})`);
      }
    }
  }
  await db.exec(`insert into public.opp_rows values (900, 900, 'YES', 'nyc', current_date - 2, 4.5, true),
                                                    (901, 900, 'NO',  'nyc', current_date - 2, 0.1, false)`);

  const oid = async (rel) => (await one(`select '${rel}'::regclass::oid::int as o`)).o;
  const wrapperOid = await oid('public.v_opportunities');
  const planOid = await oid('public.v_trade_plan');
  const cols = `select attname, atttypid from pg_attribute where attrelid = 'public.v_opportunities'::regclass and attnum > 0 order by attnum`;
  const colsBefore = await q(cols);
  const open = `resolution_date >= (now() at time zone coalesce(timezone, 'UTC'))::date`;
  const planBefore = await q(`select * from public.v_trade_plan where band_id <> 900 order by band_id, side`);

  await db.exec(MIG);
  const mvOid = await oid('public.mv_opportunities');
  await db.exec(MIG);                                                   // re-runnable
  assert.equal(await oid('public.mv_opportunities'), mvOid, 'a second run rebuilt the stored copy');

  // ---- 1. THE FRESHNESS INDEXES ------------------------------------------
  const idx = async (t) => (await q(`select indexname, indexdef from pg_indexes where tablename = '${t}' order by indexname`));
  const bp = await idx('band_probabilities');
  assert.deepEqual(bp.map((r) => r.indexname), ['ad4_ix_fresh_band_probabilities', 'band_probabilities_pkey']);
  assert.match(bp[0].indexdef, /\(computed_at DESC NULLS LAST\)$/, 'the shape ad4_44 builds');
  assert.deepEqual((await idx('resolution_verdicts')).map((r) => r.indexname), ['rv_already'],
    'an index already leads with captured_at: no second one');
  assert.deepEqual((await idx('ingest_log')).map((r) => r.indexname), [], 'the spec names a column the table does not have');
  assert.deepEqual((await idx('prediction_checkpoints')).map((r) => r.indexname), ['prediction_checkpoints_pkey'],
    "'-' means no timestamp: nothing to index");
  assert.deepEqual((await idx('derived_band_day_volume')).map((r) => r.indexname), [],
    'its upsert rewrites computed_at: left out');
  assert.deepEqual((await idx('markets')).map((r) => r.indexname), ['markets_pkey'],
    'discovery rewrites last_seen_at: left out');
  assert.equal((await one(`select count(*)::int as n from pg_indexes where indexname like 'ad4_ix_fresh_%'`)).n, 1);

  // ---- 2. THE STORED OPPORTUNITIES ---------------------------------------
  // The same view, columns and grants; the view on it never dropped.
  assert.equal(await oid('public.v_opportunities'), wrapperOid, 'v_opportunities was dropped and recreated');
  assert.deepEqual(await q(cols), colsBefore);
  assert.equal(await oid('public.v_trade_plan'), planOid, 'the view built on it was dropped and recreated');
  const def = (await one(`select pg_get_viewdef('public.v_opportunities'::regclass, true) as d`)).d;
  assert.match(def, /FROM mv_opportunities/);

  // Every open row of the live view, and only those; the closed market is
  // hidden by the wrapper, though the copy holds it.
  const diff = async (a, b) => (await one(`select count(*)::int as n from (${a} except all ${b}) x`)).n;
  const live = `select * from public.v_opportunities_live where ${open}`;
  assert.equal(await diff(live, 'select * from public.v_opportunities'), 0);
  assert.equal(await diff('select * from public.v_opportunities', live), 0);
  assert.equal((await one(`select count(*)::int as n from public.v_opportunities`)).n, 18);
  assert.equal((await one(`select count(*)::int as n from public.mv_opportunities where band_id = 900`)).n, 2);
  assert.equal((await one(`select count(*)::int as n from public.v_opportunities where band_id = 900`)).n, 0,
    'a market whose date has passed in its city is not shown');
  assert.deepEqual(await q(`select * from public.v_trade_plan order by band_id, side`), planBefore,
    'the view built on it reads the same open rows');

  // The live order: score descending, nulls last.
  const scores = (await q(`select score from public.v_opportunities`)).map((r) => (r.score === null ? null : Number(r.score)));
  assert.equal(scores[scores.length - 1], null);
  const known = scores.filter((s) => s !== null);
  assert.deepEqual(known, [...known].sort((a, b) => b - a));

  // The unique key CONCURRENTLY needs.
  const key = await idx('mv_opportunities');
  assert.equal(key.length, 1);
  assert.match(key[0].indexdef, /UNIQUE INDEX mv_opportunities_key .*\(band_id, side\)/);

  // The browser reads the wrapper and what is built on it, never the copy or
  // the live definition; the service role reads the live one.
  for (const role of ['anon', 'authenticated']) {
    for (const [rel, ok] of [['v_opportunities', true], ['v_trade_plan', true],
                             ['v_opportunities_live', false], ['mv_opportunities', false]]) {
      assert.equal((await one(`select has_table_privilege('${role}', 'public.${rel}', 'select') as ok`)).ok, ok, `${role} ${rel}`);
    }
  }
  assert.equal((await one(`select has_table_privilege('service_role', 'public.v_opportunities_live', 'select') as ok`)).ok, true);
  await db.exec('set role anon');
  assert.equal((await one(`select count(*)::int as n from public.v_opportunities where tradeable`)).n,
               (await q(`select 1 from public.v_trade_plan`)).length, 'anon counts tradeable rows through the wrapper');
  await assert.rejects(db.query('select * from public.mv_opportunities'), /permission denied/);
  await db.exec('reset role');

  // refresh_page_cache() carries the copy, and says so.
  await db.exec(`insert into public.opp_rows values (950, 950, 'YES', 'tokyo', (now() at time zone 'Asia/Tokyo')::date, 9.9, true)`);
  assert.equal((await one(`select count(*)::int as n from public.v_opportunities where band_id = 950`)).n, 0, 'stored rows until a refresh');
  const out = (await one('select public.refresh_page_cache() as r')).r;
  assert.deepEqual(Object.keys(out), ['mv_opportunities'], 'the other stored copies are not in this database');
  assert.equal((await one(`select band_id from public.v_opportunities limit 1`)).band_id, 950, 'the new top score, first');
  const logged = await one(`select status, detail from public.ingest_log where job = 'refresh_page_cache'`);
  assert.equal(logged.status, 'ok');
  assert.ok('mv_opportunities' in logged.detail.ms);
  assert.equal((await one(`select has_function_privilege('anon', 'public.refresh_page_cache()', 'execute') as ok`)).ok, false);

  // AFTER A REINSTALL. sql/ad4_13 recreates v_opportunities live, here with a
  // column more; sql/ad4_89 stores it again, with that column.
  await db.exec(`alter table public.opp_rows add column fillable numeric default 7;
    create or replace view public.v_opportunities as
      select o.edge_id, o.side, o.tradeable, o.band_id, o.city_key, o.resolution_date, c.timezone, o.score, o.fillable
        from public.opp_rows o join public.cities c on c.city_key = o.city_key
       order by o.score desc nulls last;`);
  await db.exec(AD4_89);
  assert.match((await one(`select pg_get_viewdef('public.v_opportunities'::regclass, true) as d`)).d, /FROM mv_opportunities/);
  assert.equal((await one(`select fillable::int as f from public.v_opportunities limit 1`)).f, 7);
  assert.equal(await diff(`select * from public.v_opportunities_live where ${open}`, 'select * from public.v_opportunities'), 0);
  assert.equal(await diff('select * from public.v_opportunities', `select * from public.v_opportunities_live where ${open}`), 0);
  assert.equal(await oid('public.v_trade_plan'), planOid, 'the reinstall left the view built on it alone');
  assert.equal((await one(`select has_table_privilege('anon', 'public.mv_opportunities', 'select') as ok`)).ok, false);

  console.log('PASS: masthead-cache: the freshness max() columns get ad4_44\'s index on the named tables only (never a second, never where updates rewrite the column); v_opportunities is stored rows behind the same view, columns and grants, the view built on it untouched, closed markets hidden, the live order kept, the copy and the live definition the service role\'s only, refreshed by refresh_page_cache(); re-runnable, and ad4_89 stores a reinstalled view again');
})().catch((e) => { console.error(e); process.exit(1); });
