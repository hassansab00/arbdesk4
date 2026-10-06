// ===========================================================================
// EACH CITY HAS A DAILY STATUS (migration 20261006180000; WXPredict build F.2).
//
// candidate / watch / insufficient / unavailable, the worst that applies, with
// every reason; thresholds from the newest append-only city_status_rules row,
// named on every status row. Six cities, one per path, plus the rules record:
// never updated or deleted, read by the browser, written by the service role.
// ===========================================================================
const { PGlite } = require('@electric-sql/pglite');
const fs = require('fs');
const path = require('path');
const assert = require('node:assert/strict');

const MIG = (f) => fs.readFileSync(path.join(__dirname, '..', '..', 'supabase', 'migrations', f), 'utf-8');

(async () => {
  const db = new PGlite();
  await db.exec(`
    create role anon; create role authenticated; create role service_role;
    create schema arbdesk_private;
    create function arbdesk_private.immutable_record() returns trigger
    language plpgsql set search_path='' as $$
    begin raise exception 'Append-only record; write a linked correction instead'; end $$;
    create table public.cities (city_key text primary key, status text, timezone text, unit text);
    create table public.markets (market_id uuid default gen_random_uuid(), city_key text,
      resolution_date date, closed boolean);
    create table public.weather_observations (city_key text, valid_at timestamptz, source text);
    create table public.weather_forecasts (city_key text, model text, run_at timestamptz, for_date date);
    create table public.weather_forecast_models (city_key text, model text, run_at timestamptz,
      for_date date, lead_days int, forecast_max_c numeric);
    -- the settled record, as v_prediction_hindsight returns it (one row a call)
    create table public.v_prediction_hindsight (city_key text, called_when text, hit boolean);
  `);
  await db.exec(MIG('20261006180000_each_city_has_a_daily_status.sql'));

  const cities = ['good', 'disagree', 'young', 'nomarket', 'stale', 'nomodels', 'closed'];
  for (const c of cities) {
    await db.query(`insert into public.cities values ($1, 'active', 'UTC', 'C')`, [c]);
    if (c !== 'nomarket') {
      await db.query(`insert into public.markets (city_key, resolution_date, closed) values ($1, current_date, $2)`,
        [c, c === 'closed']);
    }
    await db.query(`insert into public.weather_observations values ($1, now() - $2::interval, 'IEM')`,
      [c, c === 'stale' ? '5 hours' : '40 minutes']);
    await db.query(`insert into public.weather_forecasts values ($1, 'open_meteo_forecast', now() - interval '3 hours', current_date)`, [c]);
    if (c !== 'nomodels') {
      const models = ['ecmwf', 'gfs', 'icon', 'ukmo', 'jma', 'gem', 'mf'];
      for (let i = 0; i < models.length; i++) {
        const t = 20 + (c === 'disagree' ? i : i * 0.3);       // span 6.0 against 1.8
        await db.query(`insert into public.weather_forecast_models values ($1, $2, now() - interval '20 hours', current_date, 1, $3)`,
          [c, models[i], t]);
      }
      // an older run of one model that disagrees wildly: only the newest run counts
      await db.query(`insert into public.weather_forecast_models values ($1, 'ecmwf', now() - interval '44 hours', current_date, 1, 40)`, [c]);
    }
    const settled = c === 'young' ? 3 : 12;
    for (let i = 0; i < settled; i++) {
      await db.query(`insert into public.v_prediction_hindsight values ($1, 'day_ahead', $2)`, [c, i % 3 === 0]);
    }
    await db.query(`insert into public.v_prediction_hindsight values ($1, 'day_ahead', null)`, [c]);   // pending: not settled
  }
  // a retired city never gets a status
  await db.exec(`insert into public.cities values ('gone', 'retired', 'UTC', 'C')`);

  const at = async (city) => (await db.query(
    `select * from public.v_city_status where city_key = $1 and target_date = current_date and checkpoint = 'day_ahead'`, [city])).rows[0];

  let r = await at('good');
  assert.equal(r.status, 'candidate');
  assert.match(r.reason, /^Fresh data, forecasts agree \(span 1\.8 C\), 12 settled days$/);
  assert.equal(r.rules_version, 'status-v1');
  assert.equal(Number(r.n_models), 7);
  assert.deepEqual(r.reasons, []);

  r = await at('disagree');
  assert.equal(r.status, 'watch');
  assert.equal(r.reason, 'Forecasts disagree (the models span 6.0 C)');

  r = await at('young');
  assert.equal(r.status, 'insufficient');
  assert.equal(r.reason, 'Too little settled history at this checkpoint (3 of 10 days)');

  r = await at('nomarket');
  assert.equal(r.status, 'unavailable');
  assert.equal(r.reason, 'No market listed for this day');

  r = await at('closed');
  assert.equal(r.status, 'unavailable');
  assert.equal(r.reason, 'Market closed');

  r = await at('stale');
  assert.equal(r.status, 'unavailable');
  assert.match(r.reason, /^Station reports stale \(last 5\.0 h ago\)$/);

  r = await at('nomodels');
  assert.equal(r.status, 'watch');
  assert.match(r.reason, /^Model forecasts for this day not in yet/);

  // the worst state wins and every reason is kept, the deciding one first
  await db.exec(`update public.weather_observations set valid_at = now() - interval '6 hours' where city_key = 'young'`);
  r = await at('young');
  assert.equal(r.status, 'unavailable');
  assert.equal(r.reasons.length, 2);
  assert.match(r.reasons[0], /^Station reports stale/);
  assert.match(r.reasons[1], /^Too little settled history/);

  // every active city, today and tomorrow, at all seven moments; no retired one
  const n = (await db.query(`select count(*)::int n, count(distinct city_key)::int c from public.v_city_status`)).rows[0];
  assert.equal(n.c, cities.length);
  assert.equal(n.n, cities.length * 2 * 7);
  const moments = (await db.query(`select array_agg(distinct checkpoint order by checkpoint) m from public.v_city_status`)).rows[0].m;
  assert.deepEqual(moments, ['d1_eve', 'day_ahead', 'morning', 'noon', 'postpeak_1h', 'prepeak_1h', 'prepeak_2h']);

  // a new rules version is a new row, and the view reads the newest
  await db.exec(`insert into public.city_status_rules (version, params, method_path, method_sha256, recorded_at)
    select 'status-v2', params || '{"disagreement_c": 7}'::jsonb, method_path, method_sha256, now() + interval '1 second'
      from public.city_status_rules where version = 'status-v1'`);
  r = await at('disagree');
  assert.equal(r.status, 'candidate', 'a span of 6.0 is under the newer 7.0');
  assert.equal(r.rules_version, 'status-v2');

  // the record is append-only
  for (const sql of [`update public.city_status_rules set params = '{}'::jsonb`,
                     `delete from public.city_status_rules`,
                     `truncate public.city_status_rules`]) {
    await assert.rejects(db.exec(sql), /Append-only record/, sql);
  }
  // a version missing a threshold, or named off-pattern, is refused
  await assert.rejects(db.exec(`insert into public.city_status_rules (version, params, method_path, method_sha256)
    values ('status-v3', '{"disagreement_c": 4}'::jsonb, 'x', repeat('a', 64))`), /check/);
  await assert.rejects(db.exec(`insert into public.city_status_rules (version, params, method_path, method_sha256)
    select 'v4', params, method_path, method_sha256 from public.city_status_rules limit 1`), /check/);

  // the browser reads the view and the rules, and cannot write a rule
  const grants = (await db.query(`select has_table_privilege('anon', 'public.v_city_status', 'select') v,
      has_table_privilege('anon', 'public.city_status_rules', 'select') r,
      has_table_privilege('anon', 'public.city_status_rules', 'insert') ri,
      has_table_privilege('service_role', 'public.city_status_rules', 'insert') si`)).rows[0];
  assert.deepEqual(grants, { v: true, r: true, ri: false, si: true });

  // re-runnable: the migration again changes nothing
  await db.exec(MIG('20261006180000_each_city_has_a_daily_status.sql'));
  assert.equal((await db.query(`select count(*)::int n from public.city_status_rules`)).rows[0].n, 2);

  console.log('PASS: city-status: candidate, watch (disagreement, models not in), insufficient, unavailable (no market, closed, stale station); the worst wins with every reason kept; every active city today and tomorrow at seven moments; the newest rules version decides and is named; the rules are append-only and well-formed; anon reads, only the service role writes; re-runnable');
})().catch((e) => { console.error(e); process.exit(1); });
