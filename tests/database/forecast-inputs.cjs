// ===========================================================================
// THE MODEL INPUTS HAVE THEIR OWN FRESHNESS (audit P3, 5 Oct 2026;
// 20261005110000): per active city, the main forecast's age and the seven
// models' current run's age are judged separately, a city that missed the
// last nightly fetch is named, and the station-corrected rows say whether
// they were combined from an earlier run. The page reads the view as anon;
// the tables under it stay closed.
// ===========================================================================
const { PGlite } = require('@electric-sql/pglite');
const fs = require('fs');
const path = require('path');
const assert = require('node:assert/strict');

const MIG = (f) => fs.readFileSync(path.join(__dirname, '..', '..', 'supabase', 'migrations', f), 'utf-8');
const P39 = MIG('20260926120000_station_correction.sql');
const FRESH = MIG('20261005110000_the_model_inputs_have_their_own_freshness.sql');
const MODELS = ['ecmwf_ifs025', 'gfs_seamless', 'icon_seamless', 'ukmo_seamless', 'jma_seamless',
                'gem_seamless', 'meteofrance_seamless'];

(async () => {
  // Without the weather tables (the paper-contracts fixture) it installs nothing and does not fail.
  const bare = new PGlite();
  await bare.exec(`create role anon; create role authenticated; create role service_role;
    create table public.cities (city_key text primary key, display_name text, timezone text, status text);`);
  await bare.exec(P39);
  await bare.exec(FRESH);
  assert.equal((await bare.query(`select to_regclass('public.v_city_forecast_inputs') v`)).rows[0].v, null);

  const db = new PGlite();
  await db.exec(`
    create role anon; create role authenticated; create role service_role;
    create table public.cities (city_key text primary key, display_name text not null, timezone text,
      status text not null default 'active');
    create table public.weather_forecasts (forecast_id bigserial primary key, city_key text not null,
      model text not null, run_at timestamptz not null, observed_at timestamptz not null default now(),
      for_date date not null, lead_days integer, forecast_max_c numeric, variables jsonb, source text not null);
    create table public.weather_forecast_models (city_key text not null, model text not null,
      run_at timestamptz not null, for_date date not null, lead_days integer not null,
      forecast_max_c numeric not null, source text not null, observed_at timestamptz not null default now(),
      primary key (city_key, model, run_at, for_date));
    alter table public.weather_forecast_models enable row level security;
    revoke all on public.weather_forecast_models from public, anon, authenticated;
    insert into public.cities (city_key, display_name, timezone, status) values
      ('fresh', 'Fresh', 'UTC', 'active'), ('missed', 'Missed', 'UTC', 'active'),
      ('stale', 'Stale', 'UTC', 'active'), ('absent', 'Absent', 'UTC', 'active'),
      ('gone', 'Gone', 'UTC', 'retired');
  `);
  await db.exec(P39);
  await db.exec(FRESH);
  await db.exec(FRESH);   // re-runnable

  const ago = (h) => `now() - interval '${h} hours'`;
  const today = `(now() at time zone 'UTC')::date`;
  // The main forecast: the engine's pick is the shortest lead, then the newest run.
  await db.exec(`insert into public.weather_forecasts (city_key, model, run_at, for_date, lead_days, forecast_max_c, source) values
    ('fresh', 'open_meteo_forecast', ${ago(1)}, ${today}, 0, 20, 'open-meteo'),
    ('fresh', 'open_meteo_forecast', ${ago(0.5)}, ${today}, 1, 21, 'open-meteo'),
    ('missed', 'open_meteo_forecast', ${ago(2)}, ${today}, 0, 20, 'open-meteo'),
    ('stale', 'open_meteo_forecast', ${ago(9)}, ${today}, 0, 20, 'open-meteo'),
    ('gone', 'open_meteo_forecast', ${ago(1)}, ${today}, 0, 20, 'open-meteo')`);
  // The models' current runs: fresh tonight (7 models), missed is a night behind, stale is 40 h old.
  const run = (city, h, models = MODELS, source = 'open-meteo-models-current') =>
    models.map((m) => `('${city}', 'open_meteo_${m}', ${ago(h)}, ${today}, 0, 20, '${source}')`).join(',');
  await db.exec(`insert into public.weather_forecast_models (city_key, model, run_at, for_date, lead_days, forecast_max_c, source) values
    ${run('fresh', 3)}, ${run('missed', 27)}, ${run('stale', 40, MODELS.slice(0, 5))},
    ${run('missed', 1, MODELS, 'open-meteo-previous-runs')}`);
  // What the corrected rows were combined from.
  await db.exec(`insert into public.derived_corrected_forecast (city_key, for_date, lead_days, combined_c, n_sources, sources,
      version, computed_at, inputs_oldest_run_at, inputs_newest_run_at) values
    ('fresh', ${today}, 0, 20, 7, '{}', 'station-correction:x', ${ago(2)}, ${ago(3)}, ${ago(3)}),
    ('fresh', ${today} + 1, 1, 20, 7, '{}', 'station-correction:x', ${ago(2)}, null, null),
    ('missed', ${today}, 1, 20, 7, '{}', 'station-correction:x', ${ago(2)}, ${ago(27)}, ${ago(27)})`);

  await db.exec('set role anon');
  const rows = (await db.query(`select * from public.v_city_forecast_inputs order by city_key`)).rows;
  const by = Object.fromEntries(rows.map((r) => [r.city_key, r]));
  assert.deepEqual(Object.keys(by).sort(), ['absent', 'fresh', 'missed', 'stale'], 'active cities only');

  // The main forecast's clock.
  assert.deepEqual([by.fresh.main_verdict, Number(by.fresh.main_age_h)], ['fresh', 1], 'the shortest lead, not the newest row');
  assert.equal(by.missed.main_verdict, 'fresh');
  assert.deepEqual([by.stale.main_verdict, Number(by.stale.main_age_h)], ['stale', 9]);
  assert.deepEqual([by.absent.main_verdict, by.absent.main_age_h], ['absent', null]);

  // The models' clock, judged on its own.
  assert.deepEqual([by.fresh.models_verdict, by.fresh.n_models, Number(by.fresh.models_behind_h)], ['fresh', 7, 0]);
  assert.deepEqual([by.missed.models_verdict, Number(by.missed.models_behind_h), Number(by.missed.models_age_h)],
                   ['behind', 24, 27], 'a missed nightly fetch, though its main forecast is fresh');
  assert.deepEqual([by.stale.models_verdict, by.stale.n_models], ['stale', 5]);
  assert.deepEqual([by.absent.models_verdict, by.absent.models_run_at], ['absent', null]);

  // What the corrected rows were combined from.
  assert.deepEqual([by.fresh.today_inputs_verdict, Number(by.fresh.today_inputs_behind_h)], ['current', 0]);
  assert.equal(by.fresh.tomorrow_inputs_verdict, 'not_recorded', 'a row from before the fit recorded its runs');
  assert.deepEqual([by.missed.today_inputs_verdict, Number(by.missed.today_inputs_behind_h)], ['earlier_run', 24]);
  assert.deepEqual([by.missed.tomorrow_inputs_verdict, by.stale.today_inputs_verdict], ['none', 'none']);

  // Ages and counts only; the tables under it stay closed.
  for (const t of ['weather_forecast_models', 'derived_corrected_forecast']) {
    await assert.rejects(db.query(`select 1 from public.${t} limit 1`), /permission denied/, t);
  }
  const cols = (await db.query(`select * from public.v_city_forecast_inputs limit 1`)).fields.map((f) => f.name);
  assert.ok(!cols.some((c) => /max_c|combined|forecast_c|sources/.test(c)), `no forecast value: ${cols}`);
  await db.exec('reset role');
  const [opts] = (await db.query(`select reloptions from pg_class where oid = 'public.v_city_forecast_inputs'::regclass`)).rows;
  assert.ok((opts.reloptions || []).includes('security_invoker=false'), "the owner's view, as v_city_observation_health");
  assert.equal((await db.query(`select has_table_privilege('authenticated', 'public.v_city_forecast_inputs', 'select') p`)).rows[0].p, false);

  console.log('forecast-inputs: the main forecast and the model inputs judged separately (fresh/stale/absent; behind for a missed nightly fetch); the corrected rows say whether they were combined from an earlier run (current/earlier_run/not_recorded/none); anon reads the view, not the tables; nothing installed without the weather tables; re-runnable');
})().catch((e) => { console.error(e); process.exit(1); });
