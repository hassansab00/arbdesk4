/**
 * WHY EACH DAY HIT OR MISSED (Hassan, 12 Oct: "each day needs to teach the
 * model outcomes and data points on why this weather hit/miss based on what").
 *
 * derived_day_review holds one row a settled city-day, written each night by
 * tools/wxpredict/day_review.py: the winning bucket, the station's maximum and
 * when it came, the day-ahead forecast's error, what the afternoon's weather
 * did against what the forecast said, and at each checkpoint the platform's,
 * wxrd-v2's and the market's top bucket and whether it won. The city popup
 * shows the newest days.
 *
 * Before the peak is what counts (Hassan, 12 Oct: "after peak its almost
 * decided"), so the tally leaves the after-peak checkpoint out.
 */
import { MOMENTS } from "./focus";

export const DAY_REVIEW_SELECT = "city_key,target_date,unit,winner_label,station_max_c,peak_local_hour,usual_peak_hour,forecast_max_c,forecast_error_c,closest_model,calls,why";
export const DAY_REVIEW_DAYS = 7;

export type Who = "engine" | "wxrd" | "market";
export const WHO: Array<{ key: Who; label: string; short: string }> = [
  { key: "engine", label: "Platform", short: "P" },
  { key: "wxrd", label: "wxrd-v2 (shadow)", short: "W" },
  { key: "market", label: "Market", short: "M" },
];

export type DayCall = {
  checkpoint: string;
  engine_top: string | null; engine_label?: string | null; engine_hit: boolean | null;
  engine_p_win?: number | string | null;
  wxrd_top: string | null; wxrd_label?: string | null; wxrd_hit: boolean | null;
  wxrd_p_win?: number | string | null;
  market_top: string | null; market_label?: string | null; market_hit: boolean | null;
  market_p_win?: number | string | null;
};

export type DayReviewRow = {
  city_key: string; target_date: string; unit: string | null;
  winner_label: string | null;
  station_max_c: number | string | null;
  peak_local_hour: number | string | null; usual_peak_hour: number | string | null;
  forecast_max_c: number | string | null; forecast_error_c: number | string | null;
  closest_model: string | null;
  calls: DayCall[] | null;
  why: string | null;
};

const AFTER_PEAK = new Set(MOMENTS.filter((m) => m.afterPeak).map((m) => m.key));
export const isBeforePeak = (checkpoint: string) => !AFTER_PEAK.has(checkpoint);

export type Tally = Record<Who, { hits: number; n: number }>;

/** Hits and settled calls per source, over the checkpoints before the peak.
 *  A call with no outcome (no row, or no winner yet) is not counted. */
export function beforePeakTally(calls: DayCall[] | null | undefined): Tally {
  const out: Tally = { engine: { hits: 0, n: 0 }, wxrd: { hits: 0, n: 0 }, market: { hits: 0, n: 0 } };
  for (const c of calls ?? []) {
    if (!isBeforePeak(c.checkpoint)) continue;
    for (const { key } of WHO) {
      const hit = c[`${key}_hit`];
      if (hit === null || hit === undefined) continue;
      out[key].n += 1;
      if (hit) out[key].hits += 1;
    }
  }
  return out;
}

/** The same, summed over days. */
export function sumTallies(rows: DayReviewRow[]): Tally {
  const out: Tally = { engine: { hits: 0, n: 0 }, wxrd: { hits: 0, n: 0 }, market: { hits: 0, n: 0 } };
  for (const r of rows) {
    const t = beforePeakTally(r.calls);
    for (const { key } of WHO) { out[key].hits += t[key].hits; out[key].n += t[key].n; }
  }
  return out;
}

/** The checkpoints any of the days has, in the day's order. */
export function checkpointsOf(rows: DayReviewRow[]): string[] {
  const seen = new Set(rows.flatMap((r) => (r.calls ?? []).map((c) => c.checkpoint)));
  const order = MOMENTS.map((m) => m.key);
  return Array.from(seen).sort((a, b) => {
    const i = order.indexOf(a), j = order.indexOf(b);
    return (i < 0 ? 99 : i) - (j < 0 ? 99 : j);
  });
}
