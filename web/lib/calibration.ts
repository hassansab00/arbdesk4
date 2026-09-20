/**
 * The measured haircut: what the model's stated probability has actually meant.
 *
 * WHY THIS IS NOT THE CALIBRATION MAP. scripts/calibration.py fits a map and
 * the probability engine applies it to every band before anything is priced -
 * that changes the number. This changes nothing upstream. It sits at the point
 * of decision and answers "the card says 34%; what has 34% actually done?",
 * using v_probability_reliability, which measures realised-against-stated on
 * raw_prob over settled bands.
 *
 * THE RULE HAS NO TUNING CONSTANT. Each decile carries a Wilson 95% interval on
 * its realised rate. The adjustment moves the stated probability only as far as
 * the NEAREST BOUND of that interval, so:
 *
 *   - if the stated value is already inside the interval, nothing moves, because
 *     the data cannot tell it apart from the truth;
 *   - if it is outside, the move stops at the edge of what the data supports,
 *     never at the point estimate;
 *   - as n grows the interval narrows and the adjustment approaches the full
 *     measured gap on its own.
 *
 * Measured 2026-09-19 on 3,573 settled bands over 21 settlement dates, the gaps
 * run +0.7pp at 0-10% through -9.1pp at 40-50% - monotone, and pointing the way
 * a model too sure of the middle of its own distribution would point - and not
 * one decile's gap clears its own interval. So today this applies nothing and
 * `reason` says why. That is the honest state of the evidence, not a bug: the
 * desk trades the 20-50% region, which is exactly where the suggested
 * overstatement is 3 to 9 points, and 126 observations is not enough to act on
 * a 6.8pp gap.
 */

export interface ReliabilityBucket {
  bucket: number;
  bucket_lo: number;
  bucket_hi: number;
  n: number;
  mean_stated: number;
  realised: number;
  gap: number;
  wilson_lo: number;
  wilson_hi: number;
  adjusted: number;
  /** What to ADD to a probability in this decile. Zero unless `applies`. */
  shift: number;
  applies: boolean;
  note: string;
}

export interface Haircut {
  /** The probability to price with. Equals `stated` when nothing applies. */
  p: number;
  /** p - stated, in probability units. 0 when nothing applies. */
  shift: number;
  applied: boolean;
  /** The decile this probability fell in, or null when there is no row. */
  bucket: ReliabilityBucket | null;
  /** One sentence, always present, for the tooltip. */
  reason: string;
}

const clamp01 = (x: number) => Math.min(0.999, Math.max(0.001, x));

/** The decile row covering `p`, or null. */
export function bucketFor(
  p: number | null | undefined,
  curve: ReliabilityBucket[] | null | undefined
): ReliabilityBucket | null {
  if (p == null || !Number.isFinite(p) || !curve || !curve.length) return null;
  const i = Math.min(9, Math.max(0, Math.floor(p * 10)));
  return curve.find((b) => b.bucket === i) ?? null;
}

/**
 * Apply the measured haircut to a stated probability.
 *
 * Returns the input unchanged, with a reason, whenever there is no curve, no
 * row for this decile, or the decile's own interval cannot distinguish the
 * stated value from what happened. Never throws, never invents a bucket.
 */
export function haircut(
  stated: number | null | undefined,
  curve: ReliabilityBucket[] | null | undefined
): Haircut {
  if (stated == null || !Number.isFinite(stated)) {
    return { p: NaN, shift: 0, applied: false, bucket: null,
             reason: "no model probability on this row" };
  }
  const b = bucketFor(stated, curve);
  if (!b) {
    return { p: stated, shift: 0, applied: false, bucket: null,
             reason: curve && curve.length
               ? "no settled bands yet in this probability range"
               : "reliability has not been measured yet — no settled bands" };
  }
  if (!b.applies) {
    return { p: stated, shift: 0, applied: false, bucket: b, reason: b.note };
  }
  const p = clamp01(stated + b.shift);
  return { p, shift: p - stated, applied: true, bucket: b, reason: b.note };
}

/**
 * Edge after the haircut, in probability units.
 *
 * Edge is stated probability minus what you pay, so a haircut on the
 * probability moves the edge by exactly the same amount. Returns null rather
 * than 0 when there is no edge to adjust - a missing edge and a zero edge are
 * different facts and the card shows them differently.
 */
export function haircutEdge(
  edge: number | null | undefined,
  h: Haircut
): number | null {
  if (edge == null || !Number.isFinite(edge)) return null;
  return edge + h.shift;
}

/**
 * Expected value of a fill at a given probability.
 *
 * p x (what you win) - (1 - p) x (what you staked). Mirrors execution.ts's own
 * formula deliberately: the EV on the card and the EV used for ranking have to
 * be the same arithmetic, or the list is sorted by a number the card never
 * shows.
 */
export function evAt(
  p: number | null | undefined,
  profit: number,
  spent: number
): number | null {
  if (p == null || !Number.isFinite(p)) return null;
  return p * profit - (1 - p) * spent;
}
