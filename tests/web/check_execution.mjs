// Assertions for web/lib/execution.ts - what you can ACTUALLY place, and what
// it will ACTUALLY return.
//
// This module exists because every position figure on the desk used to be
// computed as if any quantity could be bought at the quoted price. Both halves
// were false: the venue has a floor, and the book has a depth. The tests below
// are the specific wrong answers that were on screen before it existed.
import assert from "node:assert/strict";
import * as E from "../../web/lib/execution.ts";

const out = [];
function t(name, fn) {
  try { fn(); out.push(["ok", name]); }
  catch (e) { out.push(["FAIL", name + " :: " + e.message]); }
}

const L = E.DEFAULT_LIMITS;
const near = (a, b, eps = 0.02) => assert.ok(Math.abs(a - b) <= eps, `${a} != ${b} (+-${eps})`);

// ---- the venue's floor ---------------------------------------------------
t("an order under the minimum is REJECTED, not filled small", () => {
  const f = E.fill(0.60, [[0.30, 1e6]], true, 0.4, L);
  assert.equal(f.shares, 0);
  assert.equal(f.spent, 0);
  assert.ok(f.problems.includes("below_minimum"));
  assert.match(f.note, /minimum/);
});

t("exactly the minimum is accepted", () => {
  const f = E.fill(1.00, [[0.30, 1e6]], true, 0.4, L);
  assert.ok(f.shares > 0);
  assert.ok(!f.problems.includes("below_minimum"));
});

t("a leg that cannot be made placeable snaps to null, not to zero", () => {
  assert.equal(E.snapLeg(0.42, L), null);
  assert.equal(E.snapLeg(12.345, L), 12.35);
});

// ---- the book's depth ----------------------------------------------------
t("the second hundred dollars fills worse than the first", () => {
  // 100 shares at 30c, then the rest at 40c.
  const levels = [[0.30, 100], [0.40, 1e6]];
  const small = E.fill(30, levels, true, null, L);
  const big = E.fill(200, levels, true, null, L);
  assert.ok(small.avgPrice < big.avgPrice, "walking deeper must raise the average price");
  near(small.avgPrice, 0.30, 0.005);
  assert.ok(big.slippage > 0.03, `expected real slippage, got ${big.slippage}`);
});

t("a stake larger than the whole book fills PARTIALLY and says so", () => {
  const f = E.fill(500, [[0.25, 100]], true, null, L);   // 100 shares = ~$25
  assert.ok(f.problems.includes("partially_filled"));
  assert.ok(f.spent < 30, `only the book should fill, got $${f.spent}`);
  near(f.shares, 100, 0.02);
  assert.match(f.note, /book runs out/);
});

t("profit and EV are computed on what FILLED, not what was requested", () => {
  // The bug: a $500 request against a $25 book used to quote the profit of
  // $500 of shares. Here 100 shares cost ~$25 and pay $100.
  const f = E.fill(500, [[0.25, 100]], true, 0.5, L);
  near(f.payout, 100, 0.05);
  near(f.profit, 100 - f.spent, 0.01);
  near(f.ev, 0.5 * f.profit - 0.5 * f.spent, 0.01);
  assert.ok(f.profit < 80, "profit must reflect the fill, not the request");
});

t("no book at all is not infinite depth at the mid", () => {
  const { levels, known } = E.ladderFor(null, null, null, L);
  assert.equal(levels.length, 0);
  assert.equal(known, false);
  const f = E.fill(100, levels, known, 0.5, L);
  assert.ok(f.problems.includes("no_book"));
  assert.equal(f.shares, 0);
});

t("a quote with a depth total gives ONE capped level, flagged as reconstructed", () => {
  const { levels, known } = E.ladderFor(null, 0.20, 40, L);   // $40 of depth at 20c
  assert.equal(known, false);
  assert.equal(levels.length, 1);
  near(levels[0][1], 200, 1);                                  // 40 / 0.20
  const f = E.fill(100, levels, known, null, L);
  assert.ok(f.problems.includes("no_book"));
  assert.match(f.note, /no stored ladder/);
});

t("the default takes the whole quoted level - no invisible haircut", () => {
  const { levels } = E.ladderFor([[0.30, 100]], null, null, E.DEFAULT_LIMITS);
  near(levels[0][1], 100, 0.001);
});

t("max_book_fraction still applies when an operator sets one", () => {
  const half = { ...E.DEFAULT_LIMITS, maxBookFraction: 0.5 };
  const { levels } = E.ladderFor([[0.30, 100]], null, null, half);
  near(levels[0][1], 50, 0.001);
});

// ---- the fee -------------------------------------------------------------
t("the fee comes OUT of the stake, so risking $100 risks exactly $100", () => {
  const f = E.fill(100, [[0.40, 1e6]], true, null, L);
  near(f.spent, 100, 0.02);
  assert.ok(f.fee > 0, "a taker fee must be charged");
  // shares x price + fee == spent
  near(f.shares * f.avgPrice + f.fee, f.spent, 0.05);
});

t("the fee follows p(1-p) - a 90c favourite costs less to take than a coin flip", () => {
  const cheap = E.fill(100, [[0.90, 1e6]], true, null, L);
  const flip = E.fill(100, [[0.50, 1e6]], true, null, L);
  assert.ok(cheap.fee / cheap.shares < flip.fee / flip.shares);
});

// ---- the biggest clean ticket -------------------------------------------
t("maxCleanStake is what fits in the book, to the cent", () => {
  const v = E.maxCleanStake([[0.25, 100], [0.30, 50]], L);
  // 100 x 0.25 x (1+0.05x0.75) + 50 x 0.30 x (1+0.05x0.70)
  near(v, 100 * 0.25 * 1.0375 + 50 * 0.30 * 1.035, 0.02);
});

t("an unbounded level means no useful cap, not a huge number", () => {
  assert.equal(E.maxCleanStake([[0.25, Infinity]], L), Infinity);
});

const failed = out.filter(([s]) => s === "FAIL");
for (const [s, n] of out) if (s === "FAIL") console.log("  FAIL", n);
console.log(JSON.stringify({ ok: failed.length === 0, passed: out.length - failed.length, failed: failed.length }));
