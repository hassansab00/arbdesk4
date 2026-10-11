// ===========================================================================
// THE HEAVY PAGE VIEWS READ STORED ROWS (plan v2 P6.5; WXPredict build 2.E
// part 2; 20261011100000_the_heavy_page_views_read_stored_rows).
//
// Six page views become wrappers over stored copies of their own
// definitions, replaced in place: same view, columns, grants, options and
// order, the views built on them untouched and reading the same rows. An
// invoker view's copy is readable by the page's roles; a definer view's is
// the service role's only, like every live copy. v_city_status keeps reading
// v_prediction_hindsight, now the stored rows; v_city_status_live is the
// same definition over the live hindsight, the service role's only, and sees
// a settled day the moment it is written. refresh_page_cache() carries the
// six. Re-runnable. After a view is reinstalled live the migration refuses
// (it removes nothing) and sql/ad4_89 stores it again, deriving the gate's
// view again.
// ===========================================================================
const { PGlite } = require('@electric-sql/pglite');
const fs = require('fs');
const path = require('path');
const assert = require('node:assert/strict');

const ROOT = path.join(__dirname, '..', '..');
const MIG = fs.readFileSync(path.join(ROOT, 'supabase', 'migrations',
  '20261011100000_the_heavy_page_views_read_stored_rows.sql'), 'utf-8');
const AD4_89 = fs.readFileSync(path.join(ROOT, 'sql', 'ad4_89_page_cache.sql'), 'utf-8');

const ROLES = `create role anon; create role authenticated; create role service_role;`;
const LOG = `
  create table public.ingest_log (job text, status text, rows int, detail jsonb);
  create function public.log_ingest(p_job text, p_status text, p_rows int, p_detail jsonb) returns void
    language sql as $$ insert into public.ingest_log values (p_job, p_status, p_rows, p_detail) $$;`;

// The live shapes, small: the hit record (definer, read by the summary and
// the city status), three invoker views over the verified outcomes, two
// definer skill views.
const HINDSIGHT = `
  select c.city_key, c.for_date, c.called_when, c.call_order, c.predicted_pct, c.hit
    from public.calls c`;
const VIEWS = `
  create table public.calls (city_key text, for_date date, called_when text, call_order int,
                             predicted_pct numeric, hit boolean);
  create table public.outcomes (band_id int, model_prob numeric, market_price numeric, edge_net_pp numeric,
                                settled_yes boolean, volume_usd numeric);
  create table public.skill_rows (city_key text, lead_days int, err numeric, cloud numeric);
  create table public.cities (city_key text primary key, timezone text, status text);

  create view public.v_prediction_hindsight as ${HINDSIGHT};
  create view public.v_prediction_hindsight_summary as
    select called_when, min(call_order) as call_order, count(*) filter (where hit is not null) as days,
           count(*) filter (where hit) as hits
      from public.v_prediction_hindsight group by called_when;
  create view public.v_city_status with (security_invoker = false) as
    with d as (select c.city_key, (now() at time zone coalesce(c.timezone, 'UTC'))::date as target_date
                 from public.cities c where coalesce(c.status, 'active') = 'active'),
         cp(checkpoint) as (values ('day_ahead'), ('noon')),
         settled as (select h.city_key, h.called_when as checkpoint, count(*) filter (where h.hit is not null) as days
                       from v_prediction_hindsight h group by h.city_key, h.called_when)
    select d.city_key, d.target_date, cp.checkpoint, coalesce(s.days, 0)::int as settled_days,
           case when coalesce(s.days, 0) < 2 then 'insufficient' else 'candidate' end as status
      from d cross join cp left join settled s on s.city_key = d.city_key and s.checkpoint = cp.checkpoint;

  create view public.v_edge_scaling as
    with b as (select width_bucket(edge_net_pp, 0, 25, 5) as edge_bucket, edge_net_pp, settled_yes, market_price
                 from public.outcomes where edge_net_pp is not null and market_price is not null)
    select edge_bucket, count(*)::int as n, round(avg(edge_net_pp), 2) as claimed_edge_pp
      from b where edge_bucket between 1 and 5 group by edge_bucket order by edge_bucket;
  create view public.v_calibration as
    select width_bucket(model_prob, 0, 1, 10) as bucket, count(*)::int as n, round(avg(model_prob), 4) as predicted
      from public.outcomes where model_prob is not null group by 1 order by 1;
  create view public.v_edge_realisation as
    with b as (select case when edge_net_pp < 0 then 'neg' when edge_net_pp < 5 then 'low' else 'high' end as edge_bucket,
                      case when edge_net_pp < 0 then 3 when edge_net_pp < 5 then 1 else 2 end as ord, volume_usd
                 from public.outcomes where edge_net_pp is not null)
    select edge_bucket, min(ord) as ord, count(*)::int as n, round(sum(coalesce(volume_usd, 0))) as volume_usd
      from b group by edge_bucket order by min(ord);
  alter view public.v_calibration set (security_invoker = true);
  alter view public.v_edge_realisation set (security_invoker = true);
  alter view public.v_edge_scaling set (security_invoker = true);
  create view public.v_model_forecast_skill as
    select city_key, lead_days, count(*)::int as n_days, round(avg(abs(err)), 2) as model_mae_c
      from public.skill_rows group by city_key, lead_days;
  create view public.v_condition_skill as
    select city_key, count(*)::int as n_days, round(avg(cloud), 2) as cloud_mae_oktas
      from public.skill_rows where cloud is not null group by city_key;

  -- The live grants: the pages read every view; the invoker views need the
  -- outcomes themselves; the hit record's calls are the owner's.
  grant select on public.v_prediction_hindsight, public.v_prediction_hindsight_summary, public.v_city_status,
                  public.v_edge_scaling, public.v_calibration, public.v_edge_realisation,
                  public.v_model_forecast_skill, public.v_condition_skill to anon, authenticated, service_role;
  grant select on public.outcomes to anon, authenticated;
`;

const STORED = {
  v_prediction_hindsight: 'mv_prediction_hindsight', v_edge_scaling: 'mv_edge_scaling',
  v_calibration: 'mv_calibration', v_edge_realisation: 'mv_edge_realisation',
  v_model_forecast_skill: 'mv_model_forecast_skill', v_condition_skill: 'mv_condition_skill',
};
const INVOKER = ['v_edge_scaling', 'v_calibration', 'v_edge_realisation'];
const ORDERED = ['v_edge_scaling', 'v_calibration', 'v_edge_realisation'];

(async () => {
  // A database with none of these (the paper contracts' fixture): nothing happens.
  const bare = new PGlite();
  await bare.exec(ROLES + LOG);
  await bare.exec(MIG);
  assert.equal((await bare.query(`select to_regclass('public.mv_prediction_hindsight') is null
                                     and to_regclass('public.v_city_status_live') is null as none`)).rows[0].none, true);

  const db = new PGlite();
  const q = async (sql) => (await db.query(sql)).rows;
  const one = async (sql) => (await q(sql))[0];
  await db.exec(ROLES + LOG + VIEWS);
  await db.exec(`
    insert into public.cities values ('nyc', 'America/New_York', 'active'), ('tokyo', 'Asia/Tokyo', 'active'),
                                     ('gone', 'UTC', 'retired');
    insert into public.calls values
      ('nyc', current_date - 3, 'day_ahead', 0, 40, true), ('nyc', current_date - 2, 'day_ahead', 0, 35, false),
      ('nyc', current_date - 2, 'noon', 3, 55, true), ('nyc', current_date - 1, 'noon', 3, 50, null),
      ('tokyo', current_date - 3, 'day_ahead', 0, 30, false), ('tokyo', current_date - 1, 'day_ahead', 0, 45, null);
    insert into public.outcomes
      select g, (g % 10) / 10.0 + 0.05, 0.3 + (g % 5) / 10.0, (g % 7) * 4 - 6, g % 3 = 0, g * 10
        from generate_series(1, 60) g;
    insert into public.skill_rows values ('nyc', 1, 0.8, 2), ('nyc', 1, -1.2, null), ('nyc', 2, 1.5, 3),
                                         ('tokyo', 1, 0.4, 5), ('tokyo', 2, -0.6, 4);
  `);

  const oid = async (rel) => (await one(`select '${rel}'::regclass::oid::int as o`)).o;
  const cols = async (rel) => q(`select attname, atttypid, atttypmod from pg_attribute
                                  where attrelid = '${rel}'::regclass and attnum > 0 and not attisdropped order by attnum`);
  const opts = async (rel) => (await one(`select reloptions from pg_class where oid = '${rel}'::regclass`)).reloptions;
  const rowsInOrder = async (rel) => (await q(`select t::text as r from public.${rel} t`)).map((x) => x.r);
  const diff = async (a, b) => (await one(`select count(*)::int as n from (${a} except all ${b}) x`)).n;

  const READERS = [...Object.keys(STORED), 'v_prediction_hindsight_summary', 'v_city_status'];
  const before = {};
  for (const v of READERS) {
    before[v] = { oid: await oid(`public.${v}`), cols: await cols(`public.${v}`), opts: await opts(`public.${v}`),
                  rows: await rowsInOrder(v) };
    await db.exec(`create table public.before_${v} as select * from public.${v}`);
  }
  const statusDef = (await one(`select pg_get_viewdef('public.v_city_status'::regclass, true) as d`)).d;

  await db.exec(MIG);
  const mvOids = {};
  for (const mv of Object.values(STORED)) mvOids[mv] = await oid(`public.${mv}`);
  await db.exec(MIG);                                                   // re-runnable
  for (const mv of Object.values(STORED)) assert.equal(await oid(`public.${mv}`), mvOids[mv], `a second run rebuilt ${mv}`);

  // ---- THE SAME VIEWS, READING STORED ROWS --------------------------------
  for (const v of READERS) {
    assert.equal(await oid(`public.${v}`), before[v].oid, `${v} was dropped and recreated`);
    assert.deepEqual(await cols(`public.${v}`), before[v].cols, `${v} changed its columns`);
    assert.deepEqual(await opts(`public.${v}`), before[v].opts, `${v} lost its options`);
    assert.equal(await diff(`select * from public.before_${v}`, `select * from public.${v}`), 0, v);
    assert.equal(await diff(`select * from public.${v}`, `select * from public.before_${v}`), 0, v);
  }
  for (const [v, mv] of Object.entries(STORED)) {
    assert.match((await one(`select pg_get_viewdef('public.${v}'::regclass, true) as d`)).d, new RegExp(`FROM ${mv}\\b`));
    const key = await q(`select indexdef from pg_indexes where tablename = '${mv}'`);
    assert.equal(key.length, 1);
    assert.match(key[0].indexdef, new RegExp(`UNIQUE INDEX ${mv}_key`), 'CONCURRENTLY needs a unique key');
  }
  for (const v of ORDERED) assert.deepEqual(await rowsInOrder(v), before[v].rows, `${v} lost its order`);
  for (const v of INVOKER) assert.deepEqual(await opts(`public.${v}`), ['security_invoker=true']);
  assert.deepEqual(await opts('public.v_city_status'), ['security_invoker=false']);

  // v_city_status itself is untouched: the same text, now over the stored rows.
  assert.equal((await one(`select pg_get_viewdef('public.v_city_status'::regclass, true) as d`)).d, statusDef);
  // The gate's view: the same definition over the live hindsight alone.
  const deps = async (rel) => (await q(`select distinct c.relname from pg_depend d join pg_rewrite r on r.oid = d.objid
      join pg_class c on c.oid = d.refobjid where r.ev_class = '${rel}'::regclass and c.relname like 'v_prediction_hindsight%'
      order by 1`)).map((x) => x.relname);
  assert.deepEqual(await deps('public.v_city_status_live'), ['v_prediction_hindsight_live']);
  assert.deepEqual(await deps('public.v_city_status'), ['v_prediction_hindsight']);
  assert.deepEqual(await opts('public.v_city_status_live'), ['security_invoker=false']);
  assert.equal(await diff('select * from public.v_city_status_live', 'select * from public.before_v_city_status'), 0);
  assert.equal(await diff('select * from public.before_v_city_status', 'select * from public.v_city_status_live'), 0);

  // ---- WHO READS WHAT -----------------------------------------------------
  const can = async (role, rel) => (await one(`select has_table_privilege('${role}', 'public.${rel}', 'select') as ok`)).ok;
  for (const role of ['anon', 'authenticated']) {
    for (const v of READERS) assert.equal(await can(role, v), true, `${role} ${v}`);
    for (const [v, mv] of Object.entries(STORED)) {
      assert.equal(await can(role, `${v}_live`), false, `${role} ${v}_live`);
      assert.equal(await can(role, mv), INVOKER.includes(v), `${role} ${mv}`);
    }
    assert.equal(await can(role, 'v_city_status_live'), false, `${role} v_city_status_live`);
  }
  for (const rel of ['v_city_status_live', ...Object.keys(STORED).map((v) => `${v}_live`), ...Object.values(STORED)]) {
    assert.equal(await can('service_role', rel), true, `service_role ${rel}`);
  }
  await db.exec('set role anon');
  for (const v of READERS) {
    assert.equal((await q(`select * from public.${v}`)).length, before[v].rows.length, `anon reads ${v}`);
  }
  await assert.rejects(db.query('select * from public.mv_prediction_hindsight'), /permission denied/);
  await assert.rejects(db.query('select * from public.v_city_status_live'), /permission denied/);
  await db.exec('reset role');

  // ---- A SETTLED DAY: THE GATE AT ONCE, THE PAGE AT THE REFRESH -----------
  await db.exec(`update public.calls set hit = true where city_key = 'tokyo' and for_date = current_date - 1`);
  const settled = async (rel) => (await one(`select settled_days from public.${rel}
                                              where city_key = 'tokyo' and checkpoint = 'day_ahead'`)).settled_days;
  assert.equal(await settled('v_city_status_live'), 2, 'the gate counts the day the moment it settles');
  assert.equal(await settled('v_city_status'), 1, 'the page counts the stored rows until a refresh');
  const out = (await one('select public.refresh_page_cache() as r')).r;
  assert.deepEqual(Object.keys(out).sort(), Object.values(STORED).sort(), 'the other stored copies are not in this database');
  assert.equal(await settled('v_city_status'), 2);
  assert.equal(await diff('select * from public.v_city_status', 'select * from public.v_city_status_live'), 0);
  assert.equal(await diff('select * from public.v_city_status_live', 'select * from public.v_city_status'), 0);
  const logged = await one(`select status, detail from public.ingest_log where job = 'refresh_page_cache'`);
  assert.equal(logged.status, 'ok');
  assert.ok('mv_prediction_hindsight' in logged.detail.ms);
  assert.equal((await one(`select has_function_privilege('anon', 'public.refresh_page_cache()', 'execute') as ok`)).ok, false);

  // ---- AFTER A REINSTALL ---------------------------------------------------
  // The hit record is reinstalled live with a column more (as a migration
  // replacing it does), and v_edge_scaling as an invoker view again.
  await db.exec(`create or replace view public.v_prediction_hindsight as ${HINDSIGHT.replace('c.hit', 'c.hit, 7 as engine_version')};
    create or replace view public.v_edge_scaling with (security_invoker = true) as
      with b as (select width_bucket(edge_net_pp, 0, 25, 5) as edge_bucket, edge_net_pp
                   from public.outcomes where edge_net_pp is not null and market_price is not null)
      select edge_bucket, count(*)::int as n, round(avg(edge_net_pp), 2) as claimed_edge_pp
        from b where edge_bucket between 1 and 5 group by edge_bucket order by edge_bucket;`);
  // The migration removes nothing: it refuses, and names the file that
  // rebuilds the copy.
  await assert.rejects(db.exec(MIG), /v_prediction_hindsight is live but a stored copy exists: run sql\/ad4_89_page_cache\.sql/);
  assert.match(MIG, /^(?![\s\S]*\bdrop\s+(materialized\s+)?view\b)/i, 'the migration holds no DROP');
  const summaryOid = await oid('public.v_prediction_hindsight_summary');
  await db.exec(AD4_89);
  assert.match((await one(`select pg_get_viewdef('public.v_prediction_hindsight'::regclass, true) as d`)).d,
               /FROM mv_prediction_hindsight/);
  assert.equal((await one(`select engine_version from public.v_prediction_hindsight limit 1`)).engine_version, 7);
  assert.equal(await oid('public.v_prediction_hindsight_summary'), summaryOid, 'the summary stayed bound');
  assert.deepEqual(await deps('public.v_city_status_live'), ['v_prediction_hindsight_live'], 'the gate\'s view derived again');
  assert.equal(await diff('select * from public.v_city_status', 'select * from public.v_city_status_live'), 0);
  assert.equal(await can('anon', 'v_city_status_live'), false);
  assert.equal(await can('service_role', 'v_city_status_live'), true);
  // The invoker view keeps its option and its copy readable by the page.
  assert.deepEqual(await opts('public.v_edge_scaling'), ['security_invoker=true']);
  assert.equal(await can('anon', 'mv_edge_scaling'), true);
  assert.equal(await can('anon', 'mv_prediction_hindsight'), false);
  await db.exec('set role anon');
  assert.ok((await q('select * from public.v_edge_scaling')).length > 0, 'anon reads the invoker wrapper');
  await db.exec('reset role');
  // Run again with everything stored: nothing is rebuilt.
  const mvAgain = await oid('public.mv_prediction_hindsight');
  await db.exec(AD4_89);
  assert.equal(await oid('public.mv_prediction_hindsight'), mvAgain);

  // A city status that names the hit record twice is not guessed at.
  const twice = new PGlite();
  await twice.exec(ROLES + LOG + VIEWS + `
    drop view public.v_city_status;
    create view public.v_city_status with (security_invoker = false) as
      select h.city_key, (select count(*) from v_prediction_hindsight x where x.city_key = h.city_key) as n
        from v_prediction_hindsight h;`);
  await assert.rejects(twice.exec(MIG), /v_city_status names v_prediction_hindsight 2 times, not once/);

  console.log('PASS: heavy-page-cache: six page views are stored rows behind the same views, columns, grants, options and order, the views built on them untouched; invoker copies readable by the pages, the rest and every live copy the service role\'s only; v_city_status counts the stored hit record and the gate\'s v_city_status_live the live one, which sees a settled day before the refresh; refresh_page_cache() carries the six; re-runnable; after a reinstall the migration refuses and ad4_89 stores the views again and derives the gate\'s view again');
})().catch((e) => { console.error(e); process.exit(1); });
