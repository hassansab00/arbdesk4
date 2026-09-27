import type { Unit } from "@/lib/units";

/**
 * THE CITY CARDS' RULES (web/components/CityCards.tsx), kept free of React
 * and Supabase so web/tests/city-cards.test.cjs can run them.
 */

export interface CityRow { city_key: string; display_name: string | null; unit: string | null }
export interface LadderRow {
  city_key: string; for_date: string; band_id: string; band_index: number | null;
  band_label: string | null; side: string | null; model_prob: number | null;
  forecast_max_c: number | null; sigma_c: number | null; confidence: number | null;
  regime_label: string | null; market_price: number | null; edge_net_pp: number | null;
  depth_5c: number | null; tradeable: boolean | null; block_reason: string | null;
  edge_at: string | null;
  /** The bucket's edges in the market's own unit, half-open [lo, hi). */
  band_lo?: number | null; band_hi?: number | null; open_low?: boolean | null; open_high?: boolean | null;
  /** From the SAME band_probabilities row as model_prob (the card's plan v2.3 P4.8):
   *  centre_c the centre the ladder was integrated on, after every correction;
   *  observed_floor_c the day's maximum the price already knew; prob_at when it
   *  was priced; priced_from the engine's label for the path that priced it. */
  centre_c?: number | null; forecast_sigma_c?: number | null; observed_floor_c?: number | null;
  prob_at?: string | null; priced_from?: string | null;
}
export interface ForecastRow {
  city_key: string; for_date: string; model: string; lead_days: number;
  forecast_max_c: number | null; run_at: string | null;
}
export interface OwnModelRow {
  city_key: string; for_date: string; lead_days: number | null;
  predicted_max_c: number | null; nws_max_c: number | null; promotion_state: string | null;
}
export interface LiveRow {
  city_key: string; local_date: string | null; temp_c: number | null; running_max_c: number | null;
  peak_window_state: string | null; day_decided: boolean | null; observed_at: string | null;
  source_kind: string | null;
}

/** One settled day-ahead call, from v_city_hit_history. */
export interface HitRow {
  unit: string | null; model_call: string | null; market_call: string | null;
  model_hit: boolean | null; market_hit: boolean | null; head_to_head: boolean | null;
}

/** When the engine's pick and the market's pick differed, who won - per unit. */
export interface DisagreementRecord { days: number; engine_won: number; market_won: number }

export function disagreementRecord(rows: HitRow[]): Record<string, DisagreementRecord> {
  const out: Record<string, DisagreementRecord> = {};
  for (const r of rows) {
    if (!r.head_to_head || !r.model_call || !r.market_call || r.model_call === r.market_call) continue;
    const u = r.unit === "F" ? "F" : "C";
    const rec = (out[u] ??= { days: 0, engine_won: 0, market_won: 0 });
    rec.days += 1;
    if (r.model_hit) rec.engine_won += 1;
    if (r.market_hit) rec.market_won += 1;
  }
  return out;
}

/** Public forecasts this far apart (in C) mean the day is genuinely uncertain. */
export const FORECASTS_DISAGREE_C = 1.5;

/**
 * The whole degree the venue reads for a value already in the market's unit:
 * half away from zero, cut to six places first. The twin of
 * scripts/venue.py venue_read and sql/ad4_82 venue_round().
 */
export function venueRead(valueLocal: number): number {
  const v = Number(valueLocal.toFixed(6));
  return Math.sign(v) * Math.floor(Math.abs(v) + 0.5);
}

export interface BandBounds {
  band_id: string; band_lo?: number | null; band_hi?: number | null;
  open_low?: boolean | null; open_high?: boolean | null;
}

/** The ladder in temperature order: open low tail, closed buckets, open high
 *  tail (probability_engine._ladder). */
function temperatureOrder(bands: BandBounds[]): BandBounds[] {
  const key = (b: BandBounds): [number, number] =>
    b.open_low ? [0, -Infinity] : b.open_high ? [2, Number(b.band_lo)] : [1, Number(b.band_lo)];
  return bands.slice().sort((a, b) => {
    const [ka, va] = key(a); const [kb, vb] = key(b);
    return ka - kb || va - vb;
  });
}

function holds(b: BandBounds, r: number): boolean {
  if (b.open_low) return b.band_hi != null && r < Number(b.band_hi);
  if (b.open_high) return b.band_lo != null && r >= Number(b.band_lo);
  return b.band_lo != null && b.band_hi != null && Number(b.band_lo) <= r && r < Number(b.band_hi);
}

/**
 * Where the day's maximum leaves a bucket, by the engine's own rule (plan v2
 * P3.1: probability_engine.floor_bucket / impossible_band_ids):
 *   "open"        the reading has not passed it;
 *   "one_below"   the reading's bucket is the next one up - it can still
 *                 settle only if the venue reads the day a bucket lower than
 *                 the station did;
 *   "impossible"  the reading is two or more buckets above it.
 * null when the reading or the ladder cannot say (no bucket holds it).
 */
export type PickStanding = "open" | "one_below" | "impossible";
export function pickStanding(maxC: number | null | undefined, unit: Unit, bands: BandBounds[],
                             bandId: string): PickStanding | null {
  if (maxC == null || !Number.isFinite(maxC)) return null;
  const ladder = temperatureOrder(bands);
  const r = venueRead(unit === "F" ? maxC * 9 / 5 + 32 : maxC);
  const i = ladder.findIndex((b) => holds(b, r));
  const p = ladder.findIndex((b) => b.band_id === bandId);
  if (i < 0 || p < 0) return null;
  return p >= i ? "open" : p === i - 1 ? "one_below" : "impossible";
}

const STANDING_RANK: Record<PickStanding, number> = { open: 0, one_below: 1, impossible: 2 };

/**
 * The engine's label for the path that priced a ladder (probability_engine.
 * forecast_provenance), in words. The label is kept on the card too, so the
 * words never stand in for the record.
 */
export function pricedFromWords(label: string | null | undefined): string | null {
  if (!label) return null;
  const parts: string[] = [];
  let rest = label;
  if (rest.startsWith("trajectory:")) {
    parts.push("the day so far (trajectory)");
    rest = rest.replace(/^trajectory:(\d{2}h:)?/, "");
  }
  if (rest.startsWith("station_correction:")) {
    parts.push(rest.includes("+station-mos:")
      ? "forecast models corrected at the station, blended with the station model"
      : "forecast models corrected at the station");
  } else if (rest.startsWith("model:")) {
    parts.push("the desk's promoted weather model");
  } else {
    parts.push(`the public forecast (${rest.split(":")[0]}), bias-corrected`);
  }
  return parts.join(", over ");
}

export interface CityCard {
  city_key: string; name: string; unit: Unit; for_date: string;
  /** The centre the ladder was integrated on (band_probabilities.centre_c), after
   *  every correction. null when the row does not record one: never the raw
   *  forecast in its place (Wuhan 28 Sep: raw 28.9 C, priced on 26.005 C). */
  centre_c: number | null;
  /** The public forecast the engine started from, before any correction. */
  raw_forecast_c: number | null;
  /** The day's maximum the price already counted (band_probabilities.observed_floor_c). */
  priced_floor_c: number | null;
  sigma_c: number | null; confidence: number | null; regime: string | null;
  top_band: string | null; top_prob: number | null; top_yes_price: number | null;
  /** The bucket the market prices highest, and that price (the YES row). */
  market_band: string | null; market_price: number | null;
  /** The engine's favourite and the market's are different buckets. */
  disagrees: boolean;
  /** The engine's favourite is a bucket the market prices under 5c, or one the edge engine calls dead. */
  favourite_dead: boolean;
  /** Spread between the public forecasts for this day, in C; null with fewer than two. */
  forecast_spread_c: number | null;
  /** The engine's centre lies outside every public forecast for this day (by more than 0.5 C). */
  centre_outside_forecasts: boolean;
  best: { side: string; band: string | null; edge: number; price: number | null; depth: number | null;
          /** a bet on the engine's view against the market's favourite */
          against_market: boolean } | null;
  blocked: string | null;
  /** When the probabilities were computed (band_probabilities.computed_at), and
   *  when the edges beside them were. Two jobs, two times. */
  priced_at: string | null; edges_at: string | null;
  /** The engine's label for the pricing path, and the same in words. */
  priced_from: string | null; priced_from_words: string | null;
  /**
   * THE PICK IS OUT OF DATE when the station has moved past it since it was
   * priced (plan v2.3 P4.8): its standing against the reading now is worse
   * than against the maximum the price already knew. The pick is never
   * relabelled - a new bucket needs a new price - it is marked. null when the
   * card is not a same-day card or the reading cannot say.
   */
  stale: { standing: PickStanding; floor_c_then: number | null; max_c_now: number } | null;
  forecasts: Array<{ model: string; max_c: number; run_at: string | null }>;
  own: OwnModelRow | null; live: LiveRow | null;
}

/**
 * The cards, from the rows. Exported so the rule for "which market" and "which
 * edge" is tested rather than trusted (web/tests/city-cards.test.cjs
 * exercises it).
 *
 * WHICH MARKET: the earliest trade date the city still has an OPEN ladder for
 * (`pick` = "soonest"), or one date for every city.
 * MOST LIKELY BUCKET: the YES row with the highest model probability.
 * model_prob is the YES probability on both rows of a band, while
 * market_price is the price of that row's own side, so the YES row is the
 * only one whose price answers "what does the market charge for this bucket".
 * BEST TRADE: the highest net edge among rows the edge engine marked
 * tradeable - never a blocked row, whatever its edge - and flagged when it
 * bets on the engine against the market's favourite.
 * MARKET'S FAVOURITE: the YES row the market prices highest. Shown beside the
 * engine's because on settled days (v_city_hit_history, 13-23 Sep) the
 * market's pick won about twice as often as the engine's when they differed.
 */
export function buildCards(
  cities: CityRow[], ladder: LadderRow[], forecasts: ForecastRow[],
  own: OwnModelRow[], live: LiveRow[], pick: string,
): CityCard[] {
  const byCity = new Map<string, LadderRow[]>();
  for (const r of ladder) {
    if (!byCity.has(r.city_key)) byCity.set(r.city_key, []);
    byCity.get(r.city_key)!.push(r);
  }
  const out: CityCard[] = [];
  for (const c of cities) {
    const rows = byCity.get(c.city_key) ?? [];
    const dates = Array.from(new Set(rows.map((r) => r.for_date))).sort();
    const for_date = pick === "soonest" ? dates[0] : (dates.includes(pick) ? pick : undefined);
    if (!for_date) continue;
    const day = rows.filter((r) => r.for_date === for_date);
    const yes = day.filter((r) => r.side === "YES" && r.model_prob !== null);
    const top = yes.slice().sort((a, b) => (b.model_prob ?? 0) - (a.model_prob ?? 0))[0];
    const any = top ?? day[0];
    const tradeable = day.filter((r) => r.tradeable === true && r.edge_net_pp !== null);
    const best = tradeable.slice().sort((a, b) => (b.edge_net_pp ?? 0) - (a.edge_net_pp ?? 0))[0];
    const reasons = new Map<string, number>();
    for (const r of day) if (r.block_reason) reasons.set(r.block_reason, (reasons.get(r.block_reason) ?? 0) + 1);
    const blocked = Array.from(reasons.entries()).sort((a, b) => b[1] - a[1])[0]?.[0] ?? null;
    const latestByModel = new Map<string, ForecastRow>();
    for (const f of forecasts) {
      if (f.city_key !== c.city_key || f.for_date !== for_date || f.forecast_max_c === null) continue;
      const cur = latestByModel.get(f.model);
      if (!cur || f.lead_days < cur.lead_days) latestByModel.set(f.model, f);
    }
    const liveRow = live.find((l) => l.city_key === c.city_key) ?? null;
    // Live readings belong on the card only when they are about the SAME day.
    const sameDay = liveRow && liveRow.local_date === for_date ? liveRow : null;
    const unit = (c.unit === "F" ? "F" : "C") as Unit;
    const priced = yes.filter((r) => r.market_price !== null);
    const mkt = priced.slice().sort((a, b) => (b.market_price ?? 0) - (a.market_price ?? 0))[0];
    const disagrees = !!(top && mkt && top.band_id !== mkt.band_id);
    const fcVals = Array.from(latestByModel.values()).map((f) => f.forecast_max_c as number);
    const spread = fcVals.length >= 2 ? Math.max(...fcVals) - Math.min(...fcVals) : null;
    // THE CENTRE THE LADDER WAS PRICED ON, from the same row as its
    // probabilities. forecast_max_c is the public input before any correction.
    const centre = any?.centre_c ?? null;
    const outside = centre !== null && fcVals.length > 0
      && (centre > Math.max(...fcVals) + 0.5 || centre < Math.min(...fcVals) - 0.5);
    // OUT OF DATE: the station has moved past the pick since it was priced.
    let stale: CityCard["stale"] = null;
    if (top && sameDay && sameDay.source_kind !== "model" && sameDay.running_max_c != null) {
      const bounds = new Map<string, BandBounds>();
      for (const r of day) if (!bounds.has(r.band_id)) bounds.set(r.band_id, r);
      const ladderBounds = Array.from(bounds.values());
      const now = pickStanding(sameDay.running_max_c, unit, ladderBounds, top.band_id);
      const then = pickStanding(top.observed_floor_c ?? null, unit, ladderBounds, top.band_id) ?? "open";
      if (now && STANDING_RANK[now] > STANDING_RANK[then]) {
        stale = { standing: now, floor_c_then: top.observed_floor_c ?? null, max_c_now: sameDay.running_max_c };
      }
    }
    // AGAINST THE MARKET: YES on a bucket the market does not favour, or NO on
    // the one it does, while the engine and the market disagree about the
    // favourite. That is the bet the settled record says loses most often.
    const againstMarket = (b: LadderRow | undefined) => !!(b && disagrees && mkt && (
      (b.side === "YES" && b.band_id !== mkt.band_id) || (b.side === "NO" && b.band_id === mkt.band_id)));
    out.push({
      city_key: c.city_key, name: c.display_name ?? c.city_key,
      unit, for_date,
      centre_c: centre, raw_forecast_c: any?.forecast_max_c ?? null,
      priced_floor_c: any?.observed_floor_c ?? null, sigma_c: any?.sigma_c ?? null,
      confidence: any?.confidence ?? null, regime: any?.regime_label ?? null,
      top_band: top?.band_label ?? null, top_prob: top?.model_prob ?? null,
      top_yes_price: top?.market_price ?? null,
      market_band: mkt?.band_label ?? null, market_price: mkt?.market_price ?? null,
      disagrees,
      favourite_dead: !!(top && ((top.market_price !== null && top.market_price < 0.05)
        || day.some((r) => r.band_id === top.band_id && r.block_reason === "dead_band"))),
      forecast_spread_c: spread,
      centre_outside_forecasts: outside,
      best: best && (best.edge_net_pp ?? 0) > 0
        ? { side: best.side ?? "", band: best.band_label, edge: best.edge_net_pp as number,
            price: best.market_price, depth: best.depth_5c, against_market: againstMarket(best) }
        : null,
      blocked,
      priced_at: any?.prob_at ?? null,
      edges_at: day.reduce<string | null>((m, r) => (r.edge_at && (!m || r.edge_at > m) ? r.edge_at : m), null),
      priced_from: any?.priced_from ?? null,
      priced_from_words: pricedFromWords(any?.priced_from),
      stale,
      forecasts: Array.from(latestByModel.values())
        .map((f) => ({ model: f.model, max_c: f.forecast_max_c as number, run_at: f.run_at }))
        .sort((a, b) => a.model.localeCompare(b.model)),
      own: own.find((o) => o.city_key === c.city_key && o.for_date === for_date) ?? null,
      live: sameDay,
    });
  }
  // A trade against the market sorts after every trade that is not, whatever
  // its claimed edge: the size of a disagreement is not evidence it is right.
  const rank = (c: CityCard) => (c.best ? (c.best.against_market ? 1 : 2) : 0);
  return out.sort((a, b) => rank(b) - rank(a) || (b.best?.edge ?? -1) - (a.best?.edge ?? -1)
    || a.name.localeCompare(b.name));
}

