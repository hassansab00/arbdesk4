// The engine's sizing arithmetic in the browser (web/lib/kelly.ts) gives what
// scripts/holdings_solver.py gives on the strategy walkthroughs' worked market
// (Hassan, 9 Oct: the "how it works" panels for S10, S11 and S12). The
// expected numbers are holdings_solver's own on that market;
// tests/test_the_strategy_walkthrough_is_arithmetic.py asserts the Python side
// still gives them. Run with: npm run test:routes
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { effectiveCost, singleBucketGrowth, solveKelly } =
  require(path.join(__dirname, '..', '.route-test', 'lib', 'kelly.js'));

// The worked market, parsed out of the component as the Python test parses it.
const src = fs.readFileSync(path.join(__dirname, '..', 'components', 'StrategyExplainer.tsx'), 'utf-8');
const block = src.split('export const MARKET: Bucket[] = [')[1].split('];')[0];
const market = [...block.matchAll(/label:\s*"([^"]+)".*?ask:\s*([\d.]+).*?prob:\s*([\d.]+)/g)]
  .map((m) => ({ label: m[1], ask: Number(m[2]), prob: Number(m[3]) }));
assert.equal(market.length, 11);
const probs = market.map((b) => b.prob);
const near = (a, b, tol, what) => assert.ok(Math.abs(a - b) <= tol, `${what}: ${a} against ${b}`);

// allocator.effective_cost and holdings_solver.single_bucket_growth
near(effectiveCost(0.3), 0.3105, 1e-12, 'the fee-inclusive cost of 30c');
assert.equal(effectiveCost(0), null);
assert.equal(effectiveCost(1), null);
assert.equal(singleBucketGrowth(0.3, 0.3), 0, 'a bucket at its probability grows nothing');
near(singleBucketGrowth(0.21, 0.15), 0.010035259299404936, 1e-12, 'S10 growth on 31–32');

// holdings_solver.solve(allow=YES) over the buckets the platform trades (7c and up)
const yes = solveKelly(probs, market.map((b) => (b.ask >= 0.07 ? b.ask : null)), 'YES');
const held = (r) => Object.fromEntries(r.weights.flatMap((w, i) => (w > 1e-3 ? [[market[i].label, w]] : [])));
const y = held(yes);
assert.deepEqual(Object.keys(y), ['30–31', '31–32'], 'S11 takes the two buckets the horse race takes');
near(y['30–31'], 0.014616647127784321, 1e-6, 'S11 30–31');
near(y['31–32'], 0.06627432590855806, 1e-6, 'S11 31–32');
near(yes.cash, 0.9191090269636576, 1e-6, 'S11 cash');

// holdings_solver.solve(allow=NO), NO at 1 - the ask
const no = solveKelly(probs, market.map((b) => 1 - b.ask), 'NO');
const n = held(no);
assert.deepEqual(Object.keys(n), ['32–33', '33–34', '34–35', '≥35'], 'S12 sells the four buckets the solver sells');
near(n['32–33'], 0.1693, 1e-4, 'S12 32–33');
near(n['≥35'], 0.4396, 1e-4, 'S12 ≥35');
near(no.growth, 0.02407555097182852, 1e-9, 'S12 growth');

console.log('PASS: kelly: effective cost, single-bucket growth and Cover\'s solve give holdings_solver\'s answers on the worked market (S11 30–31 and 31–32; S12 NO on 32–33 up)');
