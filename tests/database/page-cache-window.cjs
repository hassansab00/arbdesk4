// ===========================================================================
// THE PAGE CACHE KEEPS THE DAYS IT SHOWS (WXPredict build 2.A,
// 20261007150000_the_page_cache_keeps_the_days_it_shows.sql).
//
// The stored ladder keeps yesterday on and nothing else changes for its
// readers: the wrapper v_prediction_ladder keeps its columns, grants and
// identity, the view built on it (v_city_prediction_confidence) is never
// dropped, and every row from yesterday on is the live view's row. The
// funnel's bucket planes read mv_city_ladder_edges: every edge of each active
// city's markets in the ladder's window, no more. refresh_page_cache keeps
// both fresh. Re-runnable, and a no-op where the ladder cache is not installed.
// ===========================================================================
const { PGlite } = require('@electric-sql/pglite');
const fs = require('fs');
const path = require('path');
const assert = require('node:assert/strict');

const MIG = fs.readFileSync(path.join(__dirname, '..', '..', 'supabase', 'migrations',
  '20261007150000_the_page_cache_keeps_the_days_it_shows.sql'), 'utf-8');

const ROLES = `create role anon; create role authenticated; create role service_role;`;

(async () => {
  // A database without the ladder cache (the paper contracts' fixture): nothing.
  const bare = new PGlite();
  await bare.exec(ROLES + `
    create table public.ingest_log (job text, status text, rows int, detail jsonb);
    create function public.log_ingest(p_job text, p_status text, p_rows int, p_detail jsonb) returns void
      language sql as $$ insert into public.ingest_log values (p_job, p_status, p_rows, p_detail) $$;`);
  await bare.exec(MIG);
  assert.equal((await bare.query(`select to_regclass('public.mv_city_ladder_edges') is null as none`)).rows[0].none, true);

  const db = new PGlite();
  const q = async (sql) => (await db.query(sql)).rows;
  const d = (n) => `current_date ${n < 0 ? '-' : '+'} ${Math.abs(n)}`;
  // The shape production has (20260924040000, 20260927170000): canonical
  // markets and bands, the live ladder, its stored copy, the wrapper the
  // pages read, and the confidence view built on the wrapper.
  await db.exec(ROLES + `
    create table public.ingest_log (job text, status text, rows int, detail jsonb);
    create function public.log_ingest(p_job text, p_status text, p_rows int, p_detail jsonb) returns void
      language sql as $$ insert into public.ingest_log values (p_job, p_status, p_rows, p_detail) $$;
    create table public.cities (city_key text primary key, status text);
    insert into public.cities values ('nyc', 'active'), ('paris', null), ('oldtown', 'retired');
    create table public.markets (market_id int primary key, city_key text, resolution_date date);
    create table public.bands (band_id int primary key, market_id int, band_lo numeric, band_hi numeric);
    create view public.v_canonical_markets as select * from public.markets;
    create view public.v_canonical_bands as select * from public.bands;
    create table public.ladder_rows (city_key text, for_date date, band_id int, band_lo numeric, band_hi numeric,
                                     side text, model_prob numeric);
    create view public.v_prediction_ladder_live as
      select city_key, for_date, band_id, band_lo, band_hi, side, model_prob from public.ladder_rows;
    create materialized view public.mv_prediction_ladder as
      select v.*, coalesce(v.side, '-') as cache_side_key from public.v_prediction_ladder_live v;
    create unique index mv_prediction_ladder_key on public.mv_prediction_ladder (band_id, cache_side_key);
    revoke all on public.mv_prediction_ladder from public, anon, authenticated;
    grant select on public.mv_prediction_ladder to service_role;
    create view public.v_prediction_ladder as
      select city_key, for_date, band_id, band_lo, band_hi, side, model_prob from public.mv_prediction_ladder;
    grant select on public.v_prediction_ladder to anon, authenticated, service_role;
    create view public.v_city_prediction_confidence as
      select city_key, for_date, max(model_prob) as modal from public.v_prediction_ladder
       where for_date >= current_date and side = 'YES' group by city_key, for_date;
    grant select on public.v_city_prediction_confidence to anon, authenticated, service_role;
  `);
  // Markets 50, 40, 3 and 1 days back, today, tomorrow, 16 and 17 ahead; a
  // retired city's market inside the window. Each band is laddered on both
  // sides, and the open tails carry a null edge.
  const days = [-50, -40, -3, -2, -1, 0, 1, 16, 17];
  let id = 0;
  for (const [city, list] of [['nyc', days], ['paris', [-40, 0, 1]], ['oldtown', [0]]]) {
    for (const n of list) {
      const m = ++id;
      await db.exec(`insert into public.markets values (${m}, '${city}', ${d(n)})`);
      for (const [lo, hi] of [[null, 20 + n], [20 + n, 21 + n], [21 + n, null]]) {
        const b = m * 10 + (lo === null ? 0 : hi === null ? 2 : 1);
        await db.exec(`insert into public.bands values (${b}, ${m}, ${lo}, ${hi})`);
        for (const side of ['YES', 'NO']) {
          await db.exec(`insert into public.ladder_rows values ('${city}', ${d(n)}, ${b}, ${lo}, ${hi}, '${side}', ${0.1 * (b % 7)})`);
        }
      }
    }
  }
  await db.exec('refresh materialized view public.mv_prediction_ladder');
  const oid = async (rel) => (await q(`select '${rel}'::regclass::oid::int as o`))[0].o;
  const wrapperOid = await oid('public.v_prediction_ladder');
  const confidenceOid = await oid('public.v_city_prediction_confidence');
  const wrapperCols = await q(`select attname, atttypid from pg_attribute where attrelid = 'public.v_prediction_ladder'::regclass and attnum > 0 order by attnum`);
  const confidenceBefore = await q(`select * from public.v_city_prediction_confidence order by city_key, for_date`);

  await db.exec(MIG);
  const mvOid = await oid('public.mv_prediction_ladder');
  await db.exec(MIG);                                            // re-runnable
  assert.equal(await oid('public.mv_prediction_ladder'), mvOid, 'a second run rebuilt the stored ladder');

  // 1. THE STORED LADDER: yesterday on, every such row of the live view.
  const minDay = (await q(`select min(for_date) - current_date as n from public.mv_prediction_ladder`))[0].n;
  assert.equal(minDay, -1, 'the stored ladder keeps a day before yesterday, or not yesterday');
  const diff = async (a, b) => (await q(`select count(*)::int as n from (${a} except all ${b}) x`))[0].n;
  const live = `select city_key, for_date, band_id, band_lo, band_hi, side, model_prob from public.v_prediction_ladder_live where for_date >= current_date - 1`;
  const wrapped = `select city_key, for_date, band_id, band_lo, band_hi, side, model_prob from public.v_prediction_ladder`;
  assert.equal(await diff(live, wrapped), 0);
  assert.equal(await diff(wrapped, live), 0);
  assert.equal((await q(`select count(*)::int as n from public.v_prediction_ladder`))[0].n,
               (await q(`select count(*)::int as n from public.v_prediction_ladder_live where for_date >= current_date - 1`))[0].n);

  // The wrapper is the same view, with the same columns and grants, and the
  // view built on it was never dropped and reads the same rows.
  assert.equal(await oid('public.v_prediction_ladder'), wrapperOid, 'the wrapper was dropped and recreated');
  assert.deepEqual(await q(`select attname, atttypid from pg_attribute where attrelid = 'public.v_prediction_ladder'::regclass and attnum > 0 order by attnum`), wrapperCols);
  assert.equal(await oid('public.v_city_prediction_confidence'), confidenceOid, 'the confidence view was dropped and recreated');
  assert.deepEqual(await q(`select * from public.v_city_prediction_confidence order by city_key, for_date`), confidenceBefore);
  assert.ok(confidenceBefore.length > 0);
  for (const role of ['anon', 'authenticated']) {
    assert.equal((await q(`select has_table_privilege('${role}', 'public.v_prediction_ladder', 'select') as ok`))[0].ok, true);
    assert.equal((await q(`select has_table_privilege('${role}', 'public.v_city_prediction_confidence', 'select') as ok`))[0].ok, true);
    assert.equal((await q(`select has_table_privilege('${role}', 'public.mv_prediction_ladder', 'select') as ok`))[0].ok, false);
  }
  // The unique key CONCURRENTLY needs, under its old name; no copy left behind.
  const idx = await q(`select indexname, indexdef from pg_indexes where tablename = 'mv_prediction_ladder'`);
  assert.equal(idx.length, 1);
  assert.equal(idx[0].indexname, 'mv_prediction_ladder_key');
  assert.match(idx[0].indexdef, /UNIQUE INDEX .* \(band_id, cache_side_key\)/);
  assert.equal((await q(`select to_regclass('public.mv_prediction_ladder_whole') is null as gone`))[0].gone, true);
  assert.equal((await q(`select count(*)::int as n from pg_class where relname like 'mv_prediction_ladder%'`))[0].n, 2,
    'only the stored ladder and its key are left');

  // 2. THE EDGES: every edge of each active city's markets 45 days back to 16
  //    ahead - the 50-day market, the 17-day one and the retired city's are
  //    not in it - and equal to the edges of the whole ladder before the cut.
  const edges = await q(`select city_key, edge::int as edge from public.v_city_ladder_edges order by city_key, edge`);
  const want = [];
  for (const [city, list] of [['nyc', days], ['paris', [-40, 0, 1]]]) {
    const set = new Set();
    for (const n of list) if (n >= -45 && n <= 16) { set.add(20 + n); set.add(21 + n); }
    for (const e of [...set].sort((a, b) => a - b)) want.push({ city_key: city, edge: e });
  }
  assert.deepEqual(edges, want);
  const wholeLadderEdges = `select distinct city_key, e from public.v_prediction_ladder_live l
      cross join lateral (values (l.band_lo), (l.band_hi)) x(e)
     where e is not null and city_key <> 'oldtown' and for_date between current_date - 45 and current_date + 16`;
  assert.equal(await diff(wholeLadderEdges, 'select city_key, edge from public.v_city_ladder_edges'), 0);
  assert.equal(await diff('select city_key, edge from public.v_city_ladder_edges', wholeLadderEdges), 0);
  for (const role of ['anon', 'authenticated']) {
    assert.equal((await q(`select has_table_privilege('${role}', 'public.v_city_ladder_edges', 'select') as ok`))[0].ok, true);
    assert.equal((await q(`select has_table_privilege('${role}', 'public.mv_city_ladder_edges', 'select') as ok`))[0].ok, false);
  }
  await db.exec('set role anon');
  assert.equal((await q(`select count(*)::int as n from public.v_city_ladder_edges where city_key = 'nyc'`))[0].n,
               want.filter((r) => r.city_key === 'nyc').length);
  await assert.rejects(db.query('select count(*) from public.mv_city_ladder_edges'), /permission denied/);
  await db.exec('reset role');

  // 3. THE REFRESH keeps both fresh: a new market and its ladder appear.
  const m = ++id;
  await db.exec(`insert into public.markets values (${m}, 'paris', ${d(2)});
                 insert into public.bands values (${m * 10 + 1}, ${m}, 30, 31);
                 insert into public.ladder_rows values ('paris', ${d(2)}, ${m * 10 + 1}, 30, 31, 'YES', 0.4);`);
  const out = (await q(`select public.refresh_page_cache() as r`))[0].r;
  assert.ok('mv_prediction_ladder' in out && 'mv_city_ladder_edges' in out, JSON.stringify(out));
  assert.ok(!('mv_forecast_convergence_all' in out), 'a cache not installed here was refreshed');
  assert.equal((await q(`select count(*)::int as n from public.v_prediction_ladder where band_id = ${m * 10 + 1}`))[0].n, 1);
  assert.equal((await q(`select count(*)::int as n from public.v_city_ladder_edges where city_key = 'paris' and edge in (30, 31)`))[0].n, 2);
  assert.equal((await q(`select count(*)::int as n from public.ingest_log where job = 'refresh_page_cache'`))[0].n, 1);
  for (const role of ['anon', 'authenticated']) {
    assert.equal((await q(`select has_function_privilege('${role}', 'public.refresh_page_cache()', 'execute') as ok`))[0].ok, false);
  }

  console.log('PASS: page-cache-window: the stored ladder keeps yesterday on and equals the live view there, the wrapper and the view on it keep their identity, columns, rows and grants, the unique key keeps its name; the funnel edges are every edge of the active cities\' markets 45 days back to 16 ahead, equal to the whole ladder\'s, anon reads the view not the copy; the refresh keeps both fresh; re-runnable, a no-op without the ladder cache');
})().catch((e) => { console.error(e); process.exit(1); });
