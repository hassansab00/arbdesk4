/**
 * THE ENGINE'S SIZING ARITHMETIC, for the strategy walkthroughs
 * (components/StrategyExplainer.tsx).
 *
 * The same formulas as scripts/holdings_solver.py and scripts/allocator.py,
 * line for line, so the worked market shows what the engine's solver buys on
 * it rather than a typed claim about what it would buy. tests/kelly.test.cjs
 * holds the answers to the ones holdings_solver gives on the worked market.
 */

/** The venue's taker fee per share: rate x p x (1 - p) (execution_cost.fee_per_share). */
export const FEE_RATE = 0.05;
export const feePerShare = (price: number) => FEE_RATE * price * (1 - price);

/** allocator.effective_cost: what one dollar of payout costs, fee included. */
export function effectiveCost(price: number | null | undefined): number | null {
  if (price == null || !(price > 0 && price < 1)) return null;
  return price + feePerShare(price);
}

/** holdings_solver.single_bucket_growth: the Kelly growth of one YES, 0 when p <= c. */
export function singleBucketGrowth(p: number, price: number): number {
  const c = effectiveCost(price);
  if (c === null || p <= c || p >= 1) return 0;
  return p * Math.log(p / c) + (1 - p) * Math.log((1 - p) / (1 - c));
}

export type Side = "YES" | "NO";

/** holdings_solver.solve's defaults. */
export const COVER_ITERS = 20000;
export const COVER_TOL = 1e-12;

/**
 * holdings_solver.solve for one side: the log-optimal fractions of wealth over
 * cash and the side's asset on every bucket with a price, by Cover's update.
 * prices[i] null: that bucket is not an asset. Returns the fraction per bucket
 * (0 where it is not an asset), the cash kept and the expected log-growth.
 */
export function solveKelly(probs: number[], prices: (number | null)[], side: Side,
                           iters = COVER_ITERS, tol = COVER_TOL) {
  const n = probs.length;
  const assets: { i: number | null; x: number[] }[] = [{ i: null, x: Array(n).fill(1) }];
  prices.forEach((price, i) => {
    const c = effectiveCost(price);
    if (c === null) return;
    assets.push({ i, x: probs.map((_, k) => (side === "YES" ? (k === i ? 1 / c : 0) : (k === i ? 0 : 1 / c))) });
  });
  const m = assets.length;
  const wealth = (w: number[]) => probs.map((_, k) => w.reduce((s, wj, j) => s + wj * assets[j].x[k], 0));
  const growth = (w: number[]) => {
    let g = 0;
    const W = wealth(w);
    for (let k = 0; k < n; k += 1) {
      if (probs[k] <= 0) continue;
      if (W[k] <= 0) return -Infinity;
      g += probs[k] * Math.log(W[k]);
    }
    return g;
  };
  let w = Array(m).fill(1 / m);
  let gOld = growth(w);
  let it = 0;
  for (it = 1; it <= iters; it += 1) {
    const W = wealth(w);
    const next = w.map((wj, j) => {
      let s = 0;
      for (let k = 0; k < n; k += 1) if (probs[k] > 0) s += (probs[k] * assets[j].x[k]) / W[k];
      return wj * s;
    });
    const z = next.reduce((a, b) => a + b, 0);
    w = next.map((v) => v / z);
    const g = growth(w);
    if (Math.abs(g - gOld) < tol) break;
    gOld = g;
  }
  const weights = Array(n).fill(0);
  assets.forEach((a, j) => { if (a.i !== null) weights[a.i] = w[j]; });
  return { weights, cash: w[0], growth: growth(w), iterations: it };
}
