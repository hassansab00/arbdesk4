/**
 * What you can ACTUALLY place, and what it will ACTUALLY return.
 *
 * TWO THINGS EVERY POSITION FIGURE ON THIS DESK USED TO IGNORE.
 *
 * 1. THE VENUE HAS A FLOOR. Polymarket refuses an order below its minimum
 *    notional. A plan whose legs come out at 60c is not a small plan, it is
 *    no plan - and quoting its profit with a warning underneath is worse than
 *    quoting nothing, because the number is the thing people read.
 *
 * 2. THE BOOK HAS A DEPTH, AND market_price IS A MID. You buy at the ask and
 *    you buy DOWN THE LADDER: the second hundred dollars fills worse than the
 *    first, and past the end of the book it does not fill at all. Every "if
 *    right / EV" figure computed from one price is the best case of the first
 *    share, presented as the whole ticket.
 *
 * So this module answers one question - "I want to put $X on this side; what
 * happens?" - and every page that sizes a trade goes through it. Where the
 * book is unknown it says so rather than assuming infinite depth at the mid.
 *
 * The fee is scripts/cost_model.py's, via lib/costs.ts, so a fill priced here
 * and a fill priced by the engine cannot disagree.
 */

import { takerFee } from "./costs.ts";

export interface Limits {
  /** Polymarket rejects an order below this notional. */
  minOrderUsd: number;
  /** Order quantities round to this many shares. */
  shareStep: number;
  /** Limit prices snap to this. */
  priceTick: number;
  /** Most of a visible level the desk assumes it can take. */
  maxBookFraction: number;
  /** True while these are venue-wide placeholders rather than per-market. */
  provisional: boolean;
}

export const DEFAULT_LIMITS: Limits = {
  minOrderUsd: 1,
  shareStep: 0.01,
  priceTick: 0.01,
  maxBookFraction: 0.5,
  provisional: true,
};

/** One price level: [price in dollars 0..1, shares available]. */
export type Level = [number, number];

export type FillProblem =
  | "no_book"
  | "below_minimum"
  | "partially_filled"
  | "nothing_fillable";

export interface Fill {
  /** Dollars the operator asked to commit. */
  requested: number;
  /** Dollars that actually change hands, fee included, after rounding. */
  spent: number;
  /** Shares obtained, rounded to the venue's step. */
  shares: number;
  /** Weighted average price actually paid per share, before fee. */
  avgPrice: number | null;
  /** Best price on the book - what a single share would have cost. */
  topPrice: number | null;
  /** avgPrice - topPrice: what walking the ladder cost you. */
  slippage: number | null;
  fee: number;
  /** One dollar per share if this side wins. */
  payout: number;
  /** payout - spent. */
  profit: number;
  /** p x profit - (1 - p) x spent, at whatever probability is supplied. */
  ev: number | null;
  roi: number | null;
  /** True when the whole requested amount could be placed. */
  complete: boolean;
  /** Why it is not what was asked for. Empty when the fill is clean. */
  problems: FillProblem[];
  /** A sentence for the UI. Never empty. */
  note: string;
}

const round = (x: number, step: number) => Math.round(x / step) * step;
const floorTo = (x: number, step: number) => Math.floor(x / step + 1e-9) * step;

/**
 * The executable ladder for a side.
 *
 * `levels` is the real book when there is one. With no book at all the honest
 * answer is NOT "infinite depth at the mid" - that is the assumption that made
 * every figure optimistic - so the caller gets `no_book` and a single level at
 * the quoted price capped at the stated depth, or nothing if neither is known.
 */
export function ladderFor(
  levels: Level[] | null | undefined,
  quotedPrice: number | null | undefined,
  depthUsd: number | null | undefined,
  limits: Limits
): { levels: Level[]; known: boolean } {
  const clean = (levels ?? [])
    .filter((l) => Array.isArray(l) && Number.isFinite(l[0]) && Number.isFinite(l[1]) && l[0] > 0 && l[1] > 0)
    .map((l) => [l[0], l[1] * limits.maxBookFraction] as Level);
  if (clean.length) return { levels: clean, known: true };

  if (quotedPrice != null && quotedPrice > 0 && quotedPrice < 1) {
    // No ladder, but a quote and a depth figure. One level, capped by the
    // depth, is a far better model than an unbounded one - and it is flagged.
    const shares = depthUsd != null && depthUsd > 0
      ? (depthUsd * limits.maxBookFraction) / quotedPrice
      : Infinity;
    return { levels: [[quotedPrice, shares]], known: false };
  }
  return { levels: [], known: false };
}

/**
 * Walk the ladder for `usd`, respecting the venue's floor and step.
 *
 * The fee comes OUT of the money committed rather than being added on top, so
 * "risk $100" means exactly $100 leaves the account - the same convention
 * lib/costs.ts uses, and the one the paper engine settles against.
 */
export function fill(
  usd: number,
  levels: Level[],
  bookKnown: boolean,
  modelProb: number | null | undefined,
  limits: Limits = DEFAULT_LIMITS
): Fill {
  const problems: FillProblem[] = [];
  const empty = (note: string, ps: FillProblem[]): Fill => ({
    requested: usd, spent: 0, shares: 0, avgPrice: null, topPrice: levels[0]?.[0] ?? null,
    slippage: null, fee: 0, payout: 0, profit: 0, ev: null, roi: null,
    complete: false, problems: ps, note,
  });

  if (!Number.isFinite(usd) || usd <= 0) return empty("No size set.", ["nothing_fillable"]);
  if (levels.length === 0) {
    return empty("No book and no quote for this side — nothing can be sized against it.", ["no_book"]);
  }
  if (!bookKnown) problems.push("no_book");

  if (usd < limits.minOrderUsd) {
    return empty(
      `$${usd.toFixed(2)} is under the venue's $${limits.minOrderUsd.toFixed(2)} minimum — this order would be rejected, not filled small.`,
      [...problems, "below_minimum"]
    );
  }

  // Walk. Cash pays for shares AND the fee on those shares, level by level,
  // because the fee depends on the price of the level being taken.
  let cash = usd;
  let shares = 0;
  let notional = 0;
  for (const [price, avail] of levels) {
    if (cash <= 1e-9) break;
    if (!(price > 0 && price < 1)) continue;
    const perShare = price * (1 + 0.05 * (1 - price));   // price + its taker fee
    const want = cash / perShare;
    const take = Math.min(want, avail);
    if (take <= 0) continue;
    shares += take;
    notional += take * price;
    cash -= take * perShare;
  }

  shares = floorTo(shares, limits.shareStep);
  if (shares <= 0) {
    return empty("The book cannot absorb even one order at this size.", [...problems, "nothing_fillable"]);
  }

  const avgPrice = notional > 0 ? notional / (shares || 1) : null;
  const topPrice = levels[0][0];
  const fee = avgPrice != null ? takerFee(shares, avgPrice) : 0;
  const spent = round(shares * (avgPrice ?? topPrice) + fee, 0.01);

  if (spent < limits.minOrderUsd - 1e-9) {
    return empty(
      `Only $${spent.toFixed(2)} of that could fill, which is under the venue's $${limits.minOrderUsd.toFixed(2)} minimum — the order would be rejected.`,
      [...problems, "below_minimum", "partially_filled"]
    );
  }

  const shortfall = usd - spent;
  const complete = shortfall <= Math.max(0.02, usd * 0.005);
  if (!complete) problems.push("partially_filled");

  const payout = shares;                       // a share settles at $1
  const profit = payout - spent;
  const ev =
    modelProb == null || !Number.isFinite(modelProb)
      ? null
      : modelProb * profit - (1 - modelProb) * spent;

  const bits: string[] = [];
  bits.push(
    `${shares.toFixed(2)} shares at an average ${(100 * (avgPrice ?? topPrice)).toFixed(1)}¢`
  );
  if (avgPrice != null && avgPrice - topPrice > 0.001) {
    bits.push(`${((avgPrice - topPrice) * 100).toFixed(1)}¢ worse than the top of book`);
  }
  if (!complete) {
    bits.push(`only $${spent.toFixed(2)} of $${usd.toFixed(2)} fills — the book runs out`);
  }
  if (!bookKnown) {
    bits.push("no stored ladder, so this is priced off top-of-book and the depth total");
  }

  return {
    requested: usd, spent, shares,
    avgPrice, topPrice,
    slippage: avgPrice != null ? avgPrice - topPrice : null,
    fee, payout, profit,
    ev, roi: spent > 0 ? profit / spent : null,
    complete, problems,
    note: bits.join(" · "),
  };
}

/**
 * The largest stake that still fills completely, to the nearest cent.
 *
 * This is the number a desk actually wants: not "how deep is the book" in the
 * abstract, but "what is the biggest ticket I can place here without eating
 * into a worse price or getting a partial".
 */
export function maxCleanStake(levels: Level[], limits: Limits = DEFAULT_LIMITS): number {
  let usd = 0;
  for (const [price, avail] of levels) {
    if (!(price > 0 && price < 1) || !Number.isFinite(avail)) {
      return Infinity;                        // unbounded level - no useful cap
    }
    usd += avail * price * (1 + 0.05 * (1 - price));
  }
  return Math.max(0, Math.floor(usd * 100) / 100);
}

/**
 * Round a desired leg to something placeable: at least the minimum, on the
 * share step. Returns null when the leg cannot be made placeable at all.
 *
 * Used where a solver produces an ideal continuous plan - the Goals spread -
 * and the plan then has to become an order ticket.
 */
export function snapLeg(usd: number, limits: Limits = DEFAULT_LIMITS): number | null {
  if (!Number.isFinite(usd) || usd <= 0) return null;
  if (usd < limits.minOrderUsd) return null;
  return Math.round(usd * 100) / 100;
}
