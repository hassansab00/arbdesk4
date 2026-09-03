"use client";

import { useEffect, useMemo, useState } from "react";
import { supabase } from "@/lib/supabase";
import { useQuery } from "@/lib/useQuery";
import { DataState } from "@/components/DataState";
import RefreshButton from "@/components/RefreshButton";
import { fmtAge, fmtCompactUsd, fmtPct, fmtPp, fmtPrice, fmtUsd, regimeColor } from "@/lib/format";
import { fmtBandRange, fmtTemp, type Unit } from "@/lib/units";
import { fmtDaysAhead, fmtResolutionDate } from "@/lib/time";
import { buy, feeRateAt } from "@/lib/costs";
import { LADDER_SOURCE_LABEL, VOLUME_SOURCE_LABEL, type Opportunity } from "@/lib/types";

/**
 * The Board is ONE DAY'S MARKETS, laid out the way the exchange lays them out.
 *
 * It used to be a flat table of every band on every city on every date, sorted
 * by rank - a screener. A screener answers "where is the best edge anywhere";
 * it cannot answer "what is Chicago doing today", which is the question you
 * actually sit down with. Worse, with several resolution dates live at once,
 * consecutive rows were different DAYS and nothing said so.
 *
 * So: pick a day, then read each city as a ladder of bands in temperature
 * order - the shape the market itself has. Three things follow from that shape
 * and none of them were visible before:
 *
 *   * the YES prices across one city's bands should sum to about 100c, since
 *     exactly one band pays. The gap IS the combination arbitrage, and here it
 *     is a number at the foot of every city.
 *   * the day's forecast and its running maximum sit at the top of the ladder,
 *     so the price of a band can be read against where the weather actually is.
 *   * a stake typed against a band shows cost, payoff and net immediately -
 *     sizing at the point of decision rather than on another tab.
 */

interface LiveRow {
  city_key: string;
  temp_c: number | null;
  running_max_c: number | null;
  peak_window_state: string | null;
  day_decided: boolean | null;
  observed_at: string | null;
}

export default function BoardPage() {
  const [day, setDay] = useState<string | null>(null);
  const [cityFilter, setCityFilter] = useState("");
  const [stakes, setStakes] = useState<Record<string, string>>({});
  const [showUntradeable, setShowUntradeable] = useState(false);

  const q = useQuery<Opportunity[]>(
    () => supabase.from("v_opportunities").select("*").limit(4000),
    [],
    30000
  );
  const live = useQuery<LiveRow[]>(
    () => supabase.from("live_weather").select("city_key,temp_c,running_max_c,peak_window_state,day_decided,observed_at"),
    [],
    60000
  );
  const forecasts = useQuery<Array<{ city_key: string; for_date: string; forecast_max_c: number | null; model: string | null }>>(
    () =>
      supabase
        .from("weather_forecasts")
        .select("city_key,for_date,forecast_max_c,model,run_at")
        .gte("for_date", new Date().toISOString().slice(0, 10))
        .order("run_at", { ascending: false })
        .limit(2000),
    [],
    5 * 60000
  );

  const rows = q.data ?? [];

  // Every resolution date on the board, nearest first. This is the control
  // that was missing: "1 day ahead" and "2 days ahead" are separate markets
  // and were previously interleaved with no way to tell them apart.
  const days = useMemo(
    () => Array.from(new Set(rows.map((r) => r.resolution_date))).sort(),
    [rows]
  );
  useEffect(() => {
    if (!day && days.length) setDay(days[0]);
  }, [days, day]);

  const liveByCity = useMemo(
    () => new Map((live.data ?? []).map((l) => [l.city_key, l])),
    [live.data]
  );
  const forecastByCityDay = useMemo(() => {
    const m = new Map<string, number>();
    for (const f of forecasts.data ?? []) {
      if (f.forecast_max_c === null) continue;
      const k = `${f.city_key}|${f.for_date}`;
      if (!m.has(k)) m.set(k, f.forecast_max_c);   // newest run wins
    }
    return m;
  }, [forecasts.data]);

  // One entry per city on the chosen day, each holding its bands in
  // temperature order - lowest band at the bottom, like a thermometer.
  const cities = useMemo(() => {
    const onDay = rows.filter((r) => r.resolution_date === day);
    const byCity = new Map<string, Opportunity[]>();
    for (const r of onDay) {
      if (!byCity.has(r.city_key)) byCity.set(r.city_key, []);
      byCity.get(r.city_key)!.push(r);
    }
    const out = Array.from(byCity.entries()).map(([city_key, all]) => {
      // v_opportunities has a row per band PER SIDE; the ladder wants one row
      // per band, with the YES side leading and the NO side alongside.
      const byBand = new Map<string, { yes?: Opportunity; no?: Opportunity }>();
      for (const r of all) {
        const e = byBand.get(r.band_id) ?? {};
        if (r.side === "YES") e.yes = r;
        else e.no = r;
        byBand.set(r.band_id, e);
      }
      const bands = Array.from(byBand.values())
        .filter((b) => b.yes || b.no)
        .sort((a, b) => {
          const av = (a.yes ?? a.no)!;
          const bv = (b.yes ?? b.no)!;
          // open-low tail at the bottom, open-high at the top
          const ak = av.open_low ? -Infinity : av.open_high ? Infinity : (av.band_lo ?? 0);
          const bk = bv.open_low ? -Infinity : bv.open_high ? Infinity : (bv.band_lo ?? 0);
          return bk - ak;                      // descending: hottest first
        });
      const head = (bands[0]?.yes ?? bands[0]?.no)!;
      return { city_key, head, bands };
    });
    out.sort((a, b) => (a.head.display_name ?? a.city_key).localeCompare(b.head.display_name ?? b.city_key));
    return cityFilter ? out.filter((c) => c.city_key === cityFilter) : out;
  }, [rows, day, cityFilter]);

  const allCityKeys = useMemo(
    () => Array.from(new Set(rows.filter((r) => r.resolution_date === day).map((r) => r.city_key))).sort(),
    [rows, day]
  );

  const totals = useMemo(() => {
    let cost = 0, legs = 0;
    for (const [key, raw] of Object.entries(stakes)) {
      const usd = parseFloat(raw);
      if (!Number.isFinite(usd) || usd <= 0) continue;
      const o = rows.find((r) => `${r.band_id}-${r.side}` === key);
      if (!o?.market_price) continue;
      legs += 1;
      cost += usd;
    }
    return { cost, legs };
  }, [stakes, rows]);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold">Board</h1>
          <p className="mt-1 max-w-3xl text-xs leading-relaxed text-muted">
            One day, one city per block, bands in temperature order — the shape the market has.
            Prices are <b>executable</b> (depth-weighted), never top-of-book. The{" "}
            <b>sum of YES</b> under each city is the coherence check: exactly one band pays $1, so
            the prices should add to about 100¢. A sum meaningfully under 100¢ is a combination
            arbitrage; over 100¢ means the book is charging a premium to be on any side at all.
          </p>
        </div>
        <RefreshButton
          job="P0.3_book_volume_snapshot"
          label="Refresh books"
          onDone={() => { q.refresh(); live.refresh(); }}
        />
      </div>

      {/* ---- day selector: the control that was missing entirely ---------- */}
      {days.length > 0 && (
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs text-muted">Settles</span>
          {days.map((d) => (
            <button
              key={d}
              onClick={() => setDay(d)}
              className={`rounded border px-2.5 py-1 text-xs transition ${
                d === day ? "border-accent bg-accent/10 text-accent" : "border-border text-muted hover:text-text"
              }`}
            >
              {fmtResolutionDate(d)}
              <span className="ml-1.5 opacity-70">{fmtDaysAhead(d)}</span>
            </button>
          ))}
          <select
            value={cityFilter}
            onChange={(e) => setCityFilter(e.target.value)}
            className="ml-2 rounded border border-border bg-panel2 px-2 py-1 text-xs"
          >
            <option value="">All cities</option>
            {allCityKeys.map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
          <label className="flex items-center gap-1 text-xs text-muted">
            <input type="checkbox" checked={showUntradeable} onChange={(e) => setShowUntradeable(e.target.checked)} />
            show blocked bands
          </label>
          {totals.legs > 0 && (
            <span className="ml-auto rounded border border-accent/40 bg-accent/5 px-2.5 py-1 font-mono text-xs">
              {totals.legs} leg{totals.legs === 1 ? "" : "s"} · cost {fmtUsd(totals.cost)}
              <button
                onClick={() => setStakes({})}
                className="ml-2 text-[10px] text-muted underline hover:text-text"
              >
                clear
              </button>
            </span>
          )}
        </div>
      )}

      <DataState
        loading={q.loading}
        error={q.error}
        isEmpty={rows.length === 0}
        emptyTitle="The board is empty"
        emptyBody={
          <>
            <code>v_opportunities</code> returned no rows. It is built from <code>edges</code>, which
            the edge engine writes — run GitHub Actions → <b>Probabilities</b>. If the board has one
            city when you expect many, that is <code>markets</code>, not this page: P0.2 Market
            Discovery is what finds them.
          </>
        }
        onRetry={q.refresh}
      >
        {cities.length === 0 ? (
          <div className="rounded border border-dashed border-border p-6 text-center text-sm text-muted">
            Nothing settles on {day ? fmtResolutionDate(day) : "this day"}
            {cityFilter ? ` for ${cityFilter}` : ""}.
          </div>
        ) : (
          <div className="space-y-4">
            {cities.map(({ city_key, head, bands }) => {
              const unit = head.unit as Unit;
              const lw = liveByCity.get(city_key);
              const fc = forecastByCityDay.get(`${city_key}|${day}`);
              const yesSum = bands.reduce((s, b) => s + (b.yes?.market_price ?? 0), 0);
              const priced = bands.filter((b) => b.yes?.market_price != null).length;
              const coherence = priced >= 2 ? yesSum : null;

              return (
                <section key={city_key} className="overflow-hidden rounded border border-border bg-panel">
                  {/* --- city header: the weather, so a price can be read against it --- */}
                  <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border bg-panel2 px-3 py-2">
                    <div className="flex items-baseline gap-3">
                      <h2 className="font-semibold">{head.display_name ?? city_key}</h2>
                      <span className="font-mono text-[11px] text-muted">
                        {head.icao ?? ""} · settles {fmtResolutionDate(head.resolution_date)}
                      </span>
                      <span className={`text-xs ${regimeColor(head.regime_label)}`}>{head.regime_label}</span>
                    </div>
                    <div className="flex flex-wrap items-center gap-4 font-mono text-[11px]">
                      <Stat label="Now" value={fmtTemp(lw?.temp_c, unit)} />
                      <Stat label="Max so far" value={fmtTemp(lw?.running_max_c, unit)} />
                      <Stat label="Forecast" value={fmtTemp(fc, unit)} tone={fc == null ? "muted" : undefined} />
                      <Stat
                        label="Peak"
                        value={lw?.peak_window_state ?? "—"}
                        tone={lw?.peak_window_state === "INSIDE" ? "accent" : undefined}
                      />
                      {lw?.day_decided && <span className="rounded bg-muted/10 px-1.5 py-0.5 text-muted">DAY DECIDED</span>}
                    </div>
                  </div>

                  <div className="overflow-x-auto">
                    <table className="w-full text-sm">
                      <thead className="bg-panel text-[11px] uppercase tracking-wide text-muted">
                        <tr>
                          <th className="p-2 text-left">Band</th>
                          <th className="p-2 text-right">YES</th>
                          <th className="p-2 text-right">NO</th>
                          <th className="p-2 text-right" title="The model's probability that the day's maximum lands in this band.">Model</th>
                          <th className="p-2 text-right" title="Model probability minus executable price, after fees.">Edge</th>
                          <th className="p-2 text-right" title="What the current quote can absorb inside 5c of slippage.">Depth 5c</th>
                          <th className="p-2 text-right" title="What has actually traded on this band in 24h.">Vol 24h</th>
                          <th className="p-2 text-right" title="Type a dollar stake. Everything to the right is that stake at the executable price, after fees.">Stake $</th>
                          <th className="p-2 text-right">Shares</th>
                          <th className="p-2 text-right" title="What this leg returns if the day settles in this band.">If it hits</th>
                          <th className="p-2 text-right">Net</th>
                        </tr>
                      </thead>
                      <tbody>
                        {bands.map(({ yes, no }) => {
                          const o = (yes ?? no)!;
                          const blocked = !(yes?.tradeable || no?.tradeable);
                          if (blocked && !showUntradeable) return null;
                          const key = `${o.band_id}-YES`;
                          const raw = stakes[key] ?? "";
                          const usd = parseFloat(raw);
                          const valid = Number.isFinite(usd) && usd > 0 && yes?.market_price;
                          const pos = valid ? buy(usd, yes!.market_price!, o.model_prob) : null;
                          // Is the day's running max already inside this band?
                          const inBand =
                            lw?.running_max_c != null &&
                            (o.band_lo == null || lw.running_max_c >= o.band_lo) &&
                            (o.band_hi == null || lw.running_max_c < o.band_hi);

                          return (
                            <tr
                              key={o.band_id}
                              className={[
                                "border-t border-border",
                                blocked ? "opacity-45" : "hover:bg-panel2",
                                inBand ? "bg-warn/5" : "",
                              ].join(" ")}
                            >
                              <td className="whitespace-nowrap p-2 font-mono">
                                {o.band_label ?? fmtBandRange(o.band_lo, o.band_hi, unit, o.open_low, o.open_high)}
                                {inBand && (
                                  <span className="ml-1.5 text-[10px] text-warn" title="The day's running maximum is currently inside this band.">
                                    ← max
                                  </span>
                                )}
                              </td>
                              <td className="p-2 text-right font-mono">{fmtPrice(yes?.market_price)}</td>
                              <td className="p-2 text-right font-mono text-muted">{fmtPrice(no?.market_price)}</td>
                              <td className="p-2 text-right font-mono">{fmtPct(o.model_prob)}</td>
                              <td className={`p-2 text-right font-mono ${(yes?.edge_net_pp ?? 0) > 0 ? "text-good" : "text-muted"}`}>
                                {fmtPp(yes?.edge_net_pp)}
                              </td>
                              <td
                                className={`p-2 text-right font-mono ${yes?.ask_levels_source === "synthetic_tiers" ? "text-warn" : ""}`}
                                title={yes?.ask_levels_source ? `ladder: ${LADDER_SOURCE_LABEL[yes.ask_levels_source]}` : ""}
                              >
                                {fmtUsd(yes?.fillable_usd_5c)}
                                {yes?.ask_levels_source === "synthetic_tiers" ? " ~" : ""}
                              </td>
                              <td
                                className={`p-2 text-right font-mono ${o.thin_market ? "text-warn" : o.volume_usd ? "" : "text-muted"}`}
                                title={[
                                  o.volume_source ? VOLUME_SOURCE_LABEL[o.volume_source] : "source unknown",
                                  o.last_trade_at ? `last trade ${fmtAge(o.last_trade_at)}` : "no trades in the window",
                                ].filter(Boolean).join(" · ")}
                              >
                                {fmtCompactUsd(o.volume_usd)}{o.thin_market ? " ⚠" : ""}
                              </td>
                              <td className="p-2 text-right">
                                <input
                                  inputMode="decimal"
                                  value={raw}
                                  onChange={(e) => setStakes((s) => ({ ...s, [key]: e.target.value }))}
                                  placeholder="—"
                                  disabled={!yes?.market_price}
                                  className="w-20 rounded border border-border bg-panel2 px-1.5 py-0.5 text-right font-mono text-xs text-text disabled:opacity-40"
                                />
                              </td>
                              <td
                                className="p-2 text-right font-mono text-muted"
                                title={pos ? `fee ${fmtUsd(pos.fee)} (${fmtPct(feeRateAt(yes!.market_price!), 2)} of notional), taken out of the stake` : ""}
                              >
                                {pos === null ? "—" : pos.shares.toFixed(0)}
                              </td>
                              <td className="p-2 text-right font-mono">{pos === null ? "—" : fmtUsd(pos.payout)}</td>
                              <td className={`p-2 text-right font-mono ${pos && pos.profit > 0 ? "text-good" : "text-muted"}`}>
                                {pos === null ? "—" : fmtUsd(pos.profit, { signed: true })}
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>

                      {/* --- the coherence check ------------------------------------- */}
                      <tfoot>
                        <tr className="border-t-2 border-border bg-panel2 text-[11px]">
                          <td className="p-2 font-semibold">Sum of YES across {priced} band{priced === 1 ? "" : "s"}</td>
                          <td
                            className={`p-2 text-right font-mono font-semibold ${
                              coherence === null ? "text-muted"
                                : coherence < 0.97 ? "text-good"
                                : coherence > 1.03 ? "text-warn"
                                : "text-text"
                            }`}
                            title={
                              coherence === null ? "Needs at least two priced bands."
                                : coherence < 0.97
                                ? "Under 100c: buying every band costs less than the $1 exactly one of them pays. That is the combination arbitrage - if every leg fills."
                                : coherence > 1.03
                                ? "Over 100c: the book is charging a premium to hold any side. Selling the full set is the mirror trade, subject to the same fill risk."
                                : "Within 3c of 100c - coherent, no free money here."
                            }
                          >
                            {coherence === null ? "—" : fmtPrice(coherence)}
                          </td>
                          <td colSpan={9} className="p-2 text-muted">
                            {coherence === null
                              ? "not enough priced bands to check"
                              : coherence < 0.97
                              ? `${fmtPrice(1 - coherence)} under par — buying the full set is a guaranteed payoff before fees. Size to the thinnest leg.`
                              : coherence > 1.03
                              ? `${fmtPrice(coherence - 1)} over par.`
                              : "coherent"}
                          </td>
                        </tr>
                      </tfoot>
                    </table>
                  </div>
                </section>
              );
            })}
          </div>
        )}
      </DataState>

      <p className="max-w-3xl text-[11px] leading-relaxed text-muted">
        Stakes are a calculator, not an order — nothing here places a trade. Every figure is{" "}
        <b>net</b>: the taker fee is <code>shares × 0.05 × p × (1 − p)</code>, taken out of the
        stake, so &ldquo;$100&rdquo; means $100 at risk. That fee peaks at 1.25% around 50¢ and
        falls to almost nothing at both extremes — resting a limit order instead pays{" "}
        <b>zero</b> and earns a rebate. Returns assume the whole stake fills at the executable
        price shown; a stake larger than <b>Depth 5c</b> will not.
      </p>
    </div>
  );
}

function Stat({ label, value, tone }: { label: string; value: string; tone?: "muted" | "accent" }) {
  return (
    <span>
      <span className="text-muted">{label} </span>
      <span className={tone === "muted" ? "text-muted" : tone === "accent" ? "text-accent" : "text-text"}>{value}</span>
    </span>
  );
}
