// RENDER TEST: the paper page on a local production build (WXPredict build,
// steps P.3 and P.4). Not part of CI: it builds the app and drives Chromium.
//
//   node tests/render/paper-page.render.cjs        (from web/)
//
// This sandbox cannot reach the site or the database, so the page is served
// by `next start` and every Supabase request - the browser's anon reads AND
// the /api/paper-desk route's service-key reads - goes to tests/fakePostgrest
// on 127.0.0.1, serving rows recorded from production:
//
//   fixtures/paper-2026-10-06.json   read live through the Supabase tool on
//                                    6 Oct 07:16Z: every desk, strategy,
//                                    active city, position, s12's orders,
//                                    activity and plan, its books check, the
//                                    pipeline's newest runs, and the last
//                                    24 h of decisions as counts per
//                                    (strategy, action, reason)
//   public/paper-trades/*.jsonl      the trade archive the page already
//                                    loads; 198 trades, identical to
//                                    paper_trades on (trade_id, net_pnl,
//                                    account_id) - md5 984bbfd1... both sides
//
// It asserts what P.3 and P.4 promise, then saves two screenshots.
const assert = require('node:assert/strict');
const { execFileSync, spawn } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const { fakePostgrest } = require('../fakePostgrest.cjs');

const WEB = path.join(__dirname, '..', '..');
const OUT = path.join(__dirname, 'out');
const FIX = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures', 'paper-2026-10-06.json'), 'utf8'));
const PORT_DB = 54329, PORT_WEB = 3107;
const SQL_NET = -800.63922;            // sum(net_pnl) of closed paper_trades, live 6 Oct 07:15Z
const playwright = require(process.env.PLAYWRIGHT_MODULE || '/opt/node-tools/node_modules/playwright');

function tables() {
  const trades = fs.readdirSync(path.join(WEB, 'public', 'paper-trades')).filter((f) => f.endsWith('.jsonl')).sort()
    .flatMap((f) => fs.readFileSync(path.join(WEB, 'public', 'paper-trades', f), 'utf8').split('\n').filter(Boolean).map(JSON.parse));
  // The recorded counts, as rows the route reads back (decided an hour ago).
  let id = 0;
  const at = new Date(Date.now() - 3600e3).toISOString();
  const decisions = FIX.decisions_24h.flatMap((g) => Array.from({ length: g.n },
    () => ({ decision_id: ++id, strategy_id: g.strategy_id, action: g.action, reason_code: g.reason_code, decided_at: at })));
  return {
    paper_accounts: FIX.paper_accounts, strategies: FIX.strategies, cities: FIX.cities.map((c) => ({ ...c, status: 'active' })),
    v_paper_desks: FIX.v_paper_desks, v_paper_desk_integrity: FIX.v_paper_desk_integrity,
    paper_orders: FIX.paper_orders, paper_activity: FIX.paper_activity, paper_trade_plans: FIX.paper_trade_plans,
    paper_positions: FIX.paper_positions, ingest_log: FIX.ingest_log, paper_trades: trades, decisions,
  };
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
  const T = tables();
  // A fixed port: NEXT_PUBLIC_SUPABASE_URL is inlined at build time.
  const fake = await fakePostgrest({ tables: T, port: PORT_DB });
  const dbUrl = fake.url;
  const env = { ...process.env, NEXT_PUBLIC_SUPABASE_URL: dbUrl, NEXT_PUBLIC_SUPABASE_ANON_KEY: 'sb_publishable_render_test',
                SUPABASE_SERVICE_KEY: 'service-key-for-the-fake', NEXT_TELEMETRY_DISABLED: '1' };
  delete env.OPERATOR_SIGN_IN;
  if (!process.env.SKIP_BUILD) execFileSync(path.join(WEB, 'node_modules', '.bin', 'next'), ['build'], { cwd: WEB, env, stdio: 'inherit' });
  const server = spawn(path.join(WEB, 'node_modules', '.bin', 'next'), ['start', '-p', String(PORT_WEB), '-H', '127.0.0.1'],
                       { cwd: WEB, env, stdio: 'ignore' });
  const browser = await playwright.chromium.launch();
  try {
    await waitFor(`http://127.0.0.1:${PORT_WEB}/paper-trades`);
    const page = await browser.newPage({ viewport: { width: 1280, height: 1600 } });
    const errors = [];
    page.on('pageerror', (e) => errors.push(String(e)));
    await page.goto(`http://127.0.0.1:${PORT_WEB}/paper-trades`);

    // 1. It opens on the desk with the most recent trade: s12_no (29 Sep).
    const s12 = FIX.paper_accounts.find((a) => a.strategy_id === 's12_no');
    const switcher = page.locator('select[aria-label="Paper account"]');
    await page.waitForFunction((id) => document.querySelector('select[aria-label="Paper account"]')?.value === id,
                               s12.account_id, { timeout: 30000 });
    // ...and says it is running with nothing to buy, from the recorded decisions.
    const mine = FIX.decisions_24h.filter((g) => g.strategy_id === 's12_no');
    const n = mine.reduce((t, g) => t + g.n, 0);
    const top = mine.filter((g) => g.action !== 'BUY').sort((a, b) => b.n - a.n || a.reason_code.localeCompare(b.reason_code))[0];
    assert.equal(mine.filter((g) => g.action === 'BUY').length, 0, 'the fixture has s12 buying nothing');
    const words = require(path.join(WEB, '.route-test', 'lib', 'paperDesks.js')).reasonWords(top.reason_code);
    const line = `Running. ${n.toLocaleString('en-US')} decisions in the last 24 h, 0 buys. Most often: ${words} (${top.n.toLocaleString('en-US')}).`;
    await page.getByText(line).waitFor({ timeout: 30000 });
    await page.screenshot({ path: path.join(OUT, 'p3-opens-on-s12.png'), fullPage: false });

    // 2. All desks: every desk, 198 trades, the SQL net P&L, retired desks marked.
    await switcher.selectOption('all');
    await page.getByRole('heading', { name: 'All desks', exact: true }).waitFor();
    const summary = await page.locator('h2:has-text("All desks") + span').innerText();
    assert.match(summary, /^20 desks · 198 trades · net \$-800\.64 on closed trades$/, summary);
    const tile = page.locator('div:has(> div:text-is("Net P&L")) > div.font-mono').first();
    assert.equal(await tile.innerText(), `$${SQL_NET.toLocaleString(undefined, { maximumFractionDigits: 2, minimumFractionDigits: 2 })}`,
                 'the trade history tile equals the SQL sum');
    const s1 = FIX.paper_accounts.find((a) => a.strategy_id === 's1_buy_low_sell_signal');
    const s1row = page.locator(`tr[data-desk="${s1.account_id}"]`);
    assert.match(await s1row.innerText(), /retired[\s\S]*retired 5 Oct 2026[\s\S]*43[\s\S]*\$-197\.96/i, await s1row.innerText());
    // Next.js adds an empty role="alert" route announcer to every page; only a
    // banner with words in it is the page's.
    const alerts = (await page.locator('[role="alert"]').allInnerTexts()).filter((t) => t.trim());
    assert.deepEqual(alerts, [], `All desks raised an error banner: ${alerts.join(' | ')}`);
    await page.screenshot({ path: path.join(OUT, 'p3-all-desks.png'), fullPage: true });
    assert.deepEqual(errors, [], 'the page threw');
    console.log(`PASS: render - opens on s12 ("${line}"); All desks: ${summary}; Net P&L tile = SQL ${SQL_NET}; s1 marked retired 5 Oct 2026; no error banner; screenshots in ${OUT}`);
  } finally {
    await browser.close();
    server.kill();
    fake.close();
  }
})().catch((e) => { console.error(e); process.exit(1); });
