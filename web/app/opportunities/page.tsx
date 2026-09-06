"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { supabase } from "@/lib/supabase";
import { useQuery } from "@/lib/useQuery";
import { DataState } from "@/components/DataState";
import RefreshButton from "@/components/RefreshButton";
import { fmtAge, fmtCompactUsd, fmtPct, fmtPp, fmtPrice, fmtUsd, regimeColor } from "@/lib/format";
import { fmtBandRange, fmtTemp, fmtTempDelta, type Unit } from "@/lib/units";
import { fmtDaysAhead, fmtResolutionDate } from "@/lib/time";
import { feeRateAt } from "@/lib/costs";
import { fill, ladderFor, maxCleanStake, parseLevels, type BookRow, type Limits } from "@/lib/execution";
import { useExecutionLimits } from "@/lib/useExecutionLimits";
import { DayPath, LivePrices, StrategyMirror, WindowPill } from "@/components/TradeTiming";
import type { CityDayPlan, OpportunityContext, TradePlan } from "@/lib/types";

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
 *
 * WHAT THE SECOND PASS ADDED, and why the page was still not a trading page
 * without it. An edge answers "is this mispriced". It does not answer:
 *
 *   WHEN - the same 16pp is a trade forty minutes before the peak with the
 *   line still climbing, and a memory once the maximum is banked.
 *   WHO - which strategy would take it, and whether that strategy is even
 *   switched on. Nine ship disabled, so the desk could show a board full of
 *   mispricings while Signals read "every strategy is off", with nothing
 *   joining the two.
 *   HOW OLD - every edge here is computed against a book snapshot. Polling
 *   the database every thirty seconds does not make a three-hour-old book
 *   current; it makes a stale page look live.
 *
 * All three come from v_trade_plan (sql/ad4_34_trade_plan.sql), which is
 * v_opportunities plus the answers, computed against the same thresholds the
 * Python strategy engine reads.
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
  const q = useQuery<TradePlan[]>(
    () =>
      supabase
        .from("v_trade_plan")
        .select("*")
        .order("score", { ascending: false, nullsFirst: false })
        .limit(400),
    [],
    30000
  );
  // s8's cover is a PAIR of adjacent buckets, so it cannot live on a band row
  // - it is per city and day, and it is the one basket strategy the desk can
  // state completely without guessing.
  const coverQ = useQuery<CityDayPlan[]>(
    () => supabase.from("v_city_day_plan").select("*"),
    [],
    60000
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
  // THE ACTUAL BOOK, not a mid. Every "if right" and "EV" on this page used to
  // be computed at market_price - a midpoint - as though any quantity could be
  // bought there. You buy at the ask, and you buy down the ladder.
  const bookQ = useQuery<BookRow[]>(
    () => supabase.from("v_latest_book")
      .select("band_id,ask_levels,bid_levels,ask_levels_source,bid_levels_source,ask_depth_usd,bid_depth_usd,best_ask,best_bid"),
    [],
    30000
  );
  const allRows = q.data ?? [];
  // The rows a strategy would actually take, right now. This is the answer to
  // "what do I do in the next hour", and it is a different question from
  // "what is mispriced" - which is what the rest of the page ranks.
  const actionable = useMemo(
    () =>
      allRows
        .filter((r) => r.tradeable && (r.would_fire?.length ?? 0) > 0)
        .sort((a, b) => {
          const aw = a.in_entry_window ? 1 : 0;
          const bw = b.in_entry_window ? 1 : 0;
          if (aw !== bw) return bw - aw;
          const ae = a.would_fire_enabled?.length ?? 0;
          const be = b.would_fire_enabled?.length ?? 0;
          if (ae !== be) return be - ae;
          return (b.score ?? 0) - (a.score ?? 0);
        }),
    [allRows]
  );
  // Newest book behind any row on the page. Every edge is priced against one.
  const bookAge = useMemo(() => {
    const ages = allRows.map((r) => r.book_age_min).filter((a): a is number => a != null);
    return ages.length ? Math.min(...ages) : null;
  }, [allRows]);
  const covers = (coverQ.data ?? []).filter((c) => c.s8_would_fire || c.adjacent);
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
  const bookByBand = useMemo(
    () => new Map((bookQ.data ?? []).map((b) => [b.band_id, b])),
    [bookQ.data]
  );
  const { limits } = useExecutionLimits();
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
            best first. Every money figure on a card is the <strong className="text-text">fill you
            would actually get</strong>: the stake is walked through the real ask ladder, the
            venue&apos;s order minimum is applied, and what the book cannot absorb is not counted.
            Rank is{" "}
            <code>edge × confidence × ln(1 + depth) × volume/(volume + k)</code> — a large edge on a
            book you cannot fill ranks below a modest edge you can. The volume term only ever
            discounts; it never inflates a rank.
          </p>
        </div>
        <div className="flex flex-col items-end gap-1.5">
          <RefreshButton
            job="P0.3_book_volume_snapshot"
            label="Refresh books"
            onDone={() => { q.refresh(); live.refresh(); coverQ.refresh(); }}
          />
          <LivePrices
            ageMin={bookAge}
            everyMs={30000}
            loading={q.loading}
            onRefresh={() => { q.refresh(); live.refresh(); ctxQ.refresh(); coverQ.refresh(); }}
          />
        </div>
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

      {/* ---- what a strategy would actually take, right now -------------
          Separated from the ranked list on purpose. "What is mispriced" and
          "what do I do in the next hour" are different questions, and mixing
          them is why the old page could be full and still not tell you to do
          anything. */}
      {actionable.length > 0 && (
        <section className="rounded border border-accent/40 bg-accent/[0.04] p-3">
          <div className="mb-2 flex flex-wrap items-baseline gap-2">
            <h2 className="text-sm font-semibold text-accent">A strategy would take these now</h2>
            <span className="text-[11px] text-muted">
              {actionable.filter((r) => (r.would_fire_enabled?.length ?? 0) > 0).length} of{" "}
              {actionable.length} would reach Signals — the rest pass their entry test on a
              strategy that is switched off.
            </span>
            <Link href="/strategies" className="ml-auto text-[11px] text-accent hover:underline">
              Strategies →
            </Link>
          </div>
          <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-3">
            {actionable.slice(0, 6).map((r) => (
              <button
                key={`act-${r.band_id}-${r.side}`}
                onClick={() => router.push(`/board?city=${encodeURIComponent(r.city_key)}&date=${r.resolution_date}`)}
                className="rounded border border-border bg-panel p-2 text-left hover:border-accent"
              >
                <div className="flex items-baseline gap-2">
                  <span className={`font-mono text-[10px] font-bold ${r.side === "YES" ? "text-good" : "text-bad"}`}>
                    {r.side}
                  </span>
                  <span className="truncate text-xs font-semibold">{r.display_name ?? r.city_key}</span>
                  <span className="font-mono text-xs text-accent">{r.band_label}</span>
                  <span className="ml-auto"><WindowPill p={r} /></span>
                </div>
                <div className="mt-1 text-[11px] leading-relaxed text-muted">{r.action}</div>
                <StrategyMirror p={r} />
              </button>
            ))}
          </div>
        </section>
      )}

      {/* ---- the pair cover, which is per city-day and not per band ------ */}
      {covers.length > 0 && <CoverStrip rows={covers} />}

      <DataState
          relation="v_trade_plan"
        loading={q.loading}
        error={q.error}
        isEmpty={allRows.length === 0}
        emptyTitle="No opportunities yet"
        emptyBody={
          <>
            <code>v_trade_plan</code> returned nothing at all — not even blocked rows. It is built
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
                book={bookByBand.get(o.band_id)}
                limits={limits}
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
  o, rank, stake, lw, ctx, book, limits, onOpen,
}: {
  o: TradePlan;
  rank: number;
  stake: number;
  lw: LiveRow | undefined;
  ctx: OpportunityContext | undefined;
  book: BookRow | undefined;
  limits: Limits;
  onOpen: () => void;
}) {
  const unit = o.unit as Unit;
  const price = o.market_price;
  const band = o.band_label ?? fmtBandRange(o.band_lo, o.band_hi, unit, o.open_low, o.open_high);

  // WHAT THE TRADE ACTUALLY RETURNS. This used to be buy(stake, market_price)
  // - a midpoint, with unlimited depth behind it. Both halves were wrong: you
  // lift the ask, and you lift it DOWN THE LADDER, so a $500 ticket against a
  // $120 book returns what $120 of shares return, not what $500 does. The
  // ladder walk lives in lib/execution.ts and is unit-tested against the same
  // fee model the paper engine settles with.
  const side = o.side === "YES" ? "ask" : "bid";
  const rawLevels = parseLevels(side === "ask" ? book?.ask_levels : book?.bid_levels, o.side);
  const quoted = o.side === "YES" ? o.best_ask : (o.best_bid != null ? 1 - o.best_bid : null);
  const depthUsd = o.side === "YES" ? (book?.ask_depth_usd ?? o.fillable_usd_5c) : (book?.bid_depth_usd ?? o.fillable_usd_5c);
  const { levels, known: bookKnown } = ladderFor(rawLevels, quoted, depthUsd, limits);
  const pos = fill(stake, levels, bookKnown, o.model_prob, limits);
  const profit = pos.shares > 0 ? pos.profit : null;
  const ev = pos.ev;
  const cleanMax = levels.length ? maxCleanStake(levels, limits) : 0;

  // Can this stake even fill? Depth is the binding constraint far more often
  // than edge is, and it was previously just another number in a grid.
  const depth = o.fillable_usd_5c ?? 0;
  const overDepth = !pos.complete && pos.shares > 0;

  // Where the day's maximum is now, relative to the band being bought.
  const inBand =
    lw?.running_max_c != null &&
    (o.band_lo == null || lw.running_max_c >= o.band_lo) &&
    (o.band_hi == null || lw.running_max_c < o.band_hi);

  const doubts = [
    o.thin_market && "barely trades — the fill will move the price further than the ladder implies",
    overDepth && `only ${fmtUsd(pos.spent)} of this ${fmtUsd(stake)} stake actually fills — the book runs out`,
    pos.problems.includes("below_minimum") && `under the venue's ${fmtUsd(limits.minOrderUsd)} order minimum — this would be rejected, not filled small`,
    pos.problems.includes("no_book") && "no stored ladder for this side, so the fill above is priced off top-of-book and a depth total",
    pos.slippage != null && pos.slippage > 0.01 && `walking the book costs ${(pos.slippage * 100).toFixed(1)}¢ a share on top of the quote`,
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
        <div className="flex shrink-0 flex-col items-end gap-1">
          <span className="font-mono text-[11px] text-muted">#{rank}</span>
          <WindowPill p={o} />
        </div>
      </div>

      {/* ---- the sentence: what to do, and why now ----------------------- */}
      {o.action && (
        <div
          className={`mt-2 rounded px-2 py-1.5 text-[11px] leading-relaxed ${
            (o.would_fire?.length ?? 0) > 0
              ? "border border-accent/40 bg-accent/10 text-accent"
              : "bg-panel2 text-muted"
          }`}
        >
          {o.action}
        </div>
      )}

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
        <div title={
          pos.avgPrice != null
            ? `The average price this stake actually fills at, walking the real ladder. Top of book is ${fmtPrice(pos.topPrice)}; the mid is ${fmtPrice(price)}.`
            : "No fill is possible at this size."
        }>
          <div className="text-[9px] uppercase tracking-wide text-muted">You pay</div>
          <div className="font-mono text-lg">
            {pos.avgPrice != null ? fmtPrice(pos.avgPrice) : fmtPrice(price)}
          </div>
          {pos.avgPrice != null && pos.topPrice != null && pos.avgPrice - pos.topPrice > 0.001 && (
            <div className="text-[9px] text-warn">top {fmtPrice(pos.topPrice)}</div>
          )}
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

      {/* ---- the same thing in money, at the fill you would actually get -- */}
      <div className="mt-2 grid grid-cols-3 gap-2 font-mono text-xs">
        <div title={`Requested ${fmtUsd(stake)}. This is what actually leaves the account once the order is walked through the real book and the venue's minimum is applied.`}>
          <div className="text-[9px] uppercase tracking-wide text-muted">Risk</div>
          <span className={pos.spent > 0 && pos.spent < stake - 0.02 ? "text-warn" : ""}>
            {pos.shares > 0 ? fmtUsd(pos.spent) : "—"}
          </span>
          {pos.shares > 0 && pos.spent < stake - 0.02 && (
            <div className="text-[9px] text-warn">of {fmtUsd(stake)}</div>
          )}
        </div>
        <div title="One dollar per share, minus what the fill cost. Computed on the shares actually obtainable, not on the requested stake.">
          <div className="text-[9px] uppercase tracking-wide text-muted">If right</div>
          <span className="text-good">{profit === null ? "—" : fmtUsd(profit, { signed: true })}</span>
          {pos.shares > 0 && <div className="text-[9px] text-muted">{pos.shares.toFixed(0)} sh</div>}
        </div>
        <div title={`Expected value at the model's own probability, on the fill: p x profit - (1 - p) x spent. Taker fee on this fill: ${fmtUsd(pos.fee)}, ${fmtPct(pos.avgPrice ? feeRateAt(pos.avgPrice) : 0, 2)} of notional.`}>
          <div className="text-[9px] uppercase tracking-wide text-muted">EV</div>
          <span className={ev !== null && ev > 0 ? "text-good" : "text-bad"}>
            {ev === null ? "—" : fmtUsd(ev, { signed: true })}
          </span>
        </div>
      </div>

      {/* ---- what the fill actually looks like ---------------------------- */}
      <div className={`mt-1 rounded px-2 py-1 text-[10px] leading-relaxed ${
        pos.shares === 0 ? "bg-bad/10 text-bad" : pos.complete ? "bg-panel2 text-muted" : "bg-warn/10 text-warn"
      }`}>
        {pos.note}
        {pos.shares > 0 && Number.isFinite(cleanMax) && cleanMax > 0 && (
          <span className="text-muted">
            {" "}· biggest clean ticket here {fmtUsd(cleanMax)}
          </span>
        )}
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

      {/* ---- how much of it is actually available ------------------------
          Two different facts, and they used to be shown as one. "Fillable
          inside 5c" is the edge engine's estimate against the top of book;
          the ladder is what is really quoted. Where both exist the ladder
          wins, because it is the thing the fill above was computed from. */}
      <div className="mt-2 flex items-center justify-between text-[11px]">
        <span className={overDepth ? "text-warn" : "text-muted"}>
          {bookKnown
            ? `book holds ${fmtUsd(cleanMax)} across ${levels.length} level${levels.length === 1 ? "" : "s"}`
            : `est. ${fmtUsd(depth)} fillable inside 5¢ — no stored ladder`}
        </span>
        <span className={o.thin_market ? "text-warn" : "text-muted"}>
          {fmtCompactUsd(o.volume_usd)} traded 24h
          {o.last_trade_at ? ` · last ${fmtAge(o.last_trade_at)}` : ""}
        </span>
      </div>
      <div
        className="mt-1 h-1 overflow-hidden rounded bg-panel2"
        title={`How much of the ${fmtUsd(stake)} you asked for actually fills at the quoted ladder.`}
      >
        <div
          className={`h-full ${pos.complete ? "bg-good" : "bg-warn"}`}
          style={{ width: `${Math.min(100, stake > 0 ? (pos.spent / stake) * 100 : 0)}%` }}
        />
      </div>

      {/* ---- where the day is heading, relative to THIS band ------------- */}
      <DayPath p={o} />

      {/* ---- who would take it ------------------------------------------- */}
      <StrategyMirror p={o} />

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


/**
 * s8's two-bucket cover. It buys the two most likely ADJACENT buckets when the
 * pair costs less than 70c including fees and one of them holds where the day
 * is actually heading - so it is a property of a city-day, not of a band, and
 * it has no home on a card.
 *
 * Rows that do NOT qualify are shown too, with the one reason. "The two most
 * likely buckets are not neighbours" is a fact about today's distribution
 * worth knowing; hiding it leaves the operator wondering whether the strategy
 * is broken or just quiet.
 */
function CoverStrip({ rows }: { rows: CityDayPlan[] }) {
  const [open, setOpen] = useState(false);
  const firing = rows.filter((r) => r.s8_would_fire);
  const shown = open ? rows : firing;
  if (shown.length === 0 && !open) {
    return (
      <button
        onClick={() => setOpen(true)}
        className="w-full rounded border border-dashed border-border px-3 py-1.5 text-left text-[11px] text-muted hover:text-text"
      >
        Two-bucket cover (s8): nothing qualifies on {rows.length} city-day
        {rows.length === 1 ? "" : "s"} — show why
      </button>
    );
  }
  return (
    <section className="rounded border border-border bg-panel">
      <div className="flex items-baseline gap-2 border-b border-border px-3 py-1.5">
        <h2 className="text-sm font-semibold">Two-bucket cover</h2>
        <span className="text-[11px] text-muted">
          the two most likely neighbouring buckets, bought together — s8
        </span>
        <button onClick={() => setOpen((o) => !o)} className="ml-auto text-[11px] text-accent hover:underline">
          {open ? "only the ones that qualify" : `show all ${rows.length}`}
        </button>
      </div>
      <div className="divide-y divide-border">
        {shown.map((r) => (
          <div key={`${r.city_key}-${r.resolution_date}`} className="flex flex-wrap items-baseline gap-x-3 gap-y-1 px-3 py-1.5 text-xs">
            <span className="font-semibold">{r.city_key}</span>
            <span className="font-mono text-accent">
              {r.label_a} + {r.label_b}
            </span>
            <span className="font-mono text-muted">
              costs {r.pair_cost_with_fee == null ? "—" : `${(r.pair_cost_with_fee * 100).toFixed(0)}c`} with fees
            </span>
            <span className="font-mono text-muted">
              covers {r.pair_prob == null ? "—" : `${(r.pair_prob * 100).toFixed(0)}%`}
            </span>
            {r.thinner_leg_usd != null && (
              <span className="font-mono text-muted" title="The thinner of the two legs. A cover only fills if BOTH sides do.">
                thinner leg {fmtUsd(r.thinner_leg_usd)}
              </span>
            )}
            <span className={`ml-auto text-[11px] ${r.s8_would_fire ? "text-good" : "text-muted"}`}>
              {r.pair_note}
            </span>
          </div>
        ))}
      </div>
    </section>
  );
}
