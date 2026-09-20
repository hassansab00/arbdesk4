/**
 * One priced opportunity: the fill you would get, and what it is worth.
 *
 * THIS EXISTS SO THE RANK AND THE CARD CANNOT DISAGREE. The money on a card was
 * computed inside the card, and the list was ordered by a server-side `score` -
 * edge x confidence x ln(1 + depth) - which is a proxy for value, not value. So
 * the top card was not the best trade; it was the best-looking one. Two numbers,
 * two places, two meanings. Everything below is computed once, per row, and the
 * SAME object is both sorted on and rendered.
 *
 * `score` is still read and still shown, because it answers a different
 * question - "what is most mispriced" - and it does not depend on a stake. Rank
 * answers "what should I do with this $100", which does.
 */
import { fill, ladderFor, maxCleanStake, parseLevels,
         type BookRow, type Fill, type Level, type Limits } from "./execution.ts";
import { haircut, haircutEdge, evAt,
         type Haircut, type ReliabilityBucket } from "./calibration.ts";

/** Only what pricing needs. Structural, so the page's TradePlan satisfies it. */
export interface PriceableRow {
  side: string;
  model_prob: number | null;
  edge_net_pp: number | null;
  best_ask: number | null;
  best_bid: number | null;
  fillable_usd_5c: number | null;
}

export interface PricedRow {
  /** The ladder walk at the requested stake. */
  pos: Fill;
  levels: Level[];
  bookKnown: boolean;
  /** Largest stake that still fills completely. */
  cleanMax: number;
  /** What you make if the band happens. null when nothing fills. */
  ifRight: number | null;
  /**
   * What you lose if it does not. A losing binary settles at zero, so this is
   * the whole fill - fee included, because the fee is taken out of the money
   * committed. It is NOT the requested stake: a $500 ticket against a $120 book
   * risks $120.
   */
  ifWrong: number | null;
  /** EV at the model's own probability, unadjusted. */
  evRaw: number | null;
  /** EV after the measured haircut. Equals evRaw when nothing applies. */
  ev: number | null;
  /** Net edge after the haircut, in probability units. */
  edge: number | null;
  /** The haircut itself, so the card can explain the number it just showed. */
  cal: Haircut;
}

/**
 * Price one row at one stake.
 *
 * `curve` may be null or empty - then the haircut applies nothing and says so,
 * and every figure is the raw one. That is the state the desk is in until
 * enough bands have settled, and it must look like a stated fact rather than
 * like a missing feature.
 */
export function priceRow(
  o: PriceableRow,
  book: BookRow | undefined,
  limits: Limits,
  stake: number,
  curve: ReliabilityBucket[] | null | undefined
): PricedRow {
  const side = o.side === "YES" ? "ask" : "bid";
  const rawLevels = parseLevels(side === "ask" ? book?.ask_levels : book?.bid_levels,
                                o.side === "YES" ? "YES" : "NO");
  const quoted = o.side === "YES" ? o.best_ask : (o.best_bid != null ? 1 - o.best_bid : null);
  const depthUsd = o.side === "YES"
    ? (book?.ask_depth_usd ?? o.fillable_usd_5c)
    : (book?.bid_depth_usd ?? o.fillable_usd_5c);
  const { levels, known: bookKnown } = ladderFor(rawLevels, quoted, depthUsd, limits);

  const cal = haircut(o.model_prob, curve);
  // The fill is walked at the RAW probability so pos.ev keeps its documented
  // meaning; the calibrated EV is computed beside it from the same fill.
  const pos = fill(stake, levels, bookKnown, o.model_prob, limits);

  const filled = pos.shares > 0;
  const ifRight = filled ? pos.profit : null;
  const ifWrong = filled ? -pos.spent : null;

  return {
    pos, levels, bookKnown,
    cleanMax: levels.length ? maxCleanStake(levels, limits) : 0,
    ifRight, ifWrong,
    evRaw: pos.ev,
    ev: filled ? evAt(cal.p, pos.profit, pos.spent) : null,
    edge: haircutEdge(o.edge_net_pp, cal),
    cal,
  };
}

/**
 * Order by what the trade is actually worth, best first.
 *
 * A row that cannot fill has no EV and sorts last whatever its edge says - that
 * is the point of ranking on this rather than on a score. Ties, and rows with
 * no probability at all, fall back to the server's score so the order stays
 * stable rather than arbitrary.
 */
export function byExpectedValue<T extends { ev: number | null; score: number | null }>(
  a: T, b: T
): number {
  const ae = a.ev, be = b.ev;
  if (ae == null && be == null) return (b.score ?? 0) - (a.score ?? 0);
  if (ae == null) return 1;
  if (be == null) return -1;
  if (be !== ae) return be - ae;
  return (b.score ?? 0) - (a.score ?? 0);
}
