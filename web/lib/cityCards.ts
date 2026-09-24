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

export interface CityCard {
  city_key: string; name: string; unit: Unit; for_date: string;
  predicted_c: number | null; sigma_c: number | null; confidence: number | null; regime: string | null;
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
  blocked: string | null; priced_at: string | null;
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
    const priced = yes.filter((r) => r.market_price !== null);
    const mkt = priced.slice().sort((a, b) => (b.market_price ?? 0) - (a.market_price ?? 0))[0];
    const disagrees = !!(top && mkt && top.band_id !== mkt.band_id);
    const fcVals = Array.from(latestByModel.values()).map((f) => f.forecast_max_c as number);
    const spread = fcVals.length >= 2 ? Math.max(...fcVals) - Math.min(...fcVals) : null;
    const centre = any?.forecast_max_c ?? null;
    const outside = centre !== null && fcVals.length > 0
      && (centre > Math.max(...fcVals) + 0.5 || centre < Math.min(...fcVals) - 0.5);
    // AGAINST THE MARKET: YES on a bucket the market does not favour, or NO on
    // the one it does, while the engine and the market disagree about the
    // favourite. That is the bet the settled record says loses most often.
    const againstMarket = (b: LadderRow | undefined) => !!(b && disagrees && mkt && (
      (b.side === "YES" && b.band_id !== mkt.band_id) || (b.side === "NO" && b.band_id === mkt.band_id)));
    out.push({
      city_key: c.city_key, name: c.display_name ?? c.city_key,
      unit: (c.unit === "F" ? "F" : "C") as Unit, for_date,
      predicted_c: any?.forecast_max_c ?? null, sigma_c: any?.sigma_c ?? null,
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
      priced_at: day.reduce<string | null>((m, r) => (r.edge_at && (!m || r.edge_at > m) ? r.edge_at : m), null),
      forecasts: Array.from(latestByModel.values())
        .map((f) => ({ model: f.model, max_c: f.forecast_max_c as number, run_at: f.run_at }))
        .sort((a, b) => a.model.localeCompare(b.model)),
      own: own.find((o) => o.city_key === c.city_key && o.for_date === for_date) ?? null,
      // Live readings belong on the card only when they are about the SAME day.
      live: liveRow && liveRow.local_date === for_date ? liveRow : null,
    });
  }
  // A trade against the market sorts after every trade that is not, whatever
  // its claimed edge: the size of a disagreement is not evidence it is right.
  const rank = (c: CityCard) => (c.best ? (c.best.against_market ? 1 : 2) : 0);
  return out.sort((a, b) => rank(b) - rank(a) || (b.best?.edge ?? -1) - (a.best?.edge ?? -1)
    || a.name.localeCompare(b.name));
}

