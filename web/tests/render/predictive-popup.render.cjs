// RENDER TEST: a city's popup on the Predictive page (Hassan, 10 Oct: "once i
// click on a card for the cities, i want a popup to show all details about the
// predictions for that particular city, including the predicted max temp of
// the day, and a temp ensemble, and all usable data that can help me decide on
// the trade, includes the accuracy of the city based on our historical data").
// Not part of CI: it builds the app and drives Chromium.
//
//   node tests/render/predictive-popup.render.cjs        (from web/)
//   SKIP_BUILD=1 node tests/render/predictive-popup.render.cjs
//
// tests/fakePostgrest serves Tokyo's ladder for tomorrow and the page's other
// reads; Open-Meteo's ensemble is answered by the test with five members whose
// maxima are known, so the bucket shares can be checked exactly.
const assert = require('node:assert/strict');
const { execFileSync, spawn } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const { fakePostgrest } = require('../fakePostgrest.cjs');

const WEB = path.join(__dirname, '..', '..');
const OUT = path.join(__dirname, 'out');
const PORT_DB = 54332, PORT_WEB = 3110;
const playwright = require(process.env.PLAYWRIGHT_MODULE || '/opt/node-tools/node_modules/playwright');

const day = (n) => new Date(Date.now() + n * 86400000).toISOString().slice(0, 10);
const TOMORROW = day(1);
const MONTH = Number(TOMORROW.slice(5, 7));

const CITIES = [
  { city_key: 'tokyo', display_name: 'Tokyo', unit: 'C', latitude: 35.55, longitude: 139.75, timezone: 'Asia/Tokyo',
    icao: 'RJTT', station_name: 'Haneda', status: 'active' },
];
// Buckets <22, 22 ... 27, >=28 C; the platform favours 25, the market 24.
const BANDS = [['lo', null, 22, true, false], ...[22, 23, 24, 25, 26, 27].map((t) => [String(t), t, t + 1, false, false]),
               ['hi', 28, null, false, true]];
const PROB = { lo: 0.01, 22: 0.03, 23: 0.1, 24: 0.27, 25: 0.33, 26: 0.17, 27: 0.06, hi: 0.03 };
const PRICE = { lo: 0.01, 22: 0.02, 23: 0.12, 24: 0.41, 25: 0.28, 26: 0.11, 27: 0.04, hi: 0.01 };
const LABEL = (id) => (id === 'lo' ? '21°C or below' : id === 'hi' ? '28°C or higher' : `${id}°C`);
const LADDER = BANDS.flatMap(([id, lo, hi, openLow, openHigh]) => ['YES', 'NO'].map((side) => ({
  city_key: 'tokyo', for_date: TOMORROW, band_id: `tokyo-${id}`, band_index: 1, band_label: LABEL(id), side,
  model_prob: PROB[id], forecast_max_c: 24.9, sigma_c: 1.1, confidence: 0.6, regime_label: 'normal',
  market_price: side === 'YES' ? PRICE[id] : Math.round((1 - PRICE[id]) * 100) / 100,
  edge_net_pp: side === 'YES' ? Math.round((PROB[id] - PRICE[id] - 0.01) * 1000) / 1000 : -0.02,
  depth_5c: 40, tradeable: id === '25' && side === 'YES', block_reason: null, edge_at: `${day(0)}T15:00:00Z`,
  band_lo: lo, band_hi: hi, open_low: openLow, open_high: openHigh, closed: false, won: null,
  centre_c: 24.6, forecast_sigma_c: 1.1, observed_floor_c: null, prob_at: `${day(0)}T14:40:00Z`,
  priced_from: 'station_correction:sc1+station-mos:m1',
})));
const HINDSIGHT = [1, 2, 3, 4].map((n) => ({
  city_key: 'tokyo', for_date: day(-n), called_when: 'day_ahead', call_order: 1, after_peak: false,
  called_at: `${day(-n - 1)}T15:00:00Z`, predicted_band: n % 2 ? '24°C' : '25°C', predicted_pct: 31,
  actual_band: n % 2 ? '24°C' : '26°C', observed_max_c: n % 2 ? 24.3 : 26.1, forecast_max_c: 24.8,
  forecast_error_c: n % 2 ? -0.4 : 1.2, hit: n % 2 === 1, market_band: '24°C', market_hit: n % 2 === 1,
  outcome_source: 'venue', scheduled_local: null, engine_version: 'e1', prob_on_winner: 0.3,
}));
const TABLES = {
  cities: CITIES,
  v_prediction_ladder: LADDER,
  v_forecast_convergence_all: ['ecmwf_ifs025', 'gfs_seamless'].map((model, i) => ({
    city_key: 'tokyo', for_date: TOMORROW, model, lead_days: 1, forecast_max_c: 24.5 + i, run_at: `${day(0)}T00:00:00Z`,
    is_past: false, is_settled: false, observed_max_c: null, error_c: null })),
  v_city_status: [{ city_key: 'tokyo', target_date: TOMORROW, checkpoint: 'day_ahead', status: 'candidate',
    reason: 'every input fresh', reasons: ['every input fresh'], station_age_h: 0.5, forecast_run_at: `${day(0)}T00:00:00Z`,
    n_models: 7, models_span_c: 1.8, disagreement_c: 4.8, settled_days: 40, min_settled_days: 20, rules_version: 'cs1' }],
  v_prediction_hindsight: HINDSIGHT,
  v_city_hit_summary: [{ city_key: 'tokyo', display_name: 'Tokyo', days: 40, model_hits: 15, model_hit_rate: 0.375,
    avg_model_prob_on_winner: 0.3, brier_model: 0.7, brier_uniform: 0.88, mae_c: 1.1, bias_c: 0.2,
    avg_hours_before_day: 9, first_day: day(-41), last_day: day(-1), h2h_days: 38, h2h_model_hits: 14,
    market_hits: 18, market_hit_rate: 0.474, brier_model_h2h: 0.71, brier_market: 0.62, days_we_beat_the_market: 11,
    verdict: 'the market has been the better caller here', centre_days: 30, centre_mae_c: 0.82, centre_bias_c: -0.1 }],
  v_prediction_scorecard_all: [
    { city_key: 'tokyo', model: 'ecmwf_ifs025', lead_days: 1, n_days: 30, mae_c: 0.9, bias_c: 0.3, error_sd_c: 1.1, worst_c: 3.2, hit_rate_pct: 36, within_1c_pct: 64 },
    { city_key: 'tokyo', model: 'gfs_seamless', lead_days: 1, n_days: 30, mae_c: 1.3, bias_c: -0.4, error_sd_c: 1.5, worst_c: 4.0, hit_rate_pct: 27, within_1c_pct: 50 },
  ],
  derived_weather_peak: [{ city_key: 'tokyo', month: MONTH, peak_hour_local: 13.5, window_width_h: 3.0, n_days: 61 }],
};

// FIVE MEMBERS whose maxima on Tokyo's tomorrow are 23.2, 24.4, 24.6, 25.1 and
// 27.9 C, at 14:00 local; the venue reads them 23, 24, 25, 25 and 28.
const PEAKS = [23.2, 24.4, 24.6, 25.1, 27.9];
function ensembleAnswer() {
  const start = Date.parse(`${day(-1)}T00:00:00Z`);
  const time = [], members = PEAKS.map(() => []);
  for (let h = 0; h < 24 * 5; h++) {
    const t = new Date(start + h * 3600000);
    time.push(t.toISOString().slice(0, 16));
    const local = new Date(t.getTime() + 9 * 3600000);
    const isPeak = local.toISOString().slice(0, 10) === TOMORROW && local.getUTCHours() === 14;
    PEAKS.forEach((p, i) => members[i].push(isPeak ? p : 15 + (local.getUTCHours() % 5)));
  }
  const hourly = { time, temperature_2m: members[0] };
  members.slice(1).forEach((m, i) => { hourly[`temperature_2m_member${String(i + 1).padStart(2, '0')}`] = m; });
  return { hourly };
}

async function waitFor(url, ms = 60000) {
  const end = Date.now() + ms;
  while (Date.now() < end) {
    try { if ((await fetch(url)).ok) return; } catch { /* not up yet */ }
    await new Promise((r) => setTimeout(r, 500));
  }
  throw new Error(`${url} did not come up`);
}

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const fake = await fakePostgrest({ tables: TABLES, port: PORT_DB });
  const env = { ...process.env, NEXT_PUBLIC_SUPABASE_URL: fake.url, NEXT_PUBLIC_SUPABASE_ANON_KEY: 'sb_publishable_render_test',
                SUPABASE_SERVICE_KEY: 'service-key-for-the-fake', NEXT_TELEMETRY_DISABLED: '1' };
  delete env.OPERATOR_SIGN_IN;
  if (!process.env.SKIP_BUILD) execFileSync(path.join(WEB, 'node_modules', '.bin', 'next'), ['build'], { cwd: WEB, env, stdio: 'inherit' });
  const server = spawn(path.join(WEB, 'node_modules', '.bin', 'next'), ['start', '-p', String(PORT_WEB), '-H', '127.0.0.1'],
                       { cwd: WEB, env, stdio: 'ignore' });
  const browser = await playwright.chromium.launch();
  try {
    await waitFor(`http://127.0.0.1:${PORT_WEB}/predictive`);
    const page = await browser.newPage({ viewport: { width: 1360, height: 1500 } });
    const errors = [];
    page.on('pageerror', (e) => errors.push(String(e)));
    const asked = [];
    await page.route('https://ensemble-api.open-meteo.com/v1/ensemble**', (route) => {
      asked.push(new URL(route.request().url()).searchParams.get('models'));
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(ensembleAnswer()) });
    });
    await page.route('https://ensemble-api.open-meteo.com/data/**', (route) => route.fulfill({
      status: 200, contentType: 'application/json', body: JSON.stringify({ last_run_initialisation_time: Math.floor(Date.parse(`${day(0)}T00:00:00Z`) / 1000) }) }));
    await page.goto(`http://127.0.0.1:${PORT_WEB}/predictive`);

    // The card opens it, anywhere on it.
    const card = page.getByTitle(/Open this city.s day in detail/).first();
    await card.waitFor({ timeout: 30000 });
    await card.click();
    const dialog = page.getByRole('dialog', { name: 'Tokyo: the day in detail' });
    await dialog.waitFor({ timeout: 10000 });
    await dialog.getByText(/5 members · median/).nth(1).waitFor({ timeout: 15000 });
    const text = await dialog.innerText();

    assert.match(text, /Platform.s pick\s+25°C · 33% likely · market charges 28c/);
    assert.match(text, /Market.s pick\s+24°C at 41c · differs from the platform.s/);
    assert.match(text, /Predicted maximum\s+24\.6°C ± 1\.1°C \(1 sd\)/);
    assert.match(text, /raw public input 24\.9°C/);
    assert.match(text, /Usual peak\s+13:30/);
    // the ensemble: median 24.6, mean 25.04, 10-90% 23.68 to 26.78 (numpy's interpolation on the five)
    assert.match(text, /5 members · median 24\.6°C · mean 25\.0°C/);
    assert.match(text, /10-90%: 23\.7°C to 26\.8°C/);
    // the ladder: each bucket's share of members, read as the venue reads
    const share = async (label) => (await dialog.locator('tr', { hasText: label }).first().innerText()).replace(/\s+/g, ' ');
    assert.match(await share('23°C'), /^23°C 10% 20% 20% 12c/);
    assert.match(await share('24°C'), /^24°C market 27% 20% 20% 41c/);
    assert.match(await share('25°C'), /^25°C pick 33% 40% 40% 28c/);
    assert.match(await share('28°C or higher'), /^28°C or higher 3% 20% 20%/);
    // the record
    assert.match(text, /Day-ahead, 40 settled days/);
    assert.match(text, /the platform.s pick won 38%, the market.s 47% on the 38 days both priced/);
    assert.match(text, /predicted maximum missed by 0\.8°C on average/);
    assert.match(text, /Day ahead\s+2\/4 \(50%\)/);
    assert.match(text, /ecmwf_ifs025\s+1 d\s+30\s+0\.9°C/);
    assert.deepEqual(asked.sort(), ['ecmwf_ifs025', 'gfs025']);
    await page.screenshot({ path: path.join(OUT, 'predictive-popup.png'), fullPage: false });

    // Escape closes it.
    await page.keyboard.press('Escape');
    await dialog.waitFor({ state: 'detached', timeout: 5000 });
    assert.deepEqual(errors, [], 'the page threw');
    console.log(`PASS: render - Tokyo's card opens its popup: the pick and the market's, the predicted maximum and its input, the usual peak, both ensembles (5 members, median 24.6), every bucket with the platform's probability beside each ensemble's share (25: 33% / 40%), the city's record at every moment and per public model; Escape closes it; screenshot in ${OUT}`);
  } finally {
    await browser.close();
    server.kill();
    fake.close();
  }
})().catch((e) => { console.error(e); process.exit(1); });
