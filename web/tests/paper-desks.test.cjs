// The paper desks, counted (WXPredict build, wave P, steps P.3 and P.4).
//
// Runs lib/paperDesks.ts and the REAL /api/paper-desk route, compiled by
// tests/tsconfig.routes.json into .route-test/. The route reads through its
// own supabase-js client against tests/fakePostgrest.cjs over real HTTP, so
// the filters, ordering and paging it sends are the ones production gets.
//   npm run test:routes
const assert = require('node:assert/strict');
const path = require('node:path');
const { fakePostgrest } = require('./fakePostgrest.cjs');

const OUT = path.join(__dirname, '..', '.route-test');
const lib = require(path.join(OUT, 'lib', 'paperDesks.js'));
const route = require(path.join(OUT, 'app', 'api', 'paper-desk', 'route.js'));

const NOW = Date.parse('2026-10-06T07:00:00Z');
const hoursAgo = (h) => new Date(Date.now() - h * 3600e3).toISOString();
const SAME = '2026-09-24T20:30:32.309906+00:00';     // nine desks shared one created_at

// A roster shaped like 6 Oct's: the Portfolio desk, switched-on shadow desks
// (only s12 has traded), and retired, archived desks holding the history.
const acct = (id, name, extra = {}) => ({
  account_id: id, name, mode: 'automatic', entries_paused: false, status: 'active',
  archived_at: null, retired_at: null, strategy_id: null, access_mode: 'single_desk', owner_id: null,
  created_at: SAME, cash: 1000, reserved_cash: 0, kind: 'shadow',
  policy: { cities: ['ALL'], strategies: [] }, policy_version: 1, ...extra,
});
const ACCOUNTS = [
  acct('a-port', 'Portfolio', { mode: 'manual', entries_paused: true, status: 'suspended', kind: 'portfolio' }),
  acct('a-s8', 'Shadow: s8_two_bucket_cover', { strategy_id: 's8_two_bucket_cover', status: 'retired',
       archived_at: '2026-10-05T21:19:09Z', retired_at: '2026-10-05T21:19:09Z' }),
  acct('a-s2', 'Shadow: s2_combination_arb', { strategy_id: 's2_combination_arb' }),
  acct('a-s10', 'Shadow: s10_winner', { strategy_id: 's10_winner' }),
  acct('a-s11', 'Shadow: s11_lock', { strategy_id: 's11_lock' }),
  acct('a-s12', 'Shadow: s12_no', { strategy_id: 's12_no' }),
  acct('a-s1', 'Shadow: s1_buy_low_sell_signal', { strategy_id: 's1_buy_low_sell_signal', status: 'retired',
       archived_at: '2026-10-05T21:19:09Z', retired_at: '2026-10-05T21:19:09Z' }),
  acct('a-wide', 'Wide edge, all US', { status: 'retired', archived_at: '2026-09-23T15:38:59Z',
       retired_at: '2026-09-23T15:38:59Z', kind: null }),
];
const TRADES = [
  { trade_id: 't1', account_id: 'a-s12', opened_at: '2026-10-02T12:00:00Z', closed_at: '2026-10-03T12:00:00Z', net_pnl: '-0.00366' },
  { trade_id: 't2', account_id: 'a-s1', opened_at: '2026-09-25T10:00:00Z', closed_at: '2026-09-26T10:00:00Z', net_pnl: '1.5' },
  { trade_id: 't3', account_id: 'a-s1', opened_at: '2026-09-27T10:00:00Z', closed_at: '2026-09-28T10:00:00Z', net_pnl: '-0.5' },
  { trade_id: 't4', account_id: 'a-wide', opened_at: '2026-09-20T10:00:00Z', closed_at: null, net_pnl: null },
  { trade_id: 't5', account_id: null, opened_at: '2026-09-10T10:00:00Z', closed_at: '2026-09-11T10:00:00Z', net_pnl: '9' },
];
const POSITIONS = [
  { account_id: 'a-s12', band_id: 'b1', side: 'NO', shares: 0 },
  { account_id: 'a-wide', band_id: 'b2', side: 'YES', shares: 12 },
];
const STRATEGIES = [
  { strategy_id: 's1_buy_low_sell_signal', name: 'S1', enabled: false },
  { strategy_id: 's2_combination_arb', name: 'S2', enabled: true },
  { strategy_id: 's8_two_bucket_cover', name: 'S8', enabled: false },
  { strategy_id: 's10_winner', name: 'S10', enabled: true },
  { strategy_id: 's11_lock', name: 'S11', enabled: true },
  { strategy_id: 's12_no', name: 'S12', enabled: true },
];
// Last 24 h as read live on 6 Oct 07:05Z, with s2 raised past one page.
let id = 0;
const dec = (strategy_id, reason_code, n, action = 'NONE', h = 3) =>
  Array.from({ length: n }, () => ({ decision_id: ++id, strategy_id, action, reason_code, decided_at: hoursAgo(h) }));
const DECISIONS = [
  ...dec('s2_combination_arb', 'no_signal', 1250),
  ...dec('s10_winner', 'own_rule_none', 204), ...dec('s10_winner', 'no_ladder', 48), ...dec('s10_winner', 'no_trade_band', 1),
  ...dec('s11_lock', 'no_trade_band', 253),
  ...dec('s12_no', 'no_trade_band', 234), ...dec('s12_no', 'nothing_tradeable', 16), ...dec('s12_no', 'against_market', 3),
  ...dec('s12_no', 'no_trade_band', 40, 'NONE', 30),      // older than 24 h: not counted
];
// The repository archive (web/public/paper-trades): t2 again, as exported (the
// archive wins), and t6, a trade already pruned from Postgres 30 days after
// export (paper_trade_log.yml) - it must still count (Codex on #318).
const ARCHIVED = [
  { trade_id: 't2', account_id: 'a-s1', opened_at: '2026-09-25T10:00:00Z', closed_at: '2026-09-26T10:00:00Z', net_pnl: '2.0' },
  { trade_id: 't6', account_id: 'a-s1', opened_at: '2026-09-20T10:00:00Z', closed_at: '2026-09-21T10:00:00Z', net_pnl: '-1' },
];
const TABLES = { paper_accounts: ACCOUNTS, paper_trades: TRADES, paper_positions: POSITIONS,
                 strategies: STRATEGIES, decisions: DECISIONS };

const get = async (query) => {
  const res = await route.GET(new Request(`https://desk.example/api/paper-desk?${query}`));
  return { status: res.status, body: await res.json() };
};

(async () => {
  // ---------------------------------------------------------------------
  // 1. lib/paperDesks.ts, pure.
  // ---------------------------------------------------------------------
  const acts = lib.deskActivity(ACCOUNTS, TRADES, POSITIONS, STRATEGIES,
                                DECISIONS.filter((d) => Date.parse(d.decided_at) > Date.now() - 24 * 3600e3));
  const by = Object.fromEntries(acts.map((a) => [a.account_id, a]));
  assert.equal(acts.length, ACCOUNTS.length, 'every desk, archived and retired included');
  assert.deepEqual([by['a-s1'].trade_count, by['a-s1'].closed_count, by['a-s1'].net_pnl], [2, 2, 1]);
  assert.equal(by['a-s1'].last_trade_at, '2026-09-27T10:00:00Z');
  assert.equal(by['a-s12'].net_pnl, -0.00366);
  assert.deepEqual([by['a-wide'].trade_count, by['a-wide'].closed_count, by['a-wide'].open_positions], [1, 0, 1]);
  assert.equal(by['a-s12'].open_positions, 0, 'a position with 0 shares is not open');
  assert.equal(acts.reduce((t, a) => t + a.trade_count, 0), 4, "a trade with no desk is on nobody's count");
  assert.deepEqual([by['a-s1'].strategy_state, by['a-s1'].strategy_state_since], ['retired', '2026-10-05T21:19:09Z']);
  assert.equal(by['a-s2'].strategy_state, 'on');
  assert.equal(by['a-port'].strategy_state, 'none');
  assert.equal(by['a-port'].decisions_24h, null, 'a desk with no strategy has no decisions');
  assert.deepEqual(by['a-s12'].decisions_24h, { n: 253, buys: 0, reasons: [
    { reason_code: 'no_trade_band', n: 234 }, { reason_code: 'nothing_tradeable', n: 16 },
    { reason_code: 'against_market', n: 3 }] });
  // Every trade once: the archive wins on an id both hold; each side's own rows stay.
  const merged = lib.mergeTradeRows(ARCHIVED, TRADES);
  assert.equal(merged.length, TRADES.length + 1);
  assert.equal(merged.find((t) => t.trade_id === 't2').net_pnl, '2.0', 'the archive wins');
  assert.ok(merged.some((t) => t.trade_id === 't6'), 'a trade pruned from Postgres still counts');
  assert.equal(lib.strategyState({ ...ACCOUNTS[2], strategy_id: 's1_buy_low_sell_signal' },
    new Map(STRATEGIES.map((s) => [s.strategy_id, s]))).state, 'off', 'a desk whose strategy is switched off');

  // pickDesk: the most recent trade, then trades, then name. 6 Oct's roster
  // opens on s12, the only desk with a trade - not the first live desk.
  const live = ACCOUNTS.filter((a) => !a.archived_at).map((a) => ({ ...a, ...lib.deskActivity([a], TRADES, POSITIONS, STRATEGIES, [])[0] }));
  assert.equal(lib.pickDesk(live), 'a-s12');
  const twin = (n, name, at, trades) => ({ account_id: n, name, mode: 'automatic', entries_paused: false, last_trade_at: at, trade_count: trades });
  assert.equal(lib.pickDesk([twin('x', 'A', '2026-10-01T00:00:00Z', 5), twin('y', 'B', '2026-10-02T00:00:00Z', 1)]), 'y',
    'newest trade first, ahead of more trades and an earlier name');
  assert.equal(lib.pickDesk([twin('x', 'B', '2026-10-01T00:00:00Z', 1), twin('y', 'A', '2026-10-01T00:00:00Z', 5)]), 'y', 'then trade count');
  assert.equal(lib.pickDesk([twin('x', 'B', null, 0), twin('y', 'A', null, 0)]), 'y', 'then name, so the choice is fixed');
  // Without counts (the edge gateway's plain rows): a desk that can act, then the name.
  const plain = ACCOUNTS.filter((a) => !a.archived_at);
  assert.equal(lib.pickDesk(plain), 'a-s10', 'live first, then name');
  assert.equal(lib.pickDesk([]), null);

  // deskState: every case, the old four and P.4's two.
  const desk = (over = {}, activity) => ({ account_id: 'd', name: 'D', mode: 'automatic', entries_paused: false,
    cash: 1000, reserved_cash: 0, policy: { strategies: ['s12_no'], cities: ['ALL'] }, activity, ...over });
  const st = (d, avail = 1000) => lib.deskState(d, avail, NOW);
  assert.equal(st(desk({ mode: 'manual' })).key, 'MANUAL');
  assert.equal(st(desk({ entries_paused: true })).key, 'PAUSED');
  assert.match(st(desk({ policy: { cities: ['ALL'] } })).line, /^No strategies chosen/);
  assert.match(st(desk({ policy: { strategies: ['s12_no'] } })).line, /^No cities chosen/);
  assert.match(st(desk(), 0).line, /^No available cash/);
  assert.match(st(desk()).line, /^Running on its own/, 'no counts: the line it always had');
  assert.match(st(desk({ mode: 'assisted' })).line, /^Running\. Strategies propose/);
  const s12 = { ...by['a-s12'] };
  assert.deepEqual(st(desk({}, s12)), { key: 'ACTIVE', dot: 'bg-good', text: 'text-good',
    line: 'Running. 253 decisions in the last 24 h, 0 buys. Most often: no purchase cleared the no-trade band (234).' });
  assert.match(st(desk({}, { ...s12, decisions_24h: { n: 9, buys: 2, reasons: [] } })).line, /^Running on its own/,
    'a strategy that bought something is not "nothing to buy"');
  assert.match(st(desk({}, { ...s12, last_trade_at: new Date(NOW - 3600e3).toISOString() })).line, /^Running on its own/,
    'a desk that traded in the last 24 h is not "nothing to buy"');
  assert.equal(st(desk({}, { ...s12, decisions_24h: { n: 0, buys: 0, reasons: [] } })).line,
    'Running. 0 decisions in the last 24 h, 0 buys.');
  const retired = st(desk({ mode: 'manual' }, by['a-s1']));
  assert.deepEqual([retired.key, retired.line], ['RETIRED', 'Strategy retired on 5 Oct 2026: this desk never trades again.'],
    'retired outranks every other state');
  const off = st(desk({}, { ...s12, strategy_state: 'off' }));
  assert.equal(off.key, 'RETIRED');
  assert.match(off.line, /^Strategy switched off/);
  for (const d of [by['a-s1'], { ...s12, strategy_state: 'off' }]) {
    assert.ok(!/Running/.test(st(desk({}, d)).line), 'a retired or switched-off strategy is never "Running"');
  }

  // Each reason code has its words; an unknown one is shown as the code.
  for (const code of ['own_rule_none', 'no_ladder', 'no_trade_band', 'nothing_tradeable', 'against_market', 'no_signal']) {
    assert.ok(!lib.reasonWords(code).startsWith('reason code'), `${code} has no plain words`);
  }
  assert.equal(lib.reasonWords('lock_breaks'), 'reason code lock_breaks');
  // no_ladder means something else for S10 (P.5): its model never prices the evening before.
  assert.match(lib.reasonWords('no_ladder', 's10_winner'), /^no S10 ladder .*never the evening before/);
  assert.equal(lib.reasonWords('no_ladder', 's12_no'), lib.reasonWords('no_ladder'));
  assert.equal(lib.reasonWords('own_rule_none', 's10_lock'), lib.reasonWords('own_rule_none'));
  {
    const ladderless = st(desk({}, { ...s12, strategy_id: 's10_lock', decisions_24h: { n: 48, buys: 0, reasons: [{ reason_code: 'no_ladder', n: 48 }] } }));
    assert.match(ladderless.line, /Most often: no S10 ladder for that checkpoint/, 'the desk line uses S10\'s own words');
  }
  assert.equal(lib.strategyPhrase(by['a-s1']), 'retired 5 Oct 2026');
  assert.equal(lib.strategyPhrase(by['a-port']), 'no strategy · suspended');

  // ---------------------------------------------------------------------
  // 2. The route, against the fake, in both sign-in modes.
  // ---------------------------------------------------------------------
  for (const k of Object.keys(process.env)) if (/SUPABASE/i.test(k)) delete process.env[k];
  const fs = require('node:fs');
  const os = require('node:os');
  const archiveDir = (index, lines) => {
    const d = fs.mkdtempSync(path.join(os.tmpdir(), 'paper-archive-'));
    if (index !== undefined) fs.writeFileSync(path.join(d, 'index.json'), index);
    if (lines) fs.writeFileSync(path.join(d, '2026-09.jsonl'), lines.map((r) => JSON.stringify(r)).join('\n') + '\n');
    return d;
  };
  process.env.PAPER_ARCHIVE_DIR = archiveDir(JSON.stringify({ generated_at: '2026-10-06T05:36:00Z', months: ['2026-09'] }), ARCHIVED);
  const db = await fakePostgrest({ tables: TABLES });
  process.env.NEXT_PUBLIC_SUPABASE_URL = db.url;
  process.env.SUPABASE_SERVICE_KEY = 'service-key-for-the-fake';
  for (const mode of [undefined, 'required']) {
    if (mode) process.env.OPERATOR_SIGN_IN = mode; else delete process.env.OPERATOR_SIGN_IN;
    db.requests.length = 0;
    const { status, body } = await get('resource=accounts');
    assert.equal(status, 200, `sign-in ${mode ?? 'off'}: reading the desks needs no session`);
    assert.equal(body.activity_error, null);
    // The list is what it always was: live desks, oldest first.
    assert.deepEqual(body.data.map((a) => a.account_id), ['a-port', 'a-s2', 'a-s10', 'a-s11', 'a-s12']);
    const s12r = body.data.find((a) => a.account_id === 'a-s12');
    assert.deepEqual([s12r.trade_count, s12r.open_positions, s12r.last_trade_at], [1, 0, '2026-10-02T12:00:00Z']);
    assert.equal(s12r.activity.decisions_24h.n, 253, 'decisions older than 24 h are not counted');
    // Every desk, for All desks, with s2's 1,250 decisions read over two pages.
    assert.equal(body.desks.length, ACCOUNTS.length);
    const s2 = body.desks.find((d) => d.account_id === 'a-s2');
    assert.deepEqual(s2.decisions_24h, { n: 1250, buys: 0, reasons: [{ reason_code: 'no_signal', n: 1250 }] });
    const pages = db.requests.filter((r) => r.path === '/rest/v1/decisions').map((r) => r.params.get('offset'));
    assert.deepEqual(pages, ['0', '1000', '2000'], 'decisions are read a page at a time until a short page');
    // The archive merged in: t6 counts, t2 is counted once at its archived net.
    assert.equal(body.desks.reduce((t, d) => t + d.trade_count, 0), 5);
    const s1d = body.desks.find((d) => d.account_id === 'a-s1');
    assert.deepEqual([s1d.trade_count, s1d.net_pnl, s1d.last_trade_at, s1d.strategy_state], [3, 0.5, '2026-09-27T10:00:00Z', 'retired']);
    assert.deepEqual(body.archive, { trades: 2, generated_at: '2026-10-06T05:36:00Z', months: ['2026-09'] });
    assert.equal(body.archive_error, null);
  }
  delete process.env.OPERATOR_SIGN_IN;
  // No archive yet (nothing exported) is not an error: Postgres alone.
  process.env.PAPER_ARCHIVE_DIR = archiveDir(undefined);
  let r = (await get('resource=accounts')).body;
  assert.equal(r.archive_error, null);
  assert.equal(r.desks.reduce((t, d) => t + d.trade_count, 0), 4);
  // An unreadable archive is said, never hidden, and never empties anything.
  process.env.PAPER_ARCHIVE_DIR = archiveDir('{not json');
  r = (await get('resource=accounts')).body;
  assert.match(r.archive_error, /index could not be read/);
  assert.equal(r.desks.reduce((t, d) => t + d.trade_count, 0), 4, 'Postgres counts still come back');
  assert.deepEqual(r.data.map((a) => a.account_id), ['a-port', 'a-s2', 'a-s10', 'a-s11', 'a-s12']);
  process.env.PAPER_ARCHIVE_DIR = archiveDir(JSON.stringify({ months: ['2026-09'] }), ARCHIVED);
  await db.close();

  // A failing count read never empties the list (P.3): the desks come back
  // exactly as paper_accounts has them, without counts, and the error says why.
  for (const broken of ['paper_trades', 'decisions', 'strategies']) {
    const bad = await fakePostgrest({ tables: TABLES, fail: new Set([broken]) });
    process.env.NEXT_PUBLIC_SUPABASE_URL = bad.url;
    const { status, body } = await get('resource=accounts');
    assert.equal(status, 200, `${broken} failing must not fail the desk list`);
    assert.deepEqual(body.data.map((a) => a.account_id), ['a-port', 'a-s2', 'a-s10', 'a-s11', 'a-s12']);
    assert.ok(body.data.every((a) => a.trade_count === undefined && a.activity === undefined), 'no half counts');
    assert.equal(body.desks, null);
    assert.match(body.activity_error, new RegExp(`${broken} failed`));
    await bad.close();
  }
  // The list itself failing is still the list's error, as before.
  const noList = await fakePostgrest({ tables: TABLES, fail: new Set(['paper_accounts']) });
  process.env.NEXT_PUBLIC_SUPABASE_URL = noList.url;
  assert.match((await get('resource=accounts')).body.error.message, /paper_accounts failed/);
  await noList.close();

  // No service key: the edge gateway answers, as before, and sends no counts.
  delete process.env.SUPABASE_SERVICE_KEY;
  const gw = await fakePostgrest({ edge: (b) => ({ data: b.resource === 'accounts' ? [ACCOUNTS[2]] : [], error: null }) });
  process.env.NEXT_PUBLIC_SUPABASE_URL = gw.url;
  process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY = 'anon-for-the-fake';
  const viaEdge = await get('resource=accounts');
  assert.deepEqual(viaEdge.body, { data: [ACCOUNTS[2]], error: null });
  await gw.close();

  console.log('PASS: paper desks - per-desk trades (the repository archive merged with Postgres, the archive winning; an unreadable archive said, a missing one empty), net P&L, open positions, strategy state and 24 h decisions from the tables (paged past 1,000 rows); the page opens on the newest trade, then trade count, then name; deskState covers every case including retired, switched off and running with nothing to buy; a failing count read leaves the desk list whole; both sign-in modes; the edge gateway path unchanged');
})().catch((e) => { console.error(e); process.exit(1); });
