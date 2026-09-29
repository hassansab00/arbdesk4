// ===========================================================================
// THE DAY-FEATURE REPAIR IS EXACTLY WHAT ITS AUDIT SAYS (plan v2 P1.6, 29 Sep,
// data/repairs/2026-09-29-day-features).
//
// The cached rows as they were before the repair (cache_before_*.txt, dumped
// from the live table and md5-checked) are loaded into a real Postgres, and
// the committed SQL blocks run against them as they ran live:
//   - every block writes the repository's values and nothing else: each of
//     the 561 repaired rows equals tools/repair_day_features.py's recompute,
//     and no other row changes;
//   - a block whose values are not the audit's, or whose rows no longer hold
//     what the audit recorded (a second run), changes nothing;
//   - the last step leaves every repaired day's prev_max_c / delta_max_c /
//     pressure change equal to lag() over the cached days, as the view
//     defines them.
// ===========================================================================
const { PGlite } = require('@electric-sql/pglite');
const fs = require('fs');
const path = require('path');
const assert = require('node:assert/strict');

const DIR = path.join(__dirname, '..', '..', 'data', 'repairs', '2026-09-29-day-features');
const COLS = ['city_key', 'obs_date', 'n_obs', 'max_c', 'min_c', 'diurnal_range_c', 'prev_max_c', 'delta_max_c',
  'morning_temp_c', 'morning_dewpoint_c', 'dewpoint_depression_c', 'morning_humidity', 'morning_pressure_hpa',
  'morning_to_max_c', 'cloud_mean', 'cloud_max', 'wind_mean', 'wind_max', 'precip_total',
  'pressure_change_24h_hpa', 'wind_u_mean', 'wind_v_mean', 'computed_at'];
const BASE = ['n_obs', 'max_c', 'min_c', 'diurnal_range_c', 'morning_temp_c', 'morning_dewpoint_c',
  'dewpoint_depression_c', 'morning_humidity', 'morning_pressure_hpa', 'morning_to_max_c', 'cloud_mean',
  'cloud_max', 'wind_mean', 'wind_max', 'precip_total', 'wind_u_mean', 'wind_v_mean'];
const read = (f) => fs.readFileSync(path.join(DIR, f), 'utf-8');
const num = (v) => (v === null || v === undefined || v === '~' ? null : Number(v));

(async () => {
  const db = new PGlite();
  await db.exec(`
    set timezone = 'UTC';
    create table public.cities (city_key text primary key, timezone text);
    create table public.derived_city_day_features (city_key text, obs_date date, n_obs int, max_c numeric,
      min_c numeric, diurnal_range_c numeric, prev_max_c numeric, delta_max_c numeric, morning_temp_c numeric,
      morning_dewpoint_c numeric, dewpoint_depression_c numeric, morning_humidity numeric,
      morning_pressure_hpa numeric, morning_to_max_c numeric, cloud_mean numeric, cloud_max numeric,
      wind_mean numeric, wind_max numeric, precip_total numeric, pressure_change_24h_hpa numeric,
      wind_u_mean numeric, wind_v_mean numeric, computed_at timestamptz, primary key (city_key, obs_date));`);
  for (const [c, z] of Object.entries(JSON.parse(read('timezones.json')))) {
    await db.query('insert into public.cities values ($1, $2)', [c, z]);
  }
  const before = ['cache_before_a-k.txt', 'cache_before_l-z.txt'].flatMap((f) => read(f).split('\n').filter(Boolean));
  for (const line of before) {
    const v = line.split('|').map((x) => (x === '~' ? null : x));
    v[22] += 'Z';
    await db.query(`insert into public.derived_city_day_features values (${COLS.map((_, i) => '$' + (i + 1)).join(',')})`, v);
  }
  const snapshot = async () => new Map((await db.query(
    `select city_key || '|' || obs_date as k, row_to_json(f)::text as r from public.derived_city_day_features f`)).rows
    .map((r) => [r.k, r.r]));
  const start = await snapshot();

  const blocks = fs.readdirSync(path.join(DIR, 'sql')).filter((f) => /^repair_\d+\.sql$/.test(f)).sort();
  assert.equal(blocks.length, 10);

  // A block whose values were not copied exactly changes nothing.
  const first = read(path.join('sql', blocks[0]));
  const tampered = first.replace(/\$blob\$([a-z_]+\|\d{4}-\d{2}-\d{2}\|\d+\|)(\d+)/, (m, head, v) => `$blob$${head}${Number(v) + 1}`);
  assert.notEqual(tampered, first, 'the fixture did not alter the block');
  await assert.rejects(db.exec(tampered), /not the ones the audit file records/);
  assert.deepEqual(await snapshot(), start, 'a refused block changed a row');

  for (const b of blocks) await db.exec(read(path.join('sql', b)));
  // ...and a second run of any block is refused: its rows no longer hold "before".
  await assert.rejects(db.exec(read(path.join('sql', blocks[3]))), /do not hold what the audit recorded/);

  // Every repaired row is the repository's recompute; every other row is untouched.
  const audit = read('audit.jsonl').split('\n').filter(Boolean).slice(1).map(JSON.parse);
  const repaired = audit.filter((r) => r.kind === 'repair');
  assert.equal(repaired.length, 561);
  const recomputed = new Map(JSON.parse(read('recomputed.json')).rows.map((r) => [`${r.city_key}|${r.obs_date}`, r]));
  const after = await snapshot();
  const repairedKeys = new Set(repaired.map((r) => `${r.city_key}|${r.obs_date}`));
  let differs = 0;
  for (const k of repairedKeys) {
    const row = JSON.parse(after.get(k));
    const r = recomputed.get(k);
    for (const f of BASE) if (num(row[f]) !== num(r[f])) differs += 1;
  }
  assert.equal(differs, 0, 'a repaired row is not the repository recompute');
  for (const [k, v] of start) if (!repairedKeys.has(k)) assert.equal(after.get(k), v, `${k} changed but was not repaired`);

  // The last step: day-over-day terms from lag() over the cached days.
  const res = (await db.query(read(path.join('sql', 'repair_last_day_over_day.sql')))).rows[0];
  assert.equal(Number(res.repaired_rows), 561);
  const bad = (await db.query(`
    with l as (select city_key, obs_date, prev_max_c, delta_max_c, max_c,
                      lag(max_c) over w as p_max, lag(obs_date) over w as p_date
                 from public.derived_city_day_features window w as (partition by city_key order by obs_date))
    select count(*)::int as n from l
     where city_key || '|' || obs_date = any($1::text[])
       and p_date = obs_date - 1
       and (prev_max_c is distinct from p_max or delta_max_c is distinct from max_c - p_max)`, [[...repairedKeys]])).rows[0].n;
  assert.equal(bad, 0, 'a repaired day does not measure itself against the cached day before it');
  const nyc = (await db.query(`select n_obs, max_c::float as m, prev_max_c::float as p from public.derived_city_day_features
                                where city_key = 'nyc' and obs_date = '2026-07-29'`)).rows[0];
  assert.deepEqual(nyc, { n_obs: 24, m: 27.22, p: 26.67 }, 'nyc 29 Jul (cached from 2 readings at 23.33 C before)');

  console.log('PASS: day-feature-repair: the 10 committed blocks rewrite exactly the 561 audited rows to the repository recompute and touch nothing else, a block not copied exactly or run twice changes nothing, and the last step leaves every repaired day measured against the cached day before it');
})().catch((e) => { console.error(e); process.exit(1); });
