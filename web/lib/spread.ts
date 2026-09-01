/**
 * Spread math — a direct port of ArbDesk 1's Goal engine (index.html:
 * spread / spreadNo / invertYesTarget / invertNoTarget / coverageIndices /
 * buildTiers / ladderCost / ladderShares / ladderBreaks / centLegs).
 *
 * Same semantics, same closed forms, same $1-minimum handling, so a plan
 * built here matches AD4-1's numbers to the cent. What changed is only
 * where the inputs come from: AD4 reads real bands, real ask/bid ladders
 * from book_snapshots, and model probabilities from band_probabilities,
 * instead of a hand-typed board.
 *
 * The two solve directions are the whole point of the section:
 *   "I set profit"  -> size the spread to pay `target` if a covered band
 *                      wins, and report the budget it needs.
 *   "I set budget"  -> invert it: spend exactly `budget` and report the
 *                      profit that buys.
 * Both are linear in `target`, which is why the inversion is exact rather
 * than a search, except on a laddered book where it bisects on cost.
 */

export type Side = "yes" | "no";
export type RiskMode = "vsafe" | "safe" | "mid" | "risky" | "custom";
export type Solve = "target" | "budget";

/** One tradeable band as the spread engine sees it. */
export interface SpreadBand {
  band_id: string;
  label: string;
  /** Best ask in cents, YES side. */
  yes: number;
  /** Best ask in cents, NO side. */
  no: number;
  /** Your probability for this band, 0-100 (percent). */
  yourPct: number;
  /** Ask ladder, YES side: [priceCents, size][] best-first. */
  yesBook: Array<[number, number]>;
  /** Ask ladder, NO side. */
  noBook: Array<[number, number]>;
  /** 24h traded volume in USD for this band. */
  volumeUsd: number;
  city_key?: string;
  /**
   * Where the YES ladder came from: "raw_book"/"levels_jsonb" is a real
   * order book, "synthetic_tiers" is approximated from the cumulative
   * USD-depth columns because no ladder was stored. The maths below treats
   * both the same - it is the UI's job to say which one you are looking at.
   */
  ladderSource?: string;
}

export interface SpreadLeg {
  idx: number;
  band_id: string;
  label: string;
  /** Average fill price in cents, from walking the ladder. */
  price: number;
  shares: number;
  cost: number;
  volumeUsd: number;
}

export interface SpreadResult {
  feasible: boolean;
  reason?: "empty" | "overpriced" | "depth" | "need2";
  sumP?: number;
  shares?: number;
  budget?: number;
  profitIfCovered?: number;
  profitIfUncovered?: number;
  coverageProb: number;
  tailProb?: number;
  lossIfUncovered?: number;
  ev?: number;
  legs?: SpreadLeg[];
  depthCapped?: boolean;
  maxProfit?: number | null;
  maxBudget?: number;
  maxShares?: number;
  sMax?: number | null;
  k?: number;
  /** Total 24h traded volume across the covered legs. */
  volumeUsd?: number;
  /** Legs whose 24h volume is under the thin-market threshold. */
  thinLegs?: SpreadLeg[];
}

export const MIN_ORDER = 1; // Polymarket minimum order size, USD

const C = (cents: number) => cents / 100;
export const ceilC = (x: number) => Math.ceil(x * 100 - 1e-9) / 100;
export const roundC = (x: number) => Math.round(x * 100) / 100;

export function bookOfSide(b: SpreadBand, side: Side): Array<[number, number]> {
  const L = side === "no" ? b.noBook : b.yesBook;
  if (Array.isArray(L) && L.length) return L;
  const px = Number(side === "no" ? b.no : b.yes) || 0;
  // No depth data: fall back to an infinitely deep book at top-of-book,
  // which is exactly AD4-1's behaviour and reduces to the closed form.
  return px > 0 ? [[px, Infinity]] : [];
}

const maxSharesOf = (L: Array<[number, number]>) => L.reduce((s, l) => s + (Number(l[1]) || 0), 0);
const isFlatBook = (L: Array<[number, number]>) => L.length === 1 && !Number.isFinite(L[0][1]);

/** Cost in dollars of buying `sh` shares by walking the ladder best-first. */
export function ladderCost(L: Array<[number, number]>, sh: number) {
  let need = sh, cost = 0, filled = 0;
  for (let i = 0; i < L.length; i++) {
    if (need <= 1e-12) break;
    const take = Math.min(need, Number(L[i][1]));
    cost += take * C(Number(L[i][0]));
    filled += take;
    need -= take;
  }
  return { cost, filled, capped: filled < sh - 1e-9, avg: filled > 0 ? (cost / filled) * 100 : 0 };
}

/** Shares a dollar stake buys, walking the ladder. */
export function ladderShares(L: Array<[number, number]>, stake: number) {
  let cash = Number(stake) || 0, sh = 0;
  for (let i = 0; i < L.length; i++) {
    if (cash <= 1e-12) break;
    const p = C(Number(L[i][0]));
    if (p <= 0) continue;
    const take = Math.min(cash / p, Number(L[i][1]));
    sh += take;
    cash -= take * p;
  }
  return { shares: sh, spent: (Number(stake) || 0) - cash, capped: cash > 1e-9 };
}

function ladderBreaks(books: Array<Array<[number, number]>>, sMax: number): number[] {
  const b = new Set<number>([0]);
  books.forEach((L) => {
    let c = 0;
    for (let i = 0; i < L.length; i++) {
      c += Number(L[i][1]);
      if (Number.isFinite(c) && c < sMax - 1e-9) b.add(c);
    }
  });
  if (Number.isFinite(sMax)) b.add(sMax);
  return [...b].sort((a, c) => a - c);
}

/** Normalised probabilities from the yourPct column. */
export function normProb(b: SpreadBand[]): number[] {
  const t = b.reduce((s, x) => s + (Number(x.yourPct) || 0), 0);
  return t > 0 ? b.map((x) => (Number(x.yourPct) || 0) / t) : b.map(() => 1 / Math.max(1, b.length));
}

/** Cent-exact order sizes — what you will actually type into Polymarket. */
export function centLegs(legs: SpreadLeg[]): SpreadLeg[] {
  return (legs || []).map((L) => ({ ...L, cost: roundC(L.cost) }));
}

export function subMinLegs(legs: SpreadLeg[] | undefined): number {
  return (legs || []).filter((L) => L.cost > 0 && L.cost < MIN_ORDER - 1e-9).length;
}

export function minYesTarget(b: SpreadBand[], cov: number[]): number | null {
  const sumP = cov.reduce((s, i) => s + C(b[i].yes), 0);
  if (sumP >= 1 || !cov.length) return null;
  const priced = cov.map((i) => C(b[i].yes)).filter((p) => p > 0);
  if (!priced.length) return null;
  const pmin = Math.min(...priced);
  return pmin > 0 ? (MIN_ORDER * (1 - sumP)) / pmin : null;
}

export function minNoTarget(b: SpreadBand[], cov: number[]): number | null {
  const k = cov.length;
  if (k < 2) return null;
  const sumP = cov.reduce((s, i) => s + C(b[i].no), 0);
  const d = k - 1 - sumP;
  if (d <= 0) return null;
  const priced = cov.map((i) => C(b[i].no)).filter((p) => p > 0);
  if (!priced.length) return null;
  const pmin = Math.min(...priced);
  return pmin > 0 ? (MIN_ORDER * d) / pmin : null;
}

function bookState(B: SpreadBand[]) {
  const priced = B.filter((x) => Number(x.yes) > 0).length;
  const unpriced = B.filter(
    (x) => !(Number(x.yes) > 0) && (x.label?.trim() || Number(x.no) > 0 || Number(x.yourPct) > 0)
  ).length;
  return { priced, unpriced, complete: priced >= 2 && unpriced === 0, sum: B.reduce((s, x) => s + C(x.yes), 0) };
}

/**
 * YES-lock: equal YES shares of every covered band. Any covered winner
 * pays exactly `target`; an uncovered winner loses the whole budget.
 */
export function spread(b: SpreadBand[], cov: number[], target: number, thinUsd = 0): SpreadResult {
  const p = normProb(b);
  const coverageProb = cov.reduce((s, i) => s + p[i], 0);
  if (cov.length === 0) return { feasible: false, reason: "empty", coverageProb: 0 };
  const sumP = cov.reduce((s, i) => s + C(b[i].yes), 0);
  if (sumP >= 1) return { feasible: false, reason: "overpriced", sumP, coverageProb };
  const books = cov.map((i) => bookOfSide(b[i], "yes"));
  if (books.some((L) => !L.length)) return { feasible: false, reason: "empty", coverageProb };

  const finish = (shares: number, depthCapped: boolean, maxProfit: number | null, sMax: number | null): SpreadResult => {
    const legs: SpreadLeg[] = cov.map((i, k) => {
      const fc = ladderCost(books[k], shares);
      return {
        idx: i,
        band_id: b[i].band_id,
        label: b[i].label,
        price: fc.filled > 0 ? Math.round(fc.avg * 10) / 10 : b[i].yes,
        shares,
        cost: fc.cost,
        volumeUsd: b[i].volumeUsd,
      };
    });
    const budget = legs.reduce((a, L) => a + L.cost, 0);
    const profit = shares - budget;
    const volumeUsd = legs.reduce((a, L) => a + (L.volumeUsd || 0), 0);
    return {
      feasible: true, sumP, shares, budget, profitIfCovered: profit, coverageProb,
      tailProb: 1 - coverageProb, lossIfUncovered: -budget,
      ev: coverageProb * profit + (1 - coverageProb) * -budget,
      legs, depthCapped, maxProfit, sMax: sMax !== null && Number.isFinite(sMax) ? sMax : null,
      volumeUsd, thinLegs: legs.filter((L) => (L.volumeUsd || 0) < thinUsd),
    };
  };

  if (books.every(isFlatBook)) return finish(target / (1 - sumP), false, null, null);

  const sMax = Math.min(...books.map(maxSharesOf));
  if (!(sMax > 0))
    return { feasible: false, reason: "depth", sumP, coverageProb, maxProfit: 0, maxBudget: 0, maxShares: 0, sMax: 0 };

  const pts = ladderBreaks(books, sMax);
  const profAt = (s: number) => s - books.reduce((t, L) => t + ladderCost(L, s).cost, 0);
  const prof = pts.map(profAt);
  let best = { s: 0, pr: 0 };
  pts.forEach((s, j) => { if (prof[j] > best.pr) best = { s, pr: prof[j] }; });

  if (target > best.pr + 1e-9) {
    const mb = books.reduce((t, L) => t + ladderCost(L, best.s).cost, 0);
    return { feasible: false, reason: "depth", sumP, coverageProb, maxProfit: best.pr, maxBudget: mb, maxShares: best.s, sMax };
  }

  let s: number | null = null;
  for (let j = 1; j < pts.length; j++) {
    const a = prof[j - 1], c2 = prof[j];
    if (a >= target - 1e-9) { s = pts[j - 1]; break; }
    if (c2 >= target - 1e-9) {
      s = c2 > a ? pts[j - 1] + ((target - a) * (pts[j] - pts[j - 1])) / (c2 - a) : pts[j];
      break;
    }
  }
  if (s === null) s = best.s;
  return finish(s, Math.abs(s - sMax) < 1e-6, best.pr, sMax);
}

/**
 * NO-lock: equal NO shares on every covered band. If the winner IS in
 * cov, exactly one leg fails to pay, so (k-1) legs pay = target by
 * construction. If the winner is OUTSIDE cov, every leg pays. Needs
 * sum(NO asks over cov) < (k-1).
 */
export function spreadNo(b: SpreadBand[], cov: number[], target: number, thinUsd = 0): SpreadResult {
  const k = cov.length;
  const p = normProb(b);
  const coverageProb = cov.reduce((s, i) => s + p[i], 0);
  if (k < 2) return { feasible: false, reason: "need2", coverageProb };
  const sumP = cov.reduce((s, i) => s + C(b[i].no), 0);
  const denom = k - 1 - sumP;
  if (denom <= 0) return { feasible: false, reason: "overpriced", sumP, coverageProb, k };
  const books = cov.map((i) => bookOfSide(b[i], "no"));
  if (books.some((L) => !L.length)) return { feasible: false, reason: "overpriced", sumP, coverageProb, k };

  const finish = (shares: number, depthCapped: boolean, maxProfit: number | null, sMax: number | null): SpreadResult => {
    const legs: SpreadLeg[] = cov.map((i, kk) => {
      const fc = ladderCost(books[kk], shares);
      return {
        idx: i, band_id: b[i].band_id, label: b[i].label,
        price: fc.filled > 0 ? Math.round(fc.avg * 10) / 10 : b[i].no,
        shares, cost: fc.cost, volumeUsd: b[i].volumeUsd,
      };
    });
    const budget = legs.reduce((a, L) => a + L.cost, 0);
    const pC = (k - 1) * shares - budget;
    const pO = k * shares - budget;
    const volumeUsd = legs.reduce((a, L) => a + (L.volumeUsd || 0), 0);
    return {
      feasible: true, sumP, shares, budget, k, coverageProb, tailProb: 1 - coverageProb,
      profitIfCovered: pC, profitIfUncovered: pO,
      ev: coverageProb * pC + (1 - coverageProb) * pO,
      legs, depthCapped, maxProfit, sMax: sMax !== null && Number.isFinite(sMax) ? sMax : null,
      volumeUsd, thinLegs: legs.filter((L) => (L.volumeUsd || 0) < thinUsd),
    };
  };

  if (books.every(isFlatBook)) return finish(target / denom, false, null, null);

  const sMax = Math.min(...books.map(maxSharesOf));
  if (!(sMax > 0))
    return { feasible: false, reason: "depth", sumP, coverageProb, k, maxProfit: 0, maxBudget: 0, maxShares: 0, sMax: 0 };

  const pts = ladderBreaks(books, sMax);
  const profAt = (s: number) => (k - 1) * s - books.reduce((t, L) => t + ladderCost(L, s).cost, 0);
  const prof = pts.map(profAt);
  let best = { s: 0, pr: 0 };
  pts.forEach((s, j) => { if (prof[j] > best.pr) best = { s, pr: prof[j] }; });

  if (target > best.pr + 1e-9) {
    const mb = books.reduce((t, L) => t + ladderCost(L, best.s).cost, 0);
    return { feasible: false, reason: "depth", sumP, coverageProb, k, maxProfit: best.pr, maxBudget: mb, maxShares: best.s, sMax };
  }

  let s: number | null = null;
  for (let j = 1; j < pts.length; j++) {
    const a = prof[j - 1], c2 = prof[j];
    if (a >= target - 1e-9) { s = pts[j - 1]; break; }
    if (c2 >= target - 1e-9) {
      s = c2 > a ? pts[j - 1] + ((target - a) * (pts[j] - pts[j - 1])) / (c2 - a) : pts[j];
      break;
    }
  }
  if (s === null) s = best.s;
  return finish(s, Math.abs(s - sMax) < 1e-6, best.pr, sMax);
}

/** budget -> target inversion. Exact on a flat book, bisection on a ladder. */
export function invertYesTarget(b: SpreadBand[], cov: number[], budget: number): number {
  if (!cov.length) return 0;
  const books = cov.map((i) => bookOfSide(b[i], "yes"));
  if (books.some((L) => !L.length)) return 0;
  const sumP = cov.reduce((s, i) => s + C(b[i].yes), 0);
  if (books.every(isFlatBook)) return sumP > 0 ? (budget * (1 - sumP)) / sumP : 0;
  const sMax = Math.min(...books.map(maxSharesOf));
  if (!(sMax > 0)) return 0;
  const costAt = (s: number) => books.reduce((t, L) => t + ladderCost(L, s).cost, 0);
  const B2 = Math.min(Number(budget) || 0, costAt(sMax));
  if (B2 <= 0) return 0;
  let lo = 0, hi = sMax;
  for (let j = 0; j < 48; j++) {
    const mid = (lo + hi) / 2;
    if (costAt(mid) < B2) lo = mid; else hi = mid;
  }
  const s = (lo + hi) / 2;
  return s - costAt(s);
}

export function invertNoTarget(b: SpreadBand[], cov: number[], budget: number): number {
  const k = cov.length;
  if (k < 2) return 0;
  const books = cov.map((i) => bookOfSide(b[i], "no"));
  if (books.some((L) => !L.length)) return 0;
  const sumP = cov.reduce((s, i) => s + C(b[i].no), 0);
  const denom = k - 1 - sumP;
  if (books.every(isFlatBook)) return sumP > 0 ? (budget * denom) / sumP : 0;
  const sMax = Math.min(...books.map(maxSharesOf));
  if (!(sMax > 0)) return 0;
  const costAt = (s: number) => books.reduce((t, L) => t + ladderCost(L, s).cost, 0);
  const B2 = Math.min(Number(budget) || 0, costAt(sMax));
  if (B2 <= 0) return 0;
  let lo = 0, hi = sMax;
  for (let j = 0; j < 48; j++) {
    const mid = (lo + hi) / 2;
    if (costAt(mid) < B2) lo = mid; else hi = mid;
  }
  const s = (lo + hi) / 2;
  return (k - 1) * s - costAt(s);
}

/**
 * Single source of truth for "which bands are covered" — shared by the
 * spread pane AND the tier cards, so selecting a mode makes the pane match
 * its tier card to the cent, with no drift between them.
 */
export function coverageIndices(b: SpreadBand[], mode: RiskMode, threshPct = 0): number[] {
  const byProb = b
    .map((_, i) => i)
    .filter((i) => C(b[i].yes) > 0)
    .sort((a, c) => Number(b[c].yourPct) - Number(b[a].yourPct));
  const n = byProb.length;
  const take = (k: number) => byProb.slice(0, Math.max(1, Math.min(k, n)));
  const st = bookState(b);
  const fullArb = st.sum < 1 && st.complete;
  switch (mode) {
    case "risky": return take(1);
    case "mid": return take(2);
    case "safe": return take(3);
    case "vsafe": return fullArb ? byProb.slice() : n <= 4 ? byProb.slice() : take(n - 1);
    default:
      return b.map((_, i) => i).filter((i) => Number(b[i].yourPct) / 100 >= (threshPct || 0) / 100);
  }
}

export interface Tier {
  key: RiskMode;
  name: string;
  desc: string;
  cov: number[];
  s: SpreadResult;
  chip: "ok" | "warn" | "bad";
  msg: string;
}

/** The four risk tiers, priced side by side for the same target/budget. */
export function buildTiers(
  b: SpreadBand[],
  target: number,
  stop: number,
  budgetMode: boolean,
  rawBudget: number,
  thinUsd = 0
): Tier[] {
  const st = bookState(b);
  const fullArb = st.sum < 1 && st.complete;
  const defs: Array<{ key: RiskMode; name: string; desc: string; cov: number[] }> = [
    { key: "vsafe", name: "Very safe", desc: fullArb ? "true arbitrage — full book" : "covers all but the thinnest tail", cov: coverageIndices(b, "vsafe") },
    { key: "safe", name: "Safe", desc: "covers the top three bands", cov: coverageIndices(b, "safe") },
    { key: "mid", name: "Mid", desc: "top two — anchor + insure zone", cov: coverageIndices(b, "mid") },
    { key: "risky", name: "Risky", desc: "the favourite only", cov: coverageIndices(b, "risky") },
  ];
  return defs.map((d) => {
    const t = budgetMode ? invertYesTarget(b, d.cov, rawBudget) : target;
    const s = spread(b, d.cov, t, thinUsd);
    let chip: Tier["chip"] = "ok";
    let msg = "within plan";
    if (!s.feasible) {
      chip = "bad";
      msg = s.reason === "overpriced" ? "cost over $1/$1"
        : s.reason === "depth" ? `book too thin — max $${s.maxProfit != null ? s.maxProfit.toFixed(0) : "?"}`
        : "no bands";
    } else if (stop > 0 && (s.budget ?? 0) > stop) {
      chip = "bad"; msg = "over stop floor";
    } else if ((s.ev ?? 0) < 0) {
      chip = "warn"; msg = "negative EV";
    }
    if (s.feasible && subMinLegs(s.legs)) { chip = "warn"; msg = "legs under $1 min"; }
    if (s.feasible && chip === "ok" && (s.thinLegs?.length ?? 0) > 0) {
      chip = "warn";
      msg = `${s.thinLegs!.length} thin-volume leg${s.thinLegs!.length > 1 ? "s" : ""}`;
    }
    return { ...d, s, chip, msg };
  });
}

/** Book-wide risk-free check, both sides — mirrors AD4-1's reverseReport. */
export function guaranteedCheck(b: SpreadBand[], target: number, thinUsd = 0) {
  const st = bookState(b);
  const allY = b.map((_, i) => i).filter((i) => C(b[i].yes) > 0);
  const sumPall = allY.reduce((s, i) => s + C(b[i].yes), 0);
  const yes = sumPall < 1 && st.complete
    ? { available: true as const, ...spread(b, allY, target, thinUsd), overround: sumPall - 1 }
    : { available: false as const, incomplete: !st.complete, unpriced: st.unpriced, sumPall, overround: sumPall - 1 };

  const allN = b.map((_, i) => i).filter((i) => C(b[i].no) > 0);
  const sumNall = allN.reduce((s, i) => s + C(b[i].no), 0);
  const kAll = allN.length;
  const denomAll = kAll - 1 - sumNall;
  const no = denomAll > 0 && st.complete && kAll >= 2
    ? { available: true as const, ...spreadNo(b, allN, target, thinUsd), overround: sumNall - (kAll - 1) }
    : { available: false as const, sumNall, kAll, overround: sumNall - (kAll - 1) };

  return { yes, no, bookSumYes: sumPall, complete: st.complete, unpriced: st.unpriced };
}

/** Analytic multi-day projection: per-day win/loss economics compounded. */
export function binomPMF(k: number, n: number, p: number): number {
  if (k < 0 || k > n) return 0;
  if (p <= 0) return k === 0 ? 1 : 0;
  if (p >= 1) return k === n ? 1 : 0;
  let lc = 0;
  for (let i = 0; i < k; i++) lc += Math.log(n - i) - Math.log(i + 1);
  return Math.exp(lc + k * Math.log(p) + (n - k) * Math.log(1 - p));
}

export function durAnalytic(q: number, P: number, B: number, D: number) {
  const dailyEV = q * P - (1 - q) * B;
  let pProfit = 0;
  for (let k = 0; k <= D; k++) {
    const total = k * P - (D - k) * B;
    if (total > 1e-9) pProfit += binomPMF(k, D, q);
  }
  const dayVar = q * Math.pow(P - dailyEV, 2) + (1 - q) * Math.pow(-B - dailyEV, 2);
  return { dailyEV, evTotal: D * dailyEV, pProfit, sd: Math.sqrt(D * dayVar), best: D * P, worst: -D * B, expWinDays: D * q };
}

/**
 * Weighted (by conviction): stakes split proportional to your probability
 * rather than equal shares, so every covered band pays — just unevenly.
 * Direct port of AD4-1's `goalSide === 'wt'` branch, including the depth
 * refinement that bisects on the real ladders so the MINIMUM covered
 * return actually reaches the target.
 */
export interface WeightedResult {
  feasible: boolean;
  reason?: "empty" | "inputs" | "no_edge" | "depth";
  depthMax?: number;
  budget?: number;
  legs?: Array<SpreadLeg & { weight: number; pl: number }>;
  minReturn?: number;
  maxReturn?: number;
  coverageProb: number;
  lossIfUncovered?: number;
  ev?: number;
  volumeUsd?: number;
  thinLegs?: SpreadLeg[];
  minOrderHint?: number | null;
}

export function spreadWeighted(
  b: SpreadBand[],
  cov: number[],
  amount: number,
  solve: Solve,
  thinUsd = 0
): WeightedResult {
  const p = normProb(b);
  const coverageProb = cov.reduce((s, i) => s + p[i], 0);
  if (!cov.length) return { feasible: false, reason: "empty", coverageProb: 0 };

  const wRaw = cov.map((i) => Number(b[i].yourPct) || 0);
  const wTot = wRaw.reduce((a, x) => a + x, 0);
  const prices = cov.map((i) => C(b[i].yes));
  if (wTot <= 0 || prices.some((x) => !(x > 0))) return { feasible: false, reason: "inputs", coverageProb };

  const w = wRaw.map((x) => x / wTot);
  const ratios = w.map((x, k) => x / prices[k]);
  const minR = Math.min(...ratios);

  let Bd: number;
  if (solve === "budget") Bd = amount;
  else if (minR > 1) Bd = amount / (minR - 1);
  else return { feasible: false, reason: "no_edge", coverageProb };

  const hasLadders = cov.some((i) => Array.isArray(b[i].yesBook) && b[i].yesBook.length > 0);
  if (solve !== "budget" && hasLadders) {
    const minRet = (bd: number) =>
      Math.min(...cov.map((i, k) => ladderShares(bookOfSide(b[i], "yes"), bd * w[k]).shares - bd));
    let hi = Bd, g = minRet(hi), it = 0;
    while (g < amount - 1e-9 && it < 48) { hi *= 1.5; g = minRet(hi); it++; }
    if (g < amount - 1e-9) return { feasible: false, reason: "depth", depthMax: Math.max(0, g), coverageProb };
    let lo = 0;
    for (let j = 0; j < 48; j++) {
      const mid = (lo + hi) / 2;
      if (minRet(mid) < amount) lo = mid; else hi = mid;
    }
    Bd = hi;
  }

  const raw = cov.map((i, k) => ({
    idx: i, band_id: b[i].band_id, label: b[i].label, price: b[i].yes,
    shares: 0, cost: roundC(Bd * w[k]), volumeUsd: b[i].volumeUsd, weight: w[k], pl: 0,
  }));
  const budget = raw.reduce((a, L) => a + L.cost, 0);
  const legs = raw.map((L, k) => {
    const sh = ladderShares(bookOfSide(b[cov[k]], "yes"), L.cost).shares;
    return { ...L, shares: sh, pl: sh - budget };
  });
  const pls = legs.map((L) => L.pl);
  const ev = cov.reduce((a, i, k) => a + p[i] * pls[k], 0) + (1 - coverageProb) * -budget;
  const wMin = Math.min(...w);
  const minOrderHint = wMin > 0
    ? solve === "budget" ? ceilC(MIN_ORDER / wMin) : minR > 1 ? ceilC((MIN_ORDER * (minR - 1)) / wMin) : null
    : null;

  return {
    feasible: true, budget, legs,
    minReturn: Math.min(...pls), maxReturn: Math.max(...pls),
    coverageProb, lossIfUncovered: -budget, ev,
    volumeUsd: legs.reduce((a, L) => a + (L.volumeUsd || 0), 0),
    thinLegs: legs.filter((L) => (L.volumeUsd || 0) < thinUsd),
    minOrderHint,
  };
}

/** Applies a percentage slippage buffer to every price and ladder level. */
export function slipBands(B: SpreadBand[], slipPct: number): SpreadBand[] {
  const s = 1 + Math.max(0, Number(slipPct) || 0) / 100;
  if (s === 1) return B;
  const sc = (L: Array<[number, number]>): Array<[number, number]> =>
    (L || []).map((l) => [Math.min(99.9, Number(l[0]) * s), Number(l[1])] as [number, number]);
  return B.map((x) => ({
    ...x,
    yes: Math.min(99.9, Number(x.yes) * s),
    no: Math.min(99.9, Number(x.no) * s),
    yesBook: sc(x.yesBook),
    noBook: sc(x.noBook),
  }));
}
