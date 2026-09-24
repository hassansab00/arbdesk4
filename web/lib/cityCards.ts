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

export interface CityCard {
  city_key: string; name: string; unit: Unit; for_date: string;
  predicted_c: number | null; sigma_c: number | null; confidence: number | null; regime: string | null;
  top_band: string | null; top_prob: number | null; top_yes_price: number | null;
  best: { side: string; band: string | null; edge: number; price: number | null; depth: number | null } | null;
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
 * tradeable - never a blocked row, whatever its edge.
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
    out.push({
      city_key: c.city_key, name: c.display_name ?? c.city_key,
      unit: (c.unit === "F" ? "F" : "C") as Unit, for_date,
      predicted_c: any?.forecast_max_c ?? null, sigma_c: any?.sigma_c ?? null,
      confidence: any?.confidence ?? null, regime: any?.regime_label ?? null,
      top_band: top?.band_label ?? null, top_prob: top?.model_prob ?? null,
      top_yes_price: top?.market_price ?? null,
      best: best && (best.edge_net_pp ?? 0) > 0
        ? { side: best.side ?? "", band: best.band_label, edge: best.edge_net_pp as number,
            price: best.market_price, depth: best.depth_5c }
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
  return out.sort((a, b) => (b.best?.edge ?? -1) - (a.best?.edge ?? -1) || a.name.localeCompare(b.name));
}

