"use client";

import { useEffect, useMemo, useState } from "react";
import { supabase } from "@/lib/supabase";
import { useQuery } from "@/lib/useQuery";
import { DataState } from "@/components/DataState";
import RefreshButton from "@/components/RefreshButton";
import { fmtAge, fmtCompactUsd, fmtPct, fmtPrice, fmtUsd, regimeColor } from "@/lib/format";
import { fmtBandRange, fmtTemp, type Unit } from "@/lib/units";
import { fmtDaysAhead, fmtResolutionDate } from "@/lib/time";
import { solveBoard, overround, type Leg } from "@/lib/ladder";
import { feeRateAt } from "@/lib/costs";
import { LADDER_SOURCE_LABEL, VOLUME_SOURCE_LABEL, type Opportunity } from "@/lib/types";
import ReasoningPanel, { useReasoning } from "@/components/Reasoning";

/**
 * The Board, in the shape ArbDesk 1 had it, with what AD4 knows that AD1 did
 * not.
 *
 * AD1's board was: bucket, market %, YOUR %, Yes c, No c, volume, bet Yes,
 * bet No - and under it a ladder of "if the day lands on ...". Two things
 * about that were right and the AD4 board had lost both.
 *
 *   PROBABILITY IS THE UNIT. A price in cents IS an implied probability, and
 *   the trade is a disagreement between two probabilities. Showing only cents
 *   asks the reader to convert in their head on every row.
 *
 *   THE POSITION IS THE LADDER, NOT THE ROW. Exactly one bucket resolves Yes,
 *   so a board with several legs has one P&L per outcome. A NO leg makes this
 *   impossible to see row-by-row: betting No on one bucket pays out on every
 *   other, so a single stake moves ten rows of the ladder at once.
 *
 * What AD4 adds: a MODEL % beside the market's, measured from forecast skill
 * rather than typed in; depth-capped fills, so a stake larger than the book
 * shows what would actually fill; and the city's live weather above the
 * ladder, because the whole thesis is where the day lands.
 *
 * Every band is shown. The previous version hid untradeable ones by default,
 * which is why a market Polymarket displays with eleven buckets appeared here
 * with six - the missing ones were blocked, not absent, and hiding them made
 * the board disagree with the exchange.
 */

interface LiveRow {
  city_key: string;
  temp_c: number | null;
  running_max_c: number | null;
  peak_window_state: string | null;
  day_decided: boolean | null;
  observed_at: string | null;
  trend: string | null;
}

export default function BoardPage() {
  const [day, setDay] = useState<string | null>(null);
  const [city, setCity] = useState<string | null>(null);
  const [yesStakes, setYesStakes] = useState<Record<string, string>>({});
  const [noStakes, setNoStakes] = useState<Record<string, string>>({});
  const [myProb, setMyProb] = useState<Record<string, string>>({});

  const q = useQuery<Opportunity[]>(
    () => supabase.from("v_opportunities").select("*").limit(4000),
    [],
    30000
  );
  const live = useQuery<LiveRow[]>(
    () => supabase.from("live_weather").select("city_key,temp_c,running_max_c,peak_window_state,day_decided,observed_at,trend"),
    [],
    60000
  );
  const forecasts = useQuery<Array<{ city_key: string; for_date: string; forecast_max_c: number | null; model: string | null; run_at: string | null }>>(
    () =>
      supabase.from("weather_forecasts")
        .select("city_key,for_date,forecast_max_c,model,run_at")
        .gte("for_date", new Date().toISOString().slice(0, 10))
        .order("run_at", { ascending: false }).limit(2000),
    [],
    5 * 60000
  );

  const rows = q.data ?? [];
  const days = useMemo(() => Array.from(new Set(rows.map((r) => r.resolution_date))).sort(), [rows]);
  useEffect(() => { if (!day && days.length) setDay(days[0]); }, [days, day]);

  const liveByCity = useMemo(() => new Map((live.data ?? []).map((l) => [l.city_key, l])), [live.data]);
  const fcByCityDay = useMemo(() => {
    const m = new Map<string, { max: number | null; model: string | null; at: string | null }>();
    for (const f of forecasts.data ?? []) {
      const k = `${f.city_key}|${f.for_date}`;
      if (!m.has(k)) m.set(k, { max: f.forecast_max_c, model: f.model, at: f.run_at });
    }
    return m;
  }, [forecasts.data]);

  // One entry per city on the chosen day, bands hottest-first, ALL of them.
  const cities = useMemo(() => {
    const onDay = rows.filter((r) => r.resolution_date === day);
    const byCity = new Map<string, Opportunity[]>();
    for (const r of onDay) {
      if (!byCity.has(r.city_key)) byCity.set(r.city_key, []);
      byCity.get(r.city_key)!.push(r);
    }
    return Array.from(byCity.entries())
      .map(([city_key, all]) => {
        const byBand = new Map<string, { yes?: Opportunity; no?: Opportunity }>();
        for (const r of all) {
          const e = byBand.get(r.band_id) ?? {};
          if (r.side === "YES") e.yes = r; else e.no = r;
          byBand.set(r.band_id, e);
        }
        const bands = Array.from(byBand.values()).sort((a, b) => {
          const av = (a.yes ?? a.no)!, bv = (b.yes ?? b.no)!;
          const ak = av.open_low ? -1e9 : av.open_high ? 1e9 : (av.band_lo ?? 0);
          const bk = bv.open_low ? -1e9 : bv.open_high ? 1e9 : (bv.band_lo ?? 0);
          return bk - ak;
        });
        return { city_key, head: (bands[0]?.yes ?? bands[0]?.no)!, bands };
      })
      .sort((a, b) => (a.head.display_name ?? a.city_key).localeCompare(b.head.display_name ?? b.city_key));
  }, [rows, day]);

  const cityOptions = useMemo(
    () => cities.map((c) => ({ key: c.city_key, label: c.head.display_name ?? c.city_key, bands: c.bands.length })),
    [cities]
  );
  useEffect(() => {
    if (cityOptions.length === 0) return;
    if (!city || !cityOptions.some((c) => c.key === city)) setCity(cityOptions[0].key);
  }, [cityOptions, city]);

  const board = cities.find((c) => c.city_key === city) ?? null;
  // The desk's argument for THIS city, above its ladder. The board shows the
  // answer; this shows the working, which is the difference between a number
  // and a reason to act on it.
  const reasoning = useReasoning(city);

  // Why is everything blocked, and are buckets missing?
  //
  // Both questions were answerable only by hovering eleven rows one at a time.
  // They are properties of the MARKET, so they belong above it.
  //
  // The expected bucket count is not asserted - it is the widest ladder any
  // city on this same day actually has. If every other city lists eleven and
  // this one lists six, five were not captured; if they all list six, six is
  // simply what this exchange published and nothing is wrong.
  const diagnosis = useMemo(() => {
    if (!board) return null;
    const total = board.bands.length;
    const blocked = board.bands.filter(({ yes, no }) => !(yes?.tradeable || no?.tradeable));
    const reasons = new Map<string, number>();
    for (const { yes, no } of blocked) {
      const r = (yes ?? no)!.block_reason ?? "no reason recorded";
      reasons.set(r, (reasons.get(r) ?? 0) + 1);
    }
    const top = [...reasons.entries()].sort((a, b) => b[1] - a[1]);
    const priced = board.bands.filter(({ yes }) => yes?.market_price != null).length;
    const widest = Math.max(0, ...cities.map((c) => c.bands.length));
    return {
      total, blocked: blocked.length, priced, widest,
      allBlocked: blocked.length === total && total > 0,
      topReason: top[0]?.[0] ?? null,
      reasons: top,
      short: widest > total,
    };
  }, [board, cities]);
  const unit = (board?.head.unit ?? "C") as Unit;
  const lw = board ? liveByCity.get(board.city_key) : undefined;
  const fc = board && day ? fcByCityDay.get(`${board.city_key}|${day}`) : undefined;

  // ---- the position ------------------------------------------------------
  const legs: Leg[] = useMemo(
    () =>
      (board?.bands ?? []).map(({ yes, no }) => {
        const o = (yes ?? no)!;
        return {
          band_id: o.band_id,
          label: o.band_label ?? fmtBandRange(o.band_lo, o.band_hi, unit, o.open_low, o.open_high),
          yesPrice: yes?.market_price ?? null,
          noPrice: no?.market_price ?? null,
          depthUsd: yes?.fillable_usd_5c ?? null,
          yesStake: parseFloat(yesStakes[o.band_id] ?? "") || 0,
          noStake: parseFloat(noStakes[o.band_id] ?? "") || 0,
        };
      }),
    [board, unit, yesStakes, noStakes]
  );

  // The probability each outcome is weighted by: the reader's own number where
  // they have typed one, the model's otherwise. That override IS the edge.
  const probs = useMemo(() => {
    const m = new Map<string, number | null>();
    for (const { yes, no } of board?.bands ?? []) {
      const o = (yes ?? no)!;
      const mine = parseFloat(myProb[o.band_id] ?? "");
      m.set(o.band_id, Number.isFinite(mine) ? mine / 100 : (yes?.model_prob ?? null));
    }
    return m;
  }, [board, myProb]);

  const result = useMemo(() => solveBoard(legs, probs), [legs, probs]);
  const book = overround(legs);
  const staked = legs.some((l) => l.yesStake > 0 || l.noStake > 0);

  function clearAll() { setYesStakes({}); setNoStakes({}); setMyProb({}); }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold">Board</h1>
          <p className="mt-1 max-w-3xl text-xs leading-relaxed text-muted">
            One city&apos;s full bucket ladder, priced in <b>probability</b> as well as cents — a
            price in cents <em>is</em> an implied probability, and the trade is the gap between two
            of them. <b>Model %</b> is AD4&apos;s own read from forecast skill; type over it in{" "}
            <b>Your %</b> where you disagree, and every figure below re-weights to your number.
          </p>
        </div>
        <div className="flex flex-col items-end gap-1">
          <RefreshButton job="P0.3_book_volume_snapshot" label="Refresh books" onDone={() => { q.refresh(); live.refresh(); }} />
          <RefreshButton job="P1.2_nws_monitor" label="Refresh weather" onDone={() => { live.refresh(); forecasts.refresh(); }} />
        </div>
      </div>

      {days.length > 0 && (
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs text-muted">Settles</span>
          {days.map((d) => (
            <button key={d} onClick={() => { setDay(d); clearAll(); }}
              className={`rounded border px-2.5 py-1 text-xs transition ${
                d === day ? "border-accent bg-accent/10 text-accent" : "border-border text-muted hover:text-text"}`}>
              {fmtResolutionDate(d)}<span className="ml-1.5 opacity-70">{fmtDaysAhead(d)}</span>
            </button>
          ))}
          <select value={city ?? ""} onChange={(e) => { setCity(e.target.value); clearAll(); }}
            className="ml-2 rounded border border-accent/50 bg-panel2 px-2 py-1 text-xs font-semibold text-text">
            {cityOptions.map((c) => (
              <option key={c.key} value={c.key}>{c.label} — {c.bands} buckets</option>
            ))}
          </select>
          {staked && (
            <button onClick={clearAll} className="text-[11px] text-muted underline hover:text-text">clear ticket</button>
          )}
        </div>
      )}

      <DataState
        loading={q.loading} error={q.error} isEmpty={rows.length === 0}
        emptyTitle="The board is empty"
        emptyBody={<><code>v_opportunities</code> has no rows. It is built from <code>edges</code> — run GitHub Actions → <b>Probabilities</b>. If a city is missing entirely, that is <code>markets</code>, which P0.2 fills.</>}
        onRetry={q.refresh}
      >
        {!board ? (
          <div className="rounded border border-dashed border-border p-6 text-center text-sm text-muted">
            Nothing settles on {day ? fmtResolutionDate(day) : "this day"}.
          </div>
        ) : (
          <div className="space-y-3">
            {/* ---- the weather this whole board is about ------------------ */}
            <div className="flex flex-wrap items-center justify-between gap-3 rounded border border-border bg-panel2 px-3 py-2">
              <div className="flex items-baseline gap-3">
                <h2 className="font-semibold">{board.head.display_name ?? board.city_key}</h2>
                <span className="font-mono text-[11px] text-muted">
                  {board.head.icao ?? ""} · settles {fmtResolutionDate(board.head.resolution_date)}
                </span>
                <span className={`text-xs ${regimeColor(board.head.regime_label)}`}>{board.head.regime_label}</span>
              </div>
              <div className="flex flex-wrap items-center gap-4 font-mono text-[11px]">
                <span><span className="text-muted">Now </span>{fmtTemp(lw?.temp_c, unit)}
                  {lw?.trend === "RISING" && <span className="ml-0.5 text-good">↑</span>}
                  {lw?.trend === "FALLING" && <span className="ml-0.5 text-bad">↓</span>}
                </span>
                <span><span className="text-muted">Max </span>{fmtTemp(lw?.running_max_c, unit)}</span>
                <span><span className="text-muted">Forecast </span>{fmtTemp(fc?.max, unit)}</span>
                <span className={lw?.peak_window_state === "INSIDE" ? "text-accent" : "text-muted"}>
                  peak {lw?.peak_window_state ?? "—"}
                </span>
                {/* Staleness is a fact about the trade, not a footnote. */}
                <span
                  className={
                    !lw?.observed_at ? "text-bad"
                    : Date.now() - new Date(lw.observed_at).getTime() > 90 * 60000 ? "text-warn"
                    : "text-muted"
                  }
                  title="Age of the newest observation for this city. Anything over 90 minutes is old enough that the running max may already have moved."
                >
                  obs {fmtAge(lw?.observed_at)}
                </span>
              </div>
            </div>

            {/* ---- the argument, before the numbers ----------------------- */}
            {reasoning.data?.[0] && <ReasoningPanel r={reasoning.data[0]} />}

            {/* ---- what is wrong with this market, stated once ----------- */}
            {diagnosis && (diagnosis.allBlocked || diagnosis.short || diagnosis.priced === 0) && (
              <div className="space-y-1.5 rounded border border-warn/40 bg-warn/10 px-3 py-2 text-xs leading-relaxed text-warn">
                {diagnosis.priced === 0 && (
                  <div>
                    <b>No bucket has a price.</b> Every figure below is blank because there is no
                    book snapshot for this market — that is <b>P0.3 Book + Volume Snapshot</b>, which
                    has to be imported and Active in n8n. You can still type prices into the Yes and
                    No columns and the ticket will calculate.
                  </div>
                )}
                {diagnosis.allBlocked && diagnosis.priced > 0 && (
                  <div>
                    <b>All {diagnosis.total} buckets are blocked from trading.</b>{" "}
                    {diagnosis.topReason && (
                      <>
                        The edge engine&apos;s reason on most of them is{" "}
                        <code>{diagnosis.topReason}</code>.
                      </>
                    )}{" "}
                    Blocked means the engine will not trade them; the prices and probabilities are
                    still real and the ticket still calculates.
                  </div>
                )}
                {diagnosis.short && (
                  <div>
                    <b>
                      This market shows {diagnosis.total} buckets; the widest ladder on this day is{" "}
                      {diagnosis.widest}.
                    </b>{" "}
                    Buckets are not hidden here — every one in the database is listed — so{" "}
                    {diagnosis.widest - diagnosis.total} were never captured. That is{" "}
                    <b>P0.2 Market Discovery</b>: it writes <code>bands</code>, and a partial ladder
                    there means the sum-of-Yes check below cannot reach 100¢ however the market is
                    priced.
                  </div>
                )}
                {diagnosis.reasons.length > 1 && (
                  <div className="font-mono text-[10px] opacity-80">
                    {diagnosis.reasons.map(([r, n]) => `${n}x ${r}`).join(" · ")}
                  </div>
                )}
              </div>
            )}

            {/* ---- the ladder -------------------------------------------- */}
            <div className="overflow-x-auto rounded border border-border">
              <table className="w-full text-sm">
                <thead className="bg-panel2 text-[10px] uppercase tracking-wide text-muted">
                  <tr>
                    <th className="p-2 text-left">Bucket</th>
                    <th className="p-2 text-right" title="The market's implied probability. For a binary contract the price IS the probability.">Mkt %</th>
                    <th className="p-2 text-right" title="AD4's own probability, from the forecast and this city's measured forecast error.">Model %</th>
                    <th className="p-2 text-right" title="Your read. Defaults to the model's. Where you disagree with the market, that difference IS your edge - every number below re-weights to it.">Your %</th>
                    <th className="p-2 text-right text-good">Yes ¢</th>
                    <th className="p-2 text-right text-bad">No ¢</th>
                    <th className="p-2 text-right" title="24h traded volume on this bucket. A thin bucket moves when you hit it.">Vol $</th>
                    <th className="p-2 text-right" title="Dollars fillable inside 5c of slippage on the YES side. A stake above this will not fill at the price shown.">Depth</th>
                    <th className="p-2 text-right text-good">Bet Yes $</th>
                    <th className="p-2 text-right text-bad">Bet No $</th>
                  </tr>
                </thead>
                <tbody>
                  {board.bands.map(({ yes, no }) => {
                    const o = (yes ?? no)!;
                    const label = o.band_label ?? fmtBandRange(o.band_lo, o.band_hi, unit, o.open_low, o.open_high);
                    const mkt = yes?.market_price ?? null;
                    const mine = parseFloat(myProb[o.band_id] ?? "");
                    const usingMine = Number.isFinite(mine);
                    const blocked = !(yes?.tradeable || no?.tradeable);
                    const inBand =
                      lw?.running_max_c != null &&
                      (o.band_lo == null || lw.running_max_c >= o.band_lo) &&
                      (o.band_hi == null || lw.running_max_c < o.band_hi);
                    const fills = result.fills.filter((f) => f.band_id === o.band_id);
                    const capped = fills.some((f) => f.capped);

                    return (
                      <tr key={o.band_id} className={[
                        "border-t border-border",
                        blocked ? "opacity-60" : "hover:bg-panel2",
                        inBand ? "bg-warn/5" : "",
                      ].join(" ")}>
                        <td className="whitespace-nowrap p-2 font-mono">
                          {label}
                          {inBand && <span className="ml-1.5 text-[10px] text-warn" title="The day's running maximum is currently inside this bucket.">← max</span>}
                          {blocked && (
                            <span className="ml-1.5 text-[9px] uppercase tracking-wide text-warn" title={o.block_reason ?? "blocked by the edge engine"}>
                              blocked
                            </span>
                          )}
                        </td>
                        <td className="p-2 text-right font-mono">{mkt === null ? "—" : fmtPct(mkt, 1)}</td>
                        <td className="p-2 text-right font-mono text-accent">{fmtPct(yes?.model_prob, 1)}</td>
                        <td className="p-2 text-right">
                          <input
                            inputMode="decimal"
                            value={myProb[o.band_id] ?? ""}
                            onChange={(e) => setMyProb((s) => ({ ...s, [o.band_id]: e.target.value }))}
                            placeholder={yes?.model_prob != null ? (yes.model_prob * 100).toFixed(1) : "—"}
                            className={`w-16 rounded border bg-panel2 px-1 py-0.5 text-right font-mono text-xs ${
                              usingMine ? "border-warn text-warn" : "border-border text-muted"}`}
                          />
                        </td>
                        <td className="p-2 text-right font-mono text-good">{fmtPrice(yes?.market_price)}</td>
                        <td className="p-2 text-right font-mono text-bad">{fmtPrice(no?.market_price)}</td>
                        <td className={`p-2 text-right font-mono ${o.thin_market ? "text-warn" : o.volume_usd ? "" : "text-muted"}`}
                            title={[o.volume_source ? VOLUME_SOURCE_LABEL[o.volume_source] : "source unknown",
                                    o.last_trade_at ? `last trade ${fmtAge(o.last_trade_at)}` : "no trades in the window"].join(" · ")}>
                          {fmtCompactUsd(o.volume_usd)}{o.thin_market ? " ⚠" : ""}
                        </td>
                        <td className={`p-2 text-right font-mono ${capped ? "text-warn" : "text-muted"}`}
                            title={yes?.ask_levels_source ? `ladder: ${LADDER_SOURCE_LABEL[yes.ask_levels_source]}` : ""}>
                          {fmtUsd(yes?.fillable_usd_5c)}
                        </td>
                        <td className="p-2 text-right">
                          <input inputMode="decimal" value={yesStakes[o.band_id] ?? ""}
                            onChange={(e) => setYesStakes((s) => ({ ...s, [o.band_id]: e.target.value }))}
                            placeholder="—" disabled={!yes?.market_price}
                            className="w-20 rounded border border-good/40 bg-good/5 px-1.5 py-0.5 text-right font-mono text-xs text-text disabled:opacity-30" />
                        </td>
                        <td className="p-2 text-right">
                          <input inputMode="decimal" value={noStakes[o.band_id] ?? ""}
                            onChange={(e) => setNoStakes((s) => ({ ...s, [o.band_id]: e.target.value }))}
                            placeholder="—" disabled={!no?.market_price}
                            className="w-20 rounded border border-bad/40 bg-bad/5 px-1.5 py-0.5 text-right font-mono text-xs text-text disabled:opacity-30" />
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
                <tfoot>
                  <tr className="border-t-2 border-border bg-panel2 text-[11px]">
                    <td className="p-2 font-semibold">
                      Sum of Yes across {board.bands.length} buckets
                      {diagnosis?.short && (
                        <span className="ml-1 text-warn" title={`The widest ladder on this day has ${diagnosis.widest} buckets. A partial ladder cannot sum to 100c.`}>
                          (incomplete)
                        </span>
                      )}
                    </td>
                    <td className={`p-2 text-right font-mono font-semibold ${
                      book === null ? "text-muted" : book < 0.97 ? "text-good" : book > 1.03 ? "text-warn" : "text-text"}`}>
                      {book === null ? "—" : fmtPct(book, 1)}
                    </td>
                    <td colSpan={8} className="p-2 text-muted">
                      {book === null ? "not enough priced buckets"
                        : book < 0.97 ? `${fmtPct(1 - book, 1)} under par — buying the whole set is a guaranteed payoff before fees. Size to the thinnest leg.`
                        : book > 1.03 ? `${fmtPct(book - 1, 1)} over par — the book charges a premium to hold any side.`
                        : "coherent — exactly one bucket pays $1 and the prices agree"}
                    </td>
                  </tr>
                </tfoot>
              </table>
            </div>

            {/* ---- if the day lands on ... -------------------------------- */}
            {staked ? (
              <OutcomeLadder result={result} unit={unit} />
            ) : (
              <div className="rounded border border-dashed border-border p-4 text-center text-xs leading-relaxed text-muted">
                <b className="text-text">Stake a bucket to see the ticket.</b> Type dollars in{" "}
                <span className="text-good">Bet Yes</span> or <span className="text-bad">Bet No</span>{" "}
                and this becomes a ladder of what the whole position returns under every outcome —
                which is the only view that shows what a NO leg does, since one No stake pays out on
                every bucket except its own.
              </div>
            )}
          </div>
        )}
      </DataState>

      <p className="max-w-3xl text-[11px] leading-relaxed text-muted">
        Nothing here places an order. Fills are capped at each bucket&apos;s measured{" "}
        <b>Depth</b> — a stake larger than the book buys the book, not the stake, and the ticket says
        so rather than quoting an imaginary fill. Fees are the real taker schedule,{" "}
        <code>shares × 0.05 × p × (1 − p)</code>, taken out of the stake. Resting a limit order
        instead pays zero and earns a rebate.
      </p>
    </div>
  );
}

/**
 * "If the day lands on ..." - one row per bucket, the whole ticket's P&L.
 * The bar is centred: profit right of the line, loss left, scaled to the
 * biggest swing on the ticket so the shape of the position is readable at a
 * glance rather than requiring the numbers to be compared.
 */
function OutcomeLadder({ result, unit }: { result: ReturnType<typeof solveBoard>; unit: Unit }) {
  const span = Math.max(Math.abs(result.worst), Math.abs(result.best), 1);
  return (
    <div className="rounded border border-border bg-panel p-3">
      <div className="mb-2 flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="text-sm font-semibold">If the day lands on…</h3>
        <div className="flex flex-wrap gap-x-4 gap-y-1 font-mono text-[11px]">
          <span><span className="text-muted">cost </span>{fmtUsd(result.cost)}
            {result.anyCapped && (
              <span className="ml-1 text-warn" title={`You asked for ${fmtUsd(result.requested)}. The rest is more than the book can absorb inside 5c.`}>
                of {fmtUsd(result.requested)} requested
              </span>
            )}
          </span>
          <span><span className="text-muted">worst </span>
            <span className={result.worst >= 0 ? "text-good" : "text-bad"}>{fmtUsd(result.worst, { signed: true })}</span>
          </span>
          <span><span className="text-muted">best </span>
            <span className="text-good">{fmtUsd(result.best, { signed: true })}</span>
          </span>
          {result.ev !== null && (
            <span title="Sum of probability x P&L across every outcome, using Your % where you set it and the model's otherwise.">
              <span className="text-muted">EV </span>
              <span className={result.ev > 0 ? "text-good" : "text-bad"}>{fmtUsd(result.ev, { signed: true })}</span>
            </span>
          )}
        </div>
      </div>

      {result.locked && (
        <div className="mb-2 rounded border border-good/40 bg-good/10 px-2 py-1.5 text-[11px] text-good">
          <b>Locked.</b> Every outcome on this ticket makes money — the weather cannot take it away.
          It is only a lock if every leg actually fills, so size to the thinnest one.
        </div>
      )}

      <div className="space-y-1">
        {result.outcomes.map((o) => {
          const pos = o.pnl >= 0;
          const w = (Math.abs(o.pnl) / span) * 48;   // half-width percentage
          return (
            <div key={o.band_id} className="flex items-center gap-2 text-[11px]">
              <span className="w-24 shrink-0 truncate font-mono text-muted">{o.label}</span>
              <span className="w-12 shrink-0 text-right font-mono text-muted"
                    title="The probability this outcome is weighted by.">
                {o.prob === null ? "—" : fmtPct(o.prob, 0)}
              </span>
              <div className="relative h-3 flex-1 rounded bg-panel2">
                <div className="absolute inset-y-0 left-1/2 w-px bg-border" />
                <div
                  className={`absolute inset-y-0 rounded ${pos ? "bg-good/70" : "bg-bad/70"}`}
                  style={pos ? { left: "50%", width: `${w}%` } : { right: "50%", width: `${w}%` }}
                />
              </div>
              <span className={`w-20 shrink-0 text-right font-mono ${pos ? "text-good" : "text-bad"}`}>
                {fmtUsd(o.pnl, { signed: true })}
              </span>
            </div>
          );
        })}
      </div>

      <p className="mt-2 text-[10px] leading-relaxed text-muted">
        Exactly one bucket resolves Yes. A <span className="text-bad">Bet No</span> pays out on every
        row except its own, which is why one No stake moves the whole ladder.
      </p>
    </div>
  );
}
