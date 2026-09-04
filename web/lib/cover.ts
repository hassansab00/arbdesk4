// The two-bucket cover, in the browser.
//
// This is a PORT of scripts/strategies/s8_two_bucket_cover.py, and a port is
// worth nothing unless it agrees with the original. The page and the signal
// engine must call the same pair live or dead - a desk that sees "cover pair
// live" on screen and gets no signal has learned to distrust both.
// tests/test_strategies.py runs the two on the same inputs and compares.
//
// The fee is the part most likely to drift. Polymarket charges
// shares x 0.05 x p x (1-p), which peaks at 1.25% near 50c and falls to
// nothing at both extremes - so two mid-priced buckets carry meaningfully more
// fee than a cheap/expensive pair at the same total, and a flat percentage
// would quietly turn a 70c rule into a 72c one.

export const FEE_RATE = 0.05;
export const MAX_PAIR_COST = 0.70;
export const MIN_PAIR_PROB = 0.72;

export interface CoverLeg {
  band_id: string;
  band_label: string | null;
  band_lo: number | null;
  band_hi: number | null;
  model_prob: number | null;
  market_price: number | null;
}

export interface Cover {
  ids: Set<string>;
  labels: (string | null)[];
  adjacent: boolean;
  cost: number;
  fee: number;
  total: number;
  prob: number;
  qualifies: boolean;
  returnPct: number | null;
  /** Why not, when it does not qualify - so the UI never just says "no". */
  blockedBy: "not_adjacent" | "too_expensive" | "not_worth_it" | null;
}

export function pairFee(a: number, b: number): number {
  return FEE_RATE * a * (1 - a) + FEE_RATE * b * (1 - b);
}

/** Neighbours on the ladder. */
export function adjacent(a: CoverLeg, b: CoverLeg): boolean {
  if (a.band_hi != null && b.band_lo != null && Math.abs(a.band_hi - b.band_lo) < 1e-6) return true;
  if (b.band_hi != null && a.band_lo != null && Math.abs(b.band_hi - a.band_lo) < 1e-6) return true;
  return false;
}

export function findCover(
  rows: CoverLeg[],
  maxCost = MAX_PAIR_COST,
  minProb = MIN_PAIR_PROB
): Cover | null {
  const priced = rows.filter((r) => r.model_prob != null && r.market_price != null);
  if (priced.length < 2) return null;

  const [p, q] = [...priced].sort((x, y) => (y.model_prob ?? 0) - (x.model_prob ?? 0)).slice(0, 2);
  const isAdjacent = adjacent(p, q);
  const cost = (p.market_price ?? 0) + (q.market_price ?? 0);
  const fee = pairFee(p.market_price ?? 0, q.market_price ?? 0);
  const total = cost + fee;
  const prob = (p.model_prob ?? 0) + (q.model_prob ?? 0);

  const blockedBy = !isAdjacent
    ? ("not_adjacent" as const)
    : total >= maxCost
      ? ("too_expensive" as const)
      : prob < minProb || prob <= total
        ? ("not_worth_it" as const)
        : null;

  return {
    ids: new Set([p.band_id, q.band_id]),
    labels: [p.band_label, q.band_label],
    adjacent: isAdjacent,
    cost, fee, total, prob,
    qualifies: blockedBy === null,
    returnPct: total > 0 ? ((1 - total) / total) * 100 : null,
    blockedBy,
  };
}
