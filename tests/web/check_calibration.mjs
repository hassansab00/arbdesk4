// Assertions for web/lib/calibration.ts and web/lib/opportunity.ts - the
// measured haircut, and the EV the opportunity list is now ranked on.
//
// Two wrong things were on screen before these existed. The list was ordered by
// a server-side score - edge x confidence x ln(1 + depth) - which does not know
// the stake, so a large edge on a book that could not absorb $100 outranked a
// smaller edge that filled completely. And every card showed "If right" with no
// "If wrong" beside it, next to a positive EV, which reads like a one-sided
// proposition when the losing case is what happens most of the time.
import assert from "node:assert/strict";
import * as C from "../../web/lib/calibration.ts";
import * as O from "../../web/lib/opportunity.ts";
import * as E from "../../web/lib/execution.ts";

const out = [];
function t(name, fn) {
  try { fn(); out.push(["ok", name]); }
  catch (e) { out.push(["FAIL", name + " :: " + e.message]); }
}
const near = (a, b, eps = 1e-6) =>
  assert.ok(Math.abs(a - b) <= eps, `${a} != ${b} (+-${eps})`);

/** A decile row. `applies` is derived the way the view derives it. */
function bucket(i, { n, mean, lo, hi }) {
  const adjusted = Math.min(hi, Math.max(lo, mean));
  const applies = n >= 30 && adjusted !== mean;
  return {
    bucket: i, bucket_lo: i / 10, bucket_hi: (i + 1) / 10,
    n, mean_stated: mean, realised: mean, gap: 0,
    wilson_lo: lo, wilson_hi: hi, adjusted,
    shift: applies ? adjusted - mean : 0, applies,
    note: applies ? "outside" : "inside",
  };
}

// ---- the haircut ---------------------------------------------------------
t("a stated probability inside its own interval is NOT moved", () => {
  // The live state on 2026-09-19: every decile's stated mean sits inside its
  // Wilson interval, so the measured gap is not yet actionable.
  const curve = [bucket(3, { n: 126, mean: 0.3458, lo: 0.2070, hi: 0.3617 })];
  const h = C.haircut(0.3458, curve);
  assert.equal(h.applied, false);
  near(h.p, 0.3458);
  assert.equal(h.shift, 0);
});

t("a stated probability outside its interval moves to the NEAREST BOUND", () => {
  // Not to the point estimate. The adjustment stops at the edge of what the
  // data supports - that is the whole rule, and it has no tuning constant.
  const curve = [bucket(3, { n: 400, mean: 0.3458, lo: 0.2400, hi: 0.3100 })];
  const h = C.haircut(0.3458, curve);
  assert.equal(h.applied, true);
  near(h.p, 0.3100);
  near(h.shift, 0.3100 - 0.3458);
});

t("a thin decile never moves a price however extreme it looks", () => {
  // n=2, realised 50% against a stated 87.7% - the live 80-90% bucket. The
  // sample floor AND the interval both have to be cleared.
  const curve = [bucket(8, { n: 2, mean: 0.8773, lo: 0.0945, hi: 0.9055 })];
  assert.equal(C.haircut(0.8773, curve).applied, false);
  const forced = [{ ...bucket(8, { n: 2, mean: 0.8773, lo: 0.10, hi: 0.50 }), applies: false, shift: 0 }];
  assert.equal(C.haircut(0.8773, forced).applied, false);
});

t("no curve at all is a stated fact, not a silent pass-through", () => {
  for (const curve of [null, undefined, []]) {
    const h = C.haircut(0.4, curve);
    assert.equal(h.applied, false);
    near(h.p, 0.4);
    assert.match(h.reason, /measured|settled/);
  }
});

t("a probability in a decile the curve has no row for is left alone", () => {
  const h = C.haircut(0.95, [bucket(0, { n: 500, mean: 0.03, lo: 0.01, hi: 0.02 })]);
  assert.equal(h.applied, false);
  assert.equal(h.bucket, null);
  near(h.p, 0.95);
});

t("the edge moves by exactly the same amount as the probability", () => {
  const curve = [bucket(3, { n: 400, mean: 0.3458, lo: 0.2400, hi: 0.3100 })];
  const h = C.haircut(0.3458, curve);
  near(C.haircutEdge(0.05, h), 0.05 + h.shift);
  assert.equal(C.haircutEdge(null, h), null, "a missing edge stays missing, not zero");
});

t("the adjusted probability is never 0 or 1", () => {
  const curve = [bucket(0, { n: 5000, mean: 0.02, lo: 0.0, hi: 0.0 })];
  const h = C.haircut(0.02, curve);
  assert.ok(h.p > 0 && h.p < 1, `clamped: ${h.p}`);
});

// ---- the ranked EV -------------------------------------------------------
const L = E.DEFAULT_LIMITS;
const deep = { band_id: "d", ask_levels: [[0.30, 100000]], bid_levels: null,
               ask_depth_usd: 1e6, bid_depth_usd: null, best_ask: 0.30, best_bid: null };
const thin = { band_id: "t", ask_levels: [[0.20, 40]], bid_levels: null,
               ask_depth_usd: 8, bid_depth_usd: null, best_ask: 0.20, best_bid: null };

function row(over) {
  return { side: "YES", model_prob: 0.40, edge_net_pp: 0.05,
           best_ask: 0.30, best_bid: null, fillable_usd_5c: 1000, ...over };
}

t("if wrong is the whole fill, fee included - not the requested stake", () => {
  const p = O.priceRow(row(), deep, L, 100, null);
  assert.ok(p.pos.shares > 0);
  near(p.ifWrong, -p.pos.spent, 0.005);
  assert.ok(p.ifWrong < 0, "a loss is shown as a loss");
  // and the two halves are the two outcomes of the same bet
  near(p.ifRight, p.pos.payout - p.pos.spent, 0.005);
});

t("a stake bigger than the book risks the book, not the stake", () => {
  const p = O.priceRow(row({ best_ask: 0.20, fillable_usd_5c: 8 }), thin, L, 500, null);
  assert.ok(p.pos.spent < 500, "the whole ticket cannot fill");
  near(p.ifWrong, -p.pos.spent, 0.005);
  assert.ok(Math.abs(p.ifWrong) < 500, `risked ${p.ifWrong}, which is more than the book holds`);
});

t("nothing fillable means no if-right, no if-wrong and no EV", () => {
  const p = O.priceRow(row(), undefined, L, 100, null);
  if (p.pos.shares === 0) {
    assert.equal(p.ifRight, null);
    assert.equal(p.ifWrong, null);
    assert.equal(p.ev, null);
  }
});

t("EV is p x (if right) - (1 - p) x |if wrong|, at the priced probability", () => {
  const p = O.priceRow(row(), deep, L, 100, null);
  near(p.ev, 0.40 * p.ifRight + 0.60 * p.ifWrong, 0.01);
});

t("the haircut moves the EV and the card's raw EV is kept beside it", () => {
  const curve = [bucket(4, { n: 400, mean: 0.4200, lo: 0.3000, hi: 0.3600 })];
  const p = O.priceRow(row({ model_prob: 0.42 }), deep, L, 100, curve);
  assert.equal(p.cal.applied, true);
  assert.ok(p.ev < p.evRaw, `haircut should cut EV: ${p.ev} vs ${p.evRaw}`);
  near(p.ev, 0.36 * p.ifRight + 0.64 * p.ifWrong, 0.01);
  near(p.edge, 0.05 + (0.36 - 0.42), 1e-6);
});

t("a row that cannot fill sorts BELOW one that can, whatever its edge", () => {
  // The bug this ranking replaces: the server score rewards a big edge on a
  // book that cannot absorb the stake.
  const unfillable = { ev: null, score: 999 };
  const modest = { ev: 1.5, score: 1 };
  assert.ok(O.byExpectedValue(unfillable, modest) > 0, "unfillable must sort later");
  assert.deepEqual([unfillable, modest].sort(O.byExpectedValue), [modest, unfillable]);
});

t("higher EV wins, and equal EV falls back to score rather than arbitrary order", () => {
  const a = { ev: 2.0, score: 1 }, b = { ev: 5.0, score: 1 };
  assert.deepEqual([a, b].sort(O.byExpectedValue), [b, a]);
  const c = { ev: 2.0, score: 50 }, d = { ev: 2.0, score: 10 };
  assert.deepEqual([d, c].sort(O.byExpectedValue), [c, d]);
});

t("two unfillable rows still order by score instead of flipping about", () => {
  const a = { ev: null, score: 3 }, b = { ev: null, score: 9 };
  assert.deepEqual([a, b].sort(O.byExpectedValue), [b, a]);
});

const failed = out.filter(([s]) => s !== "ok").length;
for (const [s, n] of out) console.log(`${s === "ok" ? "ok  " : "FAIL"} ${n}`);
console.log(JSON.stringify({ ok: failed === 0, passed: out.length - failed, failed }));
process.exit(failed === 0 ? 0 : 1);
