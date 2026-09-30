/**
 * THE PREDICTIVE PAGE'S TABLES, AS PURE FUNCTIONS (handoff 30 Sep, F2).
 *
 * Measured 30 Sep, as anon: "Hit rate, per city, per lead" rendered the first
 * 120 of 797 rows of an UNORDERED query - 6 C cities and 2 of 11 US cities -
 * with no warning; "What the desk expects" rendered 60 of 96 open city-days;
 * the lean-per-city chart showed 8 cities without saying it was a cut. Each
 * table here is built from ordered rows, keeps every city reachable, and says
 * how many rows it holds and why a city has none.
 */

/* ---------------------------------------------------------------- forward */

export interface ForwardLadderRow {
  city_key: string; for_date: string; band_id: string; band_index: number | null;
  band_label: string | null; closed: boolean | null; side: string | null;
  model_prob: number | null; market_price: number | null;
  forecast_max_c: number | null; centre_c: number | null; sigma_c: number | null;
  edge_net_pp: number | null; tradeable: boolean | null;
}

export interface ForwardRow {
  city_key: string; for_date: string;
  n_bands: number; n_priced: number;
  /** the sum of the ladder's YES probabilities; a valid ladder sums to 1 */
  prob_sum: number | null;
  /** fewer bands priced than the ladder holds, or a sum away from 1 */
  incomplete: boolean;
  centre_c: number | null; forecast_max_c: number | null; sigma_c: number | null;
  /** the bucket the published distribution puts most weight on - not the bucket holding the centre */
  best_band: string | null; best_prob: number | null; best_price: number | null;
  top_edge_pp: number | null; top_edge_band: string | null;
}

/** A ladder summing further than this from 1 is flagged, not silently shown. */
export const PROB_SUM_TOLERANCE = 0.02;

const byBand = (a: ForwardLadderRow, b: ForwardLadderRow) =>
  (a.band_index ?? 1e9) - (b.band_index ?? 1e9) || a.band_id.localeCompare(b.band_id);

export function forwardRows(ladder: ForwardLadderRow[], today: string): ForwardRow[] {
  const byCityDay = new Map<string, ForwardLadderRow[]>();
  for (const r of ladder) {
    if (r.for_date < today || r.closed) continue;
    const k = `${r.city_key}|${r.for_date}`;
    if (!byCityDay.has(k)) byCityDay.set(k, []);
    byCityDay.get(k)!.push(r);
  }
  const out: ForwardRow[] = [];
  for (const [k, rows] of Array.from(byCityDay.entries())) {
    const [city_key, for_date] = k.split("|");
    // model_prob is the YES probability on both rows of a band, and
    // market_price is the price of the row's OWN side: only the YES row's
    // price is what the market charges for the bucket.
    const yes = rows.filter((r) => r.side === "YES").sort(byBand);
    const bands = new Set(rows.map((r) => r.band_id));
    const priced = yes.filter((r) => r.model_prob !== null);
    // Highest probability first; a tie goes to the lower band, so the call
    // does not depend on the order rows arrived in.
    const best = priced.slice().sort((a, b) => (b.model_prob! - a.model_prob!) || byBand(a, b))[0];
    const edges = rows.filter((r) => r.edge_net_pp !== null && r.tradeable === true)
      .sort((a, b) => (b.edge_net_pp! - a.edge_net_pp!) || byBand(a, b) || (a.side ?? "").localeCompare(b.side ?? ""));
    const sum = priced.length ? priced.reduce((s, r) => s + (r.model_prob as number), 0) : null;
    out.push({
      city_key, for_date, n_bands: bands.size, n_priced: priced.length, prob_sum: sum,
      incomplete: priced.length > 0 && (priced.length < bands.size
        || Math.abs((sum as number) - 1) > PROB_SUM_TOLERANCE),
      centre_c: best?.centre_c ?? null, forecast_max_c: best?.forecast_max_c ?? null,
      sigma_c: best?.sigma_c ?? null,
      best_band: best?.band_label ?? null, best_prob: best?.model_prob ?? null,
      best_price: best?.market_price ?? null,
      top_edge_pp: edges[0]?.edge_net_pp ?? null, top_edge_band: edges[0]?.band_label ?? null,
    });
  }
  // Soonest day first; inside a day the largest tradeable edge, then the city,
  // so equal edges (and the unpriced) keep one order.
  return out.sort((a, b) => a.for_date.localeCompare(b.for_date)
    || (b.top_edge_pp ?? -99) - (a.top_edge_pp ?? -99)
    || a.city_key.localeCompare(b.city_key));
}

/* -------------------------------------------------------------- scorecard */

export interface ScorecardRow {
  city_key: string; model: string; lead_days: number; n_days: number;
  mae_c: number; bias_c: number; error_sd_c: number | null; worst_c: number;
  hit_rate_pct: number | null; within_1c_pct: number | null;
}

export interface ScorecardGroup { city_key: string; rows: ScorecardRow[] }

/**
 * Every active city, in roster order: its rows by model and lead, or - when it
 * has none - named in `missing`, because a city with no row has fewer than the
 * view's minimum settled days per model and lead, which is not a zero score.
 * `only` restricts to one city ("" = all).
 */
export function groupScorecard(rows: ScorecardRow[], roster: string[], only = "") {
  const want = only ? roster.filter((c) => c === only) : roster;
  const by = new Map<string, ScorecardRow[]>();
  for (const r of rows) {
    if (!by.has(r.city_key)) by.set(r.city_key, []);
    by.get(r.city_key)!.push(r);
  }
  const groups: ScorecardGroup[] = [];
  const missing: string[] = [];
  for (const c of want) {
    const rs = (by.get(c) ?? []).slice()
      .sort((a, b) => a.model.localeCompare(b.model) || a.lead_days - b.lead_days);
    if (rs.length) groups.push({ city_key: c, rows: rs });
    else missing.push(c);
  }
  const shownRows = groups.reduce((n, g) => n + g.rows.length, 0);
  return { groups, missing, shownRows, totalRows: rows.length,
           citiesWithRows: new Set(rows.map((r) => r.city_key)).size };
}

/* -------------------------------------------------- pending settled days */

/** Milliseconds `tz`'s wall clock is ahead of UTC at instant `t`. */
export function tzOffsetMs(t: number, tz: string): number {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: tz, hourCycle: "h23", year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", second: "2-digit",
  }).formatToParts(new Date(t));
  const get = (type: string) => Number(parts.find((p) => p.type === type)?.value);
  const wall = Date.UTC(get("year"), get("month") - 1, get("day"), get("hour"), get("minute"), get("second"));
  return wall - Math.floor(t / 1000) * 1000;
}

/** The instant the city's local day `date` (YYYY-MM-DD) ends, in ms since epoch. */
export function localDayEnd(date: string, tz: string): number {
  const [y, m, d] = date.split("-").map(Number);
  const wallMidnight = Date.UTC(y, m - 1, d + 1, 0, 0, 0);
  let t = wallMidnight - tzOffsetMs(wallMidnight, tz);
  t = wallMidnight - tzOffsetMs(t, tz);        // once more, across a DST change
  return t;
}

export interface MarketDay { resolution_date: string; resolution_verified_at: string | null }
export type PendingState = "day_not_ended" | "awaiting_venue" | "confirmed_not_scored";
export interface PendingDay { date: string; state: PendingState; day_ended_at: number }

/**
 * The city's recent days that are NOT in its hit/miss record, and why:
 * its local day has not ended; the venue has not confirmed the whole ladder
 * yet; or it is confirmed and waiting for the record (banking and the page
 * refresh run in the intraday pipeline). A day missing from the record is
 * pending, never a miss.
 */
export function pendingDays(markets: MarketDay[], recorded: Set<string>, tz: string | null,
                            now: number): PendingDay[] {
  if (!tz) return [];
  return markets
    .filter((m) => !recorded.has(m.resolution_date))
    .map((m) => {
      const end = localDayEnd(m.resolution_date, tz);
      const state: PendingState = end > now ? "day_not_ended"
        : m.resolution_verified_at ? "confirmed_not_scored" : "awaiting_venue";
      return { date: m.resolution_date, state, day_ended_at: end };
    })
    .filter((p) => p.state !== "day_not_ended")
    .sort((a, b) => b.date.localeCompare(a.date));
}

/* ------------------------------------------------------- the bias chart */

/** The k cities with the largest absolute lean, and how many there are in all. */
export function largestLeans<T extends { bias: number; city: string }>(byCity: T[], k: number) {
  const sorted = byCity.slice().sort((a, b) => Math.abs(b.bias) - Math.abs(a.bias) || a.city.localeCompare(b.city));
  return { shown: k > 0 ? sorted.slice(0, k) : sorted, total: sorted.length };
}
