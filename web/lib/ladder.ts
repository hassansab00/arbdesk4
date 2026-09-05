/**
 * Position maths for a whole board, the way ArbDesk 1 did it.
 *
 * The previous board answered one question per row - "what does THIS leg
 * return if it hits" - which is the easy half. The half that decides whether a
 * ticket is any good is the other one: given every stake on the board, what
 * happens under EACH possible outcome? Exactly one bucket resolves Yes, so a
 * board with several legs has one P&L per bucket, and the shape of that ladder
 * is the position.
 *
 * A NO leg is what makes this non-obvious. Betting No on the 24-25 bucket pays
 * out on every outcome except 24-25 - so one No stake contributes to ten
 * different rows of the ladder, and no per-row calculation can show that.
 */

import { buy, takerFee } from "./costs.ts";
import { DEFAULT_LIMITS, fill as walk, ladderFor, type Level, type Limits } from "./execution.ts";

export interface Leg {
  band_id: string;
  label: string;
  /** Executable YES price, 0..1. */
  yesPrice: number | null;
  /** Executable NO price, 0..1. */
  noPrice: number | null;
  /** Dollars fillable inside 5c on the YES side - the depth cap. */
  depthUsd: number | null;
  /** The real ask ladder, when the snapshot stored one. Walking it is the
   *  difference between "capped at $80" and "$80 at a worse average price". */
  yesLevels?: Level[] | null;
  noLevels?: Level[] | null;
  yesStake: number;
  noStake: number;
}

export interface LegFill {
  band_id: string;
  side: "YES" | "NO";
  stake: number;
  /** What actually fills, after the depth cap. */
  filled: number;
  shares: number;
  fee: number;
  /** True when the book cannot absorb the whole stake. */
  capped: boolean;
  /** Average price actually paid, from walking the ladder. */
  avgPrice: number | null;
  /** avgPrice - top of book: what the size cost you. */
  slippage: number | null;
  /** True when the leg is under the venue's order minimum and would be rejected. */
  belowMinimum: boolean;
}

export interface Outcome {
  band_id: string;
  label: string;
  /** Total P&L if the day settles in THIS bucket. */
  pnl: number;
  /** The probability being used for this bucket (model, or the reader's). */
  prob: number | null;
}

export interface BoardResult {
  fills: LegFill[];
  outcomes: Outcome[];
  /** Cash actually committed - capped stakes cost less than requested. */
  cost: number;
  requested: number;
  /** Sum of prob x pnl over the outcomes, when every bucket has a probability. */
  ev: number | null;
  worst: number;
  best: number;
  /** True when no outcome loses money: a locked ticket. */
  locked: boolean;
  anyCapped: boolean;
  /** True when any leg is under the venue's order minimum and would be rejected. */
  anyBelowMinimum: boolean;
}

/**
 * Fill one leg, respecting the book.
 *
 * Depth is the constraint that gets ignored until it costs money. A $500 stake
 * against a book holding $80 inside 5c does not buy $500 of anything; it buys
 * $80 and then walks the price. Rather than pretend, the fill is capped at the
 * measured depth and flagged - an honest $80 beats an imaginary $500.
 */
function fill(
  band_id: string, side: "YES" | "NO", stake: number, price: number | null,
  depthUsd: number | null, levels: Level[] | null | undefined, limits: Limits
): LegFill | null {
  if (!stake || stake <= 0 || price === null || price <= 0 || price >= 1) return null;

  // With a real ladder, walk it: a stake bigger than the top level does not
  // just cap, it fills at a WORSE AVERAGE, and the difference is the cost of
  // the size. Without one, fall back to the depth cap, which is still better
  // than pretending the book is bottomless.
  const built = ladderFor(levels, price, depthUsd, limits);
  if (built.levels.length) {
    const f = walk(stake, built.levels, built.known, null, limits);
    if (f.shares <= 0) {
      return {
        band_id, side, stake, filled: 0, shares: 0, fee: 0,
        capped: true, avgPrice: null, slippage: null,
        belowMinimum: f.problems.includes("below_minimum"),
      };
    }
    return {
      band_id, side, stake,
      filled: f.spent, shares: f.shares, fee: f.fee,
      capped: !f.complete, avgPrice: f.avgPrice, slippage: f.slippage,
      belowMinimum: false,
    };
  }

  const cap = depthUsd !== null && depthUsd > 0 ? Math.min(stake, depthUsd) : stake;
  const pos = buy(cap, price);
  if (!pos) return null;
  return {
    band_id, side, stake,
    filled: cap,
    shares: pos.shares,
    fee: pos.fee,
    capped: cap < stake - 1e-9,
    avgPrice: price,
    slippage: 0,
    belowMinimum: cap < limits.minOrderUsd,
  };
}

export function solveBoard(legs: Leg[], probs?: Map<string, number | null>, limits: Limits = DEFAULT_LIMITS): BoardResult {
  const fills: LegFill[] = [];
  for (const l of legs) {
    const y = fill(l.band_id, "YES", l.yesStake, l.yesPrice, l.depthUsd, l.yesLevels, limits);
    if (y) fills.push(y);
    // The NO side has its OWN ladder. Where the snapshot stored one it is
    // walked; where it did not, NO is left uncapped rather than borrowing the
    // YES depth figure, which describes a different book.
    const n = fill(l.band_id, "NO", l.noStake, l.noPrice, null, l.noLevels, limits);
    if (n) fills.push(n);
  }

  const cost = fills.reduce((s, f) => s + f.filled, 0);
  const requested = legs.reduce((s, l) => s + (l.yesStake || 0) + (l.noStake || 0), 0);

  const outcomes: Outcome[] = legs.map((winner) => {
    let payout = 0;
    for (const f of fills) {
      // YES pays only on its own bucket; NO pays on every OTHER bucket.
      const hits = f.side === "YES" ? f.band_id === winner.band_id : f.band_id !== winner.band_id;
      if (hits) payout += f.shares;
    }
    return {
      band_id: winner.band_id,
      label: winner.label,
      pnl: payout - cost,
      prob: probs?.get(winner.band_id) ?? null,
    };
  });

  const pnls = outcomes.map((o) => o.pnl);
  const haveAllProbs = outcomes.length > 0 && outcomes.every((o) => o.prob !== null);
  const ev = haveAllProbs ? outcomes.reduce((s, o) => s + (o.prob as number) * o.pnl, 0) : null;

  return {
    fills, outcomes, cost, requested, ev,
    worst: pnls.length ? Math.min(...pnls) : 0,
    best: pnls.length ? Math.max(...pnls) : 0,
    locked: pnls.length > 0 && Math.min(...pnls) >= 0 && cost > 0,
    anyCapped: fills.some((f) => f.capped),
    anyBelowMinimum: fills.some((f) => f.belowMinimum),
  };
}

/** What the market implies this bucket's probability is. For a binary
 *  contract the executable price IS the implied probability, which is why the
 *  board can show cents and percent as the same column read two ways. */
export function impliedProb(price: number | null | undefined): number | null {
  if (price === null || price === undefined || !Number.isFinite(price)) return null;
  return price;
}

/** Sum of implied probabilities across a city's buckets. Exactly one pays, so
 *  this should be ~1.00; the gap is the book's overround, or an arbitrage. */
export function overround(legs: Leg[]): number | null {
  const priced = legs.filter((l) => l.yesPrice !== null);
  if (priced.length < 2) return null;
  return priced.reduce((s, l) => s + (l.yesPrice as number), 0);
}

export { takerFee };
