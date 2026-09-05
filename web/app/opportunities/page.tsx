"use client";

import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { supabase } from "@/lib/supabase";
import { useQuery } from "@/lib/useQuery";
import { DataState } from "@/components/DataState";
import RefreshButton from "@/components/RefreshButton";
import { fmtAge, fmtCompactUsd, fmtPct, fmtPp, fmtPrice, fmtUsd, regimeColor } from "@/lib/format";
import { fmtBandRange, fmtTemp, fmtTempDelta, type Unit } from "@/lib/units";
import { fmtDaysAhead, fmtResolutionDate } from "@/lib/time";
import { buy, feeRateAt } from "@/lib/costs";
import type { Opportunity, OpportunityContext } from "@/lib/types";

/**
 * An opportunity is a TRADE, not a row of statistics.
 *
 * The cards here used to open with a rank number and then list six metrics -
 * price, model probability, edge, depth, volume, trades - in a uniform grid,
 * leaving the reader to work out what any of it was proposing. Every card
 * looked the same whether the edge was 2pp or 20pp, and none of them said
 * what to actually do.
 *
 * Each card now leads with the sentence: buy this side, of this band, in this
 * city, at this price, because the model says it is worth more. Then the
 * money - what a $100 stake risks and returns. Then the reasons to doubt it,
 * which are the part that decides whether it gets taken.
 */

const STAKE_PRESETS = [50, 100, 250, 500];

interface LiveRow { city_key: string; temp_c: number | null; running_max_c: number | null }

export default function OpportunitiesPage() {
  const router = useRouter();
  // Starts at -10pp, i.e. showing everything. It used to start at 0, which
  // silently hid every band the model rates BELOW its market price - and on a
  // fairly-priced day that is most of them, so the page just went blank with
  // no way to tell that from having no data at all.
  const [minEdge, setMinEdge] = useState(-10);
  const [minVolume, setMinVolume] = useState(0);
  const [stake, setStake] = useState(100);
  const [dayFilter, setDayFilter] = useState("");
  const [showBlocked, setShowBlocked] = useState(false);
  const [onlyUnrepriced, setOnlyUnrepriced] = useState(false);

  // Everything is fetched, tradeable or not, so the page can tell you WHY it
  // is empty. Filtering in the query made "no opportunities" and "every
  // opportunity is blocked" look identical, and they need different actions.
  const q = useQuery<Opportunity[]>(
    () =>
      supabase
        .from("v_opportunities")
        .select("*")
        .order("score", { ascending: false, nullsFirst: false })
        .limit(400),
    [],
    30000
  );
  const live = useQuery<LiveRow[]>(
    () => supabase.from("live_weather").select("city_key,temp_c,running_max_c"),
    [],
    60000
  );
  // How the price and the forecast have MOVED. An edge is a still photograph
  // without this: it cannot say whether the market is coming round or walking
  // away, nor whether the price was even set against the current forecast.
  const ctxQ = useQuery<OpportunityContext[]>(
    () => supabase.from("v_opportunity_context").select("*").limit(4000),
    [],
    60000
  );

  const allRows = q.data ?? [];
  const blocked = allRows.filter((r) => !r.tradeable);
  const rows = showBlocked ? allRows : allRows.filter((r) => r.tradeable);
  const liveByCity = useMemo(
    () => new Map((live.data ?? []).map((l) => [l.city_key, l])),
    [live.data]
  );
  const ctxByBand = useMemo(
    () => new Map((ctxQ.data ?? []).map((c) => [c.band_id, c])),
    [ctxQ.data]
  );
  const unrepriced = useMemo(
    () => (ctxQ.data ?? []).filter((c) => c.forecast_ahead_of_book).length,
    [ctxQ.data]
  );

  const days = useMemo(
    () => Array.from(new Set(rows.map((r) => r.resolution_date))).sort(),
    [rows]
  );

  const filtered = rows.filter(
    (r) =>
      (r.edge_net_pp ?? 0) >= minEdge / 100 &&
      (r.volume_usd ?? 0) >= minVolume &&
      (!dayFilter || r.resolution_date === dayFilter) &&
      (!onlyUnrepriced || ctxByBand.get(r.band_id)?.forecast_ahead_of_book === true)
  );

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold">Opportunities</h1>
          <p className="mt-1 max-w-3xl text-xs leading-relaxed text-muted">
            Every band where the model and the market disagree by more than the cost of trading,
            best first. Rank is{" "}
            <code>edge × confidence × ln(1 + depth) × volume/(volume + k)</code> — a large edge on a
            book you cannot fill ranks below a modest edge you can. The volume term only ever
            discounts; it never inflates a rank.
          </p>
        </div>
        <RefreshButton
          job="P0.3_book_volume_snapshot"
          label="Refresh books"
          onDone={() => { q.refresh(); live.refresh(); }}
        />
      </div>

      {/* ---- controls: sized in the money, not in abstractions ------------ */}
      <div className="flex flex-wrap items-center gap-x-6 gap-y-2 rounded border border-border bg-panel px-3 py-2 text-sm">
        <label className="flex items-center gap-2">
          <span className="text-muted">Stake</span>
          {STAKE_PRESETS.map((s) => (
            <button
              key={s}
              onClick={() => setStake(s)}
              className={`rounded border px-2 py-0.5 font-mono text-xs ${
                stake === s ? "border-accent bg-accent/10 text-accent" : "border-border text-muted hover:text-text"
              }`}
            >
              ${s}
            </button>
          ))}
        </label>
        <label className="flex items-center gap-2" title="Net of fees. Negative values show bands the model rates BELOW their market price - which is most of them on a fairly-priced day.">
          <span className="text-muted">Min edge</span>
          <input type="range" min={-10} max={25} step={1} value={minEdge} onChange={(e) => setMinEdge(parseFloat(e.target.value))} />
          <span className="w-12 font-mono text-xs">{minEdge > 0 ? "+" : ""}{minEdge}pp</span>
        </label>
        <label className="flex items-center gap-1.5 text-xs text-muted" title="Bands the engine refused to trade - too wide, too thin, no book, or a stale price. Each one shows its reason.">
          <input type="checkbox" checked={showBlocked} onChange={(e) => setShowBlocked(e.target.checked)} />
          show blocked ({blocked.length})
        </label>
        <label
          className={`flex items-center gap-1.5 text-xs ${unrepriced > 0 ? "text-accent" : "text-muted"}`}
          title="Bands whose forecast changed AFTER the market last repriced. The price on screen was set against older information than the desk is holding - the only kind of edge with a cause behind it."
        >
          <input type="checkbox" checked={onlyUnrepriced} onChange={(e) => setOnlyUnrepriced(e.target.checked)} />
          market hasn&apos;t repriced ({unrepriced})
        </label>
        <label className="flex items-center gap-2" title="Filter out bands that have barely traded in the last 24h.">
          <span className="text-muted">Min 24h volume</span>
          <input type="range" min={0} max={5000} step={100} value={minVolume} onChange={(e) => setMinVolume(parseFloat(e.target.value))} />
          <span className="w-14 font-mono text-xs">{fmtCompactUsd(minVolume)}</span>
        </label>
        {days.length > 1 && (
          <select
            value={dayFilter}
            onChange={(e) => setDayFilter(e.target.value)}
            className="rounded border border-border bg-panel2 px-2 py-1 text-xs"
          >
            <option value="">Any settlement day</option>
            {days.map((d) => (
              <option key={d} value={d}>{fmtResolutionDate(d)} — {fmtDaysAhead(d)}</option>
            ))}
          </select>
        )}
        <span className="ml-auto font-mono text-[11px] text-muted">
          {filtered.length} of {rows.length} shown
          {blocked.length > 0 && !showBlocked ? ` · ${blocked.length} blocked` : ""}
        </span>
      </div>

      <DataState
        loading={q.loading}
        error={q.error}
        isEmpty={allRows.length === 0}
        emptyTitle="No opportunities yet"
        emptyBody={
          <>
            <code>v_opportunities</code> returned nothing at all — not even blocked rows. It is built
            from <code>edges</code>, so run GitHub Actions → <b>Probabilities</b>. If only one city
            appears when you expect many, the gap is upstream in <code>markets</code>, which P0.2
            Market Discovery fills.
          </>
        }
        onRetry={q.refresh}
      >
        {filtered.length === 0 ? (
          // Empty for a REASON, and the reason decides what to do about it.
          <div className="space-y-2 rounded border border-dashed border-border p-6 text-center text-sm text-muted">
            {rows.length === 0 && blocked.length > 0 ? (
              <>
                <div className="font-semibold text-warn">
                  Every one of the {blocked.length} priced bands is blocked from trading.
                </div>
                <div className="mx-auto max-w-lg leading-relaxed">
                  Nothing here is a filter — the edge engine refused all of them. Common causes: no
                  book snapshot yet (P0.3), a spread wider than the tradeability thresholds, or a
                  stale price. Tick <b>show blocked</b> to see each one&apos;s reason.
                </div>
                <button onClick={() => setShowBlocked(true)} className="rounded border border-accent px-2.5 py-1 text-xs text-accent hover:bg-accent/10">
                  Show me why
                </button>
              </>
            ) : (
              <>
                <div>
                  {rows.length} opportunit{rows.length === 1 ? "y" : "ies"} loaded, none matching
                  these filters.
                </div>
                <button
                  onClick={() => { setMinEdge(-10); setMinVolume(0); setDayFilter(""); }}
                  className="rounded border border-accent px-2.5 py-1 text-xs text-accent hover:bg-accent/10"
                >
                  Clear filters
                </button>
              </>
            )}
          </div>
        ) : (
          <div className="grid gap-3 lg:grid-cols-2 2xl:grid-cols-3">
            {filtered.map((o, i) => (
              <Card
                key={`${o.band_id}-${o.side}`}
                o={o}
                rank={i + 1}
                stake={stake}
                lw={liveByCity.get(o.city_key)}
                ctx={ctxByBand.get(o.band_id)}
                onOpen={() => router.push(`/board?city=${encodeURIComponent(o.city_key)}&date=${o.resolution_date}`)}
              />
            ))}
          </div>
        )}
      </DataState>
    </div>
  );
}

/**
 * A price move, coloured by whether it helps THIS side.
 *
 * A band drifting up is good news for a YES holder and bad for a NO holder,
 * so the same number is green on one card and red on another. Colouring by
 * direction alone would tell half the readers the opposite of the truth.
 */
function Drift({ label, v, side }: { label: string; v: number; side: string }) {
  const helps = side === "YES" ? v > 0 : v < 0;
  const flat = Math.abs(v) < 0.005;
  return (
    <span
      className={flat ? "text-muted" : helps ? "text-good" : "text-bad"}
      title={
        flat
          ? `Unchanged over ${label}.`
          : helps
          ? `Moved ${Math.abs(v * 100).toFixed(1)}c toward this side over ${label} - the market is coming round.`
          : `Moved ${Math.abs(v * 100).toFixed(1)}c against this side over ${label}. A market walking away from the model is more often the model being wrong than the edge growing.`
      }
    >
      <span className="text-muted">{label} </span>
      {flat ? "flat" : `${v > 0 ? "▲" : "▼"}${Math.abs(v * 100).toFixed(1)}c`}
    </span>
  );
}

function Card({
  o, rank, stake, lw, ctx, onOpen,
}: {
  o: Opportunity;
  rank: number;
  stake: number;
  lw: LiveRow | undefined;
  ctx: OpportunityContext | undefined;
  onOpen: () => void;
}) {
  const unit = o.unit as Unit;
  const price = o.market_price;
  const band = o.band_label ?? fmtBandRange(o.band_lo, o.band_hi, unit, o.open_low, o.open_high);

  // What the trade actually risks and returns, in dollars, at this stake -
  // through the desk's own cost model, so this card and the board can
  // never quote different numbers for the same trade.
  const pos = price ? buy(stake, price, o.model_prob) : null;
  const profit = pos?.profit ?? null;
  const ev = pos?.ev ?? null;

  // Can this stake even fill? Depth is the binding constraint far more often
  // than edge is, and it was previously just another number in a grid.
  const depth = o.fillable_usd_5c ?? 0;
  const overDepth = depth > 0 && stake > depth;

  // Where the day's maximum is now, relative to the band being bought.
  const inBand =
    lw?.running_max_c != null &&
    (o.band_lo == null || lw.running_max_c >= o.band_lo) &&
    (o.band_hi == null || lw.running_max_c < o.band_hi);

  const doubts = [
    o.thin_market && "barely trades — the fill will move the price further than the ladder implies",
    overDepth && `only ${fmtUsd(depth)} fillable inside 5¢ — this stake is larger than the book`,
    o.ask_levels_source === "synthetic_tiers" && "book ladder reconstructed from depth totals, not the raw book",
    o.volume_stale && "the volume figure is from a snapshot older than three lookback windows",
    (o.confidence ?? 1) < 0.5 && `model confidence only ${fmtPct(o.confidence, 0)}`,
    o.regime_label === "UNCERTAIN" && "forecast regime is UNCERTAIN — sigma is already widened",
  ].filter(Boolean) as string[];

  return (
    <button
      onClick={onOpen}
      className={`flex flex-col rounded border bg-panel p-3 text-left transition hover:border-accent ${
        o.tradeable ? "border-border" : "border-warn/30 opacity-70"
      }`}
    >
      {/* ---- the trade, in one sentence ---------------------------------- */}
      <div className="flex items-start justify-between gap-2">
        <div>
          <div className="flex items-baseline gap-2">
            <span
              className={`rounded px-1.5 py-0.5 font-mono text-[11px] font-bold ${
                o.side === "YES" ? "bg-good/15 text-good" : "bg-bad/15 text-bad"
              }`}
            >
              BUY {o.side}
            </span>
            <span className="font-semibold">{o.display_name ?? o.city_key}</span>
            <span className="font-mono text-accent">{band}</span>
          </div>
          <div className="mt-0.5 text-[11px] text-muted">
            settles {fmtResolutionDate(o.resolution_date)} · {fmtDaysAhead(o.resolution_date)}
            {lw?.temp_c != null && <> · now {fmtTemp(lw.temp_c, unit)}</>}
            {lw?.running_max_c != null && <> · max {fmtTemp(lw.running_max_c, unit)}</>}
            {inBand && <span className="ml-1 text-warn">← inside this band</span>}
          </div>
        </div>
        <span className="shrink-0 font-mono text-[11px] text-muted">#{rank}</span>
      </div>

      {/* ---- has the market even seen this? ------------------------------
          The strongest form of edge is not "the model disagrees with the
          market" - it is "the market has not repriced since the forecast
          moved". That one has a cause behind it. */}
      {ctx?.forecast_ahead_of_book && (
        <div className="mt-2 rounded border border-accent/50 bg-accent/10 px-2 py-1.5 text-[11px] leading-relaxed text-accent">
          <b>The market hasn&apos;t repriced.</b> The forecast moved{" "}
          {ctx.forecast_move_c !== null && (
            <b>{ctx.forecast_move_c > 0 ? "+" : ""}{fmtTempDelta(ctx.forecast_move_c, unit)}</b>
          )}{" "}
          {ctx.forecast_lead_hours !== null && (
            <>{ctx.forecast_lead_hours.toFixed(1)}h after the last book snapshot</>
          )}
          . This price was set against older information than the desk is holding.
        </div>
      )}

      {/* ---- price vs value --------------------------------------------- */}
      <div className="mt-3 flex items-center gap-3 rounded bg-panel2 px-3 py-2">
        <div>
          <div className="text-[9px] uppercase tracking-wide text-muted">You pay</div>
          <div className="font-mono text-lg">{fmtPrice(price)}</div>
        </div>
        <div className="text-muted">→</div>
        <div>
          <div className="text-[9px] uppercase tracking-wide text-muted">Model says worth</div>
          <div className="font-mono text-lg text-accent">{fmtPct(o.model_prob, 0)}</div>
        </div>
        <div className="ml-auto text-right">
          <div className="text-[9px] uppercase tracking-wide text-muted">Net edge</div>
          <div className={`font-mono text-lg ${(o.edge_net_pp ?? 0) > 0 ? "text-good" : "text-bad"}`}>
            {fmtPp(o.edge_net_pp)}
          </div>
        </div>
      </div>

      {/* ---- the same thing in money ------------------------------------- */}
      <div className="mt-2 grid grid-cols-3 gap-2 font-mono text-xs">
        <div>
          <div className="text-[9px] uppercase tracking-wide text-muted">Risk</div>
          {fmtUsd(stake)}
        </div>
        <div>
          <div className="text-[9px] uppercase tracking-wide text-muted">If right</div>
          <span className="text-good">{profit === null ? "—" : fmtUsd(profit, { signed: true })}</span>
        </div>
        <div title={`Expected value at the model's own probability: p x profit - (1 - p) x stake. Positive is the whole point; it is not a promise. Taker fee here: ${pos ? fmtUsd(pos.fee) : "-"}, ${fmtPct(price ? feeRateAt(price) : 0, 2)} of notional.`}>
          <div className="text-[9px] uppercase tracking-wide text-muted">EV</div>
          <span className={ev !== null && ev > 0 ? "text-good" : "text-bad"}>
            {ev === null ? "—" : fmtUsd(ev, { signed: true })}
          </span>
        </div>
      </div>

      {/* ---- which way is it moving, and how long is left ---------------- */}
      {ctx && (ctx.drift_1h !== null || ctx.drift_24h !== null || ctx.hours_to_resolution !== null) && (
        <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-border pt-2 font-mono text-[10px]">
          {ctx.drift_1h !== null && <Drift label="1h" v={ctx.drift_1h} side={o.side} />}
          {ctx.drift_6h !== null && <Drift label="6h" v={ctx.drift_6h} side={o.side} />}
          {ctx.drift_24h !== null && <Drift label="24h" v={ctx.drift_24h} side={o.side} />}
          {ctx.hours_to_resolution !== null && (
            <span
              className={`ml-auto ${
                ctx.hours_to_resolution < 6 ? "text-warn" : "text-muted"
              }`}
              title="Hours until this market settles. An edge with hours left is a different trade from one with days - there is less time for the forecast to move, and less time to get out."
            >
              {ctx.hours_to_resolution < 0
                ? "settling"
                : `${ctx.hours_to_resolution.toFixed(0)}h to settle`}
            </span>
          )}
        </div>
      )}

      {/* ---- how much of it is actually available ------------------------ */}
      <div className="mt-2 flex items-center justify-between text-[11px]">
        <span className={overDepth ? "text-warn" : "text-muted"}>
          fillable {fmtUsd(depth)} inside 5¢
        </span>
        <span className={o.thin_market ? "text-warn" : "text-muted"}>
          {fmtCompactUsd(o.volume_usd)} traded 24h
          {o.last_trade_at ? ` · last ${fmtAge(o.last_trade_at)}` : ""}
        </span>
      </div>
      <div className="mt-1 h-1 overflow-hidden rounded bg-panel2">
        {/* how much of this stake the book can absorb */}
        <div
          className={`h-full ${overDepth ? "bg-warn" : "bg-good"}`}
          style={{ width: `${Math.min(100, depth > 0 ? (Math.min(stake, depth) / stake) * 100 : 0)}%` }}
        />
      </div>

      {/* ---- reasons to doubt it ----------------------------------------- */}
      {doubts.length > 0 && (
        <ul className="mt-2 space-y-0.5 border-t border-border pt-2 text-[10px] leading-relaxed text-warn">
          {doubts.map((d) => (
            <li key={d}>• {d}</li>
          ))}
        </ul>
      )}

      {!o.tradeable && (
        <div className="mt-2 rounded border border-warn/40 bg-warn/10 px-2 py-1.5 text-[10px] leading-relaxed text-warn">
          <b>Blocked by the edge engine — not tradeable.</b>{" "}
          {o.block_reason ? <code>{o.block_reason}</code> : "no reason recorded"}. The numbers above
          are the model&apos;s, but nothing will fill at them.
        </div>
      )}

      <div className="mt-2 flex items-center justify-between border-t border-border pt-2 text-[10px] text-muted">
        <span className={regimeColor(o.regime_label)}>{o.regime_label}</span>
        <span>confidence {fmtPct(o.confidence, 0)}</span>
        <span className="text-accent">open on the board →</span>
      </div>
    </button>
  );
}
