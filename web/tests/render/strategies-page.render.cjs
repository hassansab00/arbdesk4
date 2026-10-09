// RENDER TEST: every active strategy's "how it works" panel on a local
// production build (Hassan, 9 Oct: "we need the how it works explanation for
// each strategy, we used to have it visible when we clicked but now no").
// Not part of CI: it builds the app and drives Chromium.
//
//   node tests/render/strategies-page.render.cjs        (from web/)
//   SKIP_BUILD=1 node tests/render/strategies-page.render.cjs
//
// The page reads v_strategy_board with the anon key; tests/fakePostgrest
// serves the 13 active rows as the live view returned them to anon on 9 Oct
// ~09:00Z (every one "on, but nothing has met its conditions in 30 days"),
// plus one retired row. Each panel is opened in turn and must be a
// walkthrough, not "No walkthrough is written"; the engine panels must show
// what holdings_solver buys on the worked market (tests/kelly.test.cjs).
const assert = require('node:assert/strict');
const { execFileSync, spawn } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const { fakePostgrest } = require('../fakePostgrest.cjs');

const WEB = path.join(__dirname, '..', '..');
const OUT = path.join(__dirname, 'out');
const PORT_DB = 54331, PORT_WEB = 3109;
const playwright = require(process.env.PLAYWRIGHT_MODULE || '/opt/node-tools/node_modules/playwright');

const ON = 'on, but nothing has met its conditions in 30 days';
const ACTIVE = [
  ['s2_combination_arb', 'Combination arb', 'BOTH', 'HASSAN'],
  ['s10_winner', 'S10 max-temp winner: the most probable bucket', 'YES'],
  ['s10_growth', 'S10 max-temp winner: the best-growth tradeable bucket', 'YES'],
  ['s10_lock', 'S10 max-temp winner: the ladder under a no-loss lock', 'YES'],
  ['s11_ladder', 'S11 ladder optimiser: the growth-optimal YES set', 'YES'],
  ['s11_lock', 'S11 ladder optimiser under a no-loss lock', 'YES'],
  ['s12_no', 'S12 overpriced bucket: NO', 'NO'],
  ['s10_winner_model', 'S10 max-temp winner, model only (w = 1): the most probable bucket', 'YES'],
  ['s10_growth_model', 'S10 max-temp winner, model only (w = 1): the best-growth tradeable bucket', 'YES'],
  ['s10_lock_model', 'S10 max-temp winner, model only (w = 1): the ladder under a no-loss lock', 'YES'],
  ['s11_ladder_model', 'S11 ladder optimiser, model only (w = 1): the growth-optimal YES set', 'YES'],
  ['s11_lock_model', 'S11 ladder optimiser, model only (w = 1), under a no-loss lock', 'YES'],
  ['s12_no_model', 'S12 overpriced bucket, model only (w = 1): NO', 'NO'],
];
const row = ([strategy_id, name, side, origin = 'engine'], extra = {}) => ({
  strategy_id, name, side, origin, enabled: true, conflict_class: null, universe: null, regime_filter: null,
  capital_cap_pct: null, max_concurrent: null, fired_30d: 0, waiting: 0, last_fired_at: null, filled_all_time: 0,
  won_all_time: 0, net_pnl: null, avg_slippage_c: null, win_rate_pct: null, verdict: ON, settled_signals: 0,
  correct_signals: 0, hit_rate_pct: null, marked_signals: 0, mark_stake_per_share: null, mark_net_per_share: null,
  return_on_stake_pct: null, mark_basis: null, mark_win_rate_pct: null, ...extra,
});
const BOARD = [
  ...ACTIVE.map((a) => row(a)),
  row(['s4_tail_fade', 'Tail fade', 'NO', 'CLAUDE_CANDIDATE'],
      { enabled: false, verdict: 'retired 2026-09-22 - 451 marked, -2.2c on the dollar, 77.4% of its calls right and still losing' }),
];

// What each engine panel must show: holdings_solver's answer on the worked market.
const MUST = {
  s10_growth_model: /Here that is 31–32/,
  s11_ladder_model: /Here that is 30–31, 31–32/,
  s12_no_model: /NO on 32–33, 33–34, 34–35, ≥35/,
  s10_winner_model: /It takes nothing here\.[\s\S]*no edge/,
  s10_lock_model: /It takes nothing here\.[\s\S]*No book avoids a loss in every outcome/,
  s11_lock_model: /It takes nothing here\.[\s\S]*No book avoids a loss in every outcome/,
  s12_no: /It takes nothing here\.[\s\S]*At w = 0[\s\S]*s12_no_model[\s\S]*buy NO 32–33, NO 33–34, NO 34–35, NO ≥35/,
  s10_winner: /It takes nothing here\.[\s\S]*At w = 0/,
  s2_combination_arb: /It takes nothing here\.[\s\S]*No arbitrage/,
  s4_tail_fade: /Sell the outer buckets/,
};

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
  const fake = await fakePostgrest({ tables: { v_strategy_board: BOARD }, port: PORT_DB });
  const env = { ...process.env, NEXT_PUBLIC_SUPABASE_URL: fake.url, NEXT_PUBLIC_SUPABASE_ANON_KEY: 'sb_publishable_render_test',
                SUPABASE_SERVICE_KEY: 'service-key-for-the-fake', NEXT_TELEMETRY_DISABLED: '1' };
  delete env.OPERATOR_SIGN_IN;
  if (!process.env.SKIP_BUILD) execFileSync(path.join(WEB, 'node_modules', '.bin', 'next'), ['build'], { cwd: WEB, env, stdio: 'inherit' });
  const server = spawn(path.join(WEB, 'node_modules', '.bin', 'next'), ['start', '-p', String(PORT_WEB), '-H', '127.0.0.1'],
                       { cwd: WEB, env, stdio: 'ignore' });
  const browser = await playwright.chromium.launch();
  try {
    await waitFor(`http://127.0.0.1:${PORT_WEB}/strategies`);
    const page = await browser.newPage({ viewport: { width: 1280, height: 1400 } });
    const errors = [];
    page.on('pageerror', (e) => errors.push(String(e)));
    await page.goto(`http://127.0.0.1:${PORT_WEB}/strategies`);
    const ids = BOARD.map((r) => r.strategy_id);
    for (const id of ids) {
      const card = page.locator(`[data-strategy="${id}"]`);
      await card.waitFor({ timeout: 30000 });
      const summary = await card.locator('p').first().innerText();
      assert.notEqual(summary.trim(), '—', `${id} has no one-line summary`);
      await card.getByRole('button', { name: 'how it works' }).click();
      await card.getByRole('heading', { name: 'How it works' }).waitFor({ timeout: 10000 });
      const text = await card.innerText();
      assert.ok(!text.includes('No walkthrough is written'), `${id}: no walkthrough`);
      if (MUST[id]) assert.match(text, MUST[id], `${id}: ${text.slice(0, 600)}`);
      if (id === 's12_no_model' || id === 's12_no') {
        await card.screenshot({ path: path.join(OUT, `strategies-${id}.png`) });
      }
      await card.getByRole('button', { name: 'hide' }).click();
    }
    assert.deepEqual(errors, [], 'the page threw');
    console.log(`PASS: render - all ${ids.length} strategies (13 active, one retired) open a walkthrough with a summary; the engine panels show what holdings_solver buys on the worked market; screenshots in ${OUT}`);
  } finally {
    await browser.close();
    server.kill();
    fake.close();
  }
})().catch((e) => { console.error(e); process.exit(1); });
