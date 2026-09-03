/**
 * What a trade actually costs. Ported from scripts/cost_model.py, which is
 * the desk's single source of truth and is unit-tested in
 * tests/test_cost_model.py.
 *
 *     fee = shares × rate × p × (1 − p),  rate = 0.05
 *
 * SOURCED: Polymarket weather markets, fee schedule effective 30 March 2026.
 * TAKER ONLY - makers pay nothing and earn a rebate. Settlement is free, which
 * the formula gives for nothing: it goes to zero at both p=0 and p=1.
 *
 * The shape matters and a flat percentage does not have it. The fee peaks at
 * 1.25% when p = 0.50 and falls away toward both extremes, so a 90c favourite
 * costs 0.45% to take while a coin-flip costs nearly three times that. Any
 * page quoting a flat rate would overstate the cost of the cheap tails and
 * understate nothing - which is exactly the direction that makes a bad trade
 * look survivable.
 *
 * Every figure this module returns is NET. That is the house rule.
 */

export const TAKER_RATE = 0.05;
export const MAKER_REBATE = 0.25;
export const GAS_USD = 0.01;

/** Polymarket weather taker fee, in dollars. Peaks at p = 0.50. */
export function takerFee(shares: number, price: number, rate = TAKER_RATE): number {
  return shares * rate * price * (1 - price);
}

/** Makers pay nothing - which is why AD4 defaults to resting limit orders. */
export function makerFee(): number {
  return 0;
}

export interface Position {
  /** Dollars committed at entry, fee included. */
  cost: number;
  shares: number;
  fee: number;
  /** Dollars back if this band settles YES: one dollar per share. */
  payout: number;
  /** payout − cost. */
  profit: number;
  /** −cost. Losing is total: the band settles at zero. */
  loss: number;
  /** p × profit − (1 − p) × cost, at whatever probability is supplied. */
  ev: number | null;
  /** profit / cost. */
  roi: number;
}

/**
 * Buy `usd` worth at `price`, as a taker.
 *
 * `usd` is what leaves the account: the fee comes out of it rather than being
 * added on top, so "risk $100" means exactly $100 at risk. Solving
 * usd = shares × price + shares × rate × price × (1 − price) gives
 * shares = usd / (price × (1 + rate × (1 − price))).
 */
export function buy(usd: number, price: number, modelProb?: number | null, isTaker = true): Position | null {
  if (!Number.isFinite(usd) || usd <= 0) return null;
  if (!Number.isFinite(price) || price <= 0 || price >= 1) return null;

  const perShare = isTaker ? price * (1 + TAKER_RATE * (1 - price)) : price;
  const shares = usd / perShare;
  const fee = isTaker ? takerFee(shares, price) : makerFee();
  const payout = shares;                 // each share settles at $1
  const profit = payout - usd;
  const ev =
    modelProb === null || modelProb === undefined || !Number.isFinite(modelProb)
      ? null
      : modelProb * profit - (1 - modelProb) * usd;

  return { cost: usd, shares, fee, payout, profit, loss: -usd, ev, roi: profit / usd };
}

/** The fee as a fraction of notional, for showing what the trade costs to take. */
export function feeRateAt(price: number, rate = TAKER_RATE): number {
  if (!Number.isFinite(price) || price <= 0 || price >= 1) return 0;
  return rate * (1 - price);
}
