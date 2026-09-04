/**
 * Monte Carlo over a day's settlement.
 *
 * A note on what this does and does not add. The probability engine already
 * computes each band's probability analytically - it integrates a normal over
 * the band edges (the "lattice"). Simulating that same normal to recover those
 * same probabilities would prove nothing except that both implementations can
 * do arithmetic.
 *
 * What is NOT analytically obvious is the P&L DISTRIBUTION of a position. Once
 * there are several legs, a fee that varies with price, and a payoff that is
 * all-or-nothing per band, the spread of outcomes - not the mean - is what
 * decides whether a position is survivable. A +EV basket that loses 80% of the
 * time is a different proposition from one that loses 20% of the time, and the
 * expected value alone cannot tell them apart. That is what this simulates.
 *
 * The draw is the day's maximum from the model's own centre and sigma, so the
 * simulation inherits exactly the distribution the desk is already betting on -
 * including a sigma already widened by regime and by forecast divergence.
 */

import { buy } from "./costs.ts";

export interface McBand {
  band_id: string;
  label: string;
  /** Celsius, null on an open tail. */
  lo: number | null;
  hi: number | null;
  /** Executable price, 0..1. */
  price: number | null;
  /** Dollars staked on YES for this band. */
  stake: number;
}

export interface McResult {
  runs: number;
  /** Per-band share of simulated outcomes - the empirical settlement pmf. */
  landed: Array<{ band_id: string; label: string; share: number; price: number | null }>;
  /** How often the day landed outside every band we know about. */
  offLadder: number;
  pnl: number[];
  mean: number;
  median: number;
  p5: number;
  p95: number;
  worst: number;
  best: number;
  probProfit: number;
  staked: number;
}

/** Box-Muller. Deterministic given `seed`, so a run is reproducible. */
function gaussian(rand: () => number): number {
  let u = 0, v = 0;
  while (u === 0) u = rand();
  while (v === 0) v = rand();
  return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
}

/** mulberry32 - small, fast, and seedable, so the same inputs give the same
 *  picture twice. A chart that reshuffles on every render cannot be read. */
function rng(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export function simulate(
  centreC: number,
  sigmaC: number,
  bands: McBand[],
  runs = 20000,
  seed = 42
): McResult {
  const rand = rng(seed);
  const counts = new Map<string, number>();
  const pnl: number[] = [];
  let offLadder = 0;

  // Pre-compute each leg once: the cost model does not change between runs.
  const legs = bands
    .map((b) => {
      const pos = b.stake > 0 && b.price ? buy(b.stake, b.price) : null;
      return pos ? { band_id: b.band_id, cost: pos.cost, payout: pos.payout } : null;
    })
    .filter(Boolean) as Array<{ band_id: string; cost: number; payout: number }>;
  const staked = legs.reduce((s, l) => s + l.cost, 0);

  for (let i = 0; i < runs; i++) {
    const draw = centreC + sigmaC * gaussian(rand);
    const hit = bands.find(
      (b) => (b.lo === null || draw >= b.lo) && (b.hi === null || draw < b.hi)
    );
    if (hit) counts.set(hit.band_id, (counts.get(hit.band_id) ?? 0) + 1);
    else offLadder++;

    if (legs.length) {
      let out = -staked;
      if (hit) {
        const win = legs.find((l) => l.band_id === hit.band_id);
        if (win) out += win.payout;
      }
      pnl.push(out);
    }
  }

  pnl.sort((a, b) => a - b);
  const at = (q: number) => (pnl.length ? pnl[Math.min(pnl.length - 1, Math.floor(q * pnl.length))] : 0);
  const mean = pnl.length ? pnl.reduce((s, v) => s + v, 0) / pnl.length : 0;

  return {
    runs,
    landed: bands.map((b) => ({
      band_id: b.band_id,
      label: b.label,
      share: (counts.get(b.band_id) ?? 0) / runs,
      price: b.price,
    })),
    offLadder: offLadder / runs,
    pnl,
    mean,
    median: at(0.5),
    p5: at(0.05),
    p95: at(0.95),
    worst: pnl.length ? pnl[0] : 0,
    best: pnl.length ? pnl[pnl.length - 1] : 0,
    probProfit: pnl.length ? pnl.filter((v) => v > 0).length / pnl.length : 0,
    staked,
  };
}

/** Bucket a P&L sample into a histogram. */
export function histogram(values: number[], bins = 28): Array<{ label: string; value: number; lo: number; hi: number }> {
  if (!values.length) return [];
  const lo = values[0], hi = values[values.length - 1];
  if (hi === lo) return [{ label: lo.toFixed(0), value: values.length, lo, hi }];
  const w = (hi - lo) / bins;
  const out = Array.from({ length: bins }, (_, i) => ({
    label: (lo + w * (i + 0.5)).toFixed(0),
    value: 0,
    lo: lo + w * i,
    hi: lo + w * (i + 1),
  }));
  for (const v of values) {
    const i = Math.min(bins - 1, Math.max(0, Math.floor((v - lo) / w)));
    out[i].value++;
  }
  return out;
}
