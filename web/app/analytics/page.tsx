"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { supabase } from "@/lib/supabase";
import { useQuery } from "@/lib/useQuery";
import { useCityStats } from "@/lib/useCityStats";
import StatsNotice from "@/components/StatsNotice";
import ModelAnalytics from "@/components/ModelAnalytics";
import { DataState, InlineError } from "@/components/DataState";
import { Freshness, FreshnessRow } from "@/components/Provenance";
import { Empty, Histogram, LineChart, Scatter } from "@/components/charts";
import { fmtCompactUsd, fmtInt, fmtPct, fmtPrice, fmtUsd, pnlColor } from "@/lib/format";
import { fmtTemp, type Unit } from "@/lib/units";
import { fmtDateTime, fmtDaysAhead, fmtResolutionDate } from "@/lib/time";
import { heatColor } from "@/lib/heat";
import { histogram, simulate, type McBand } from "@/lib/montecarlo";
import type { CityStats, Opportunity, StrategyBoardRow } from "@/lib/types";

const MIN_TRADES_FOR_RATE = 20; // matches scripts/backtest/metrics.py's own threshold
const RUNS = 20000;

interface SkillRow { city_key: string; mae_c: number; bias_c: number; mae_bands: number; n_days: number; lead_days: number }
interface ProbRow { band_id: string; forecast_max_c: number | null; sigma_c: number | null; bias_applied_c: number | null; computed_at: string }
interface TradeRow { strategy_id: string; net_pnl: number | null; closed_at: string | null }

export default function AnalyticsPage() {
  const skillQ = useQuery<SkillRow[]>(
    () =>
      supabase.from("derived_forecast_skill")
        .select("city_key,mae_c,bias_c,mae_bands,n_days,lead_days,computed_at")
        .eq("lead_days", 1).eq("evidence_scope", "verified_outcomes_v1")
        .order("computed_at", { ascending: false }).limit(500),
    []
  );
  const oppQ = useQuery<Opportunity[]>(
    () => supabase.from("v_opportunities").select("*").limit(4000), [], 60000, 4000
  );
  const statsQ = useCityStats(60000);
  const tradesQ = useQuery<TradeRow[]>(
    () => supabase.from("paper_trades").select("strategy_id,net_pnl,gross_pnl,closed_at").not("closed_at", "is", null).order("closed_at", { ascending: true }),
    []
  );
  // The roster with its own record. Attribution used to show an empty box on a
  // desk that has not traded, which reads as broken; the roster turns that into
  // the reason - every strategy is off, or firing and not filling.
  const boardQ = useQuery<StrategyBoardRow[]>(
    () => supabase.from("v_strategy_board").select("*"), [], 60000
  );
  const signalsQ = useQuery<Array<{ strategy_id: string }>>(
    () => supabase.from("signals").select("strategy_id").gte("fired_at", new Date(Date.now() - 7 * 864e5).toISOString()),
    []
  );

  const skill = useMemo(() => {
    const latest = new Map<string, SkillRow>();
    for (const r of skillQ.data ?? []) if (!latest.has(r.city_key)) latest.set(r.city_key, r);
    return Array.from(latest.values()).sort((a, b) => a.mae_c - b.mae_c);
  }, [skillQ.data]);

  const opps = oppQ.data ?? [];

  /**
   * What the book charges, per city-day. Computable with nothing but a book
   * snapshot - no trades, no settled outcomes - which is why it belongs in
   * group 2 rather than waiting behind a P&L curve that may be months away.
   *
   * Exactly one bucket pays, so the YES prices should sum to $1. The excess is
   * the house's margin and comes out of every edge on that board before the
   * desk sees a cent of it.
   */
  const bookCost = useMemo(() => {
    const g = new Map<string, { city: string; day: string; prices: number[]; spreads: number[]; tradeable: number }>();
    for (const o of opps) {
      if (o.side !== "YES" || o.market_price == null) continue;
      const key = `${o.city_key}|${o.resolution_date}`;
      const e = g.get(key) ?? { city: o.display_name ?? o.city_key, day: o.resolution_date, prices: [], spreads: [], tradeable: 0 };
      e.prices.push(o.market_price);
      if (o.spread != null) e.spreads.push(o.spread);
      if (o.tradeable) e.tradeable += 1;
      g.set(key, e);
    }
    const median = (xs: number[]) => {
      if (!xs.length) return 0;
      const s = [...xs].sort((a, b) => a - b);
      return s[Math.floor(s.length / 2)];
    };
    return Array.from(g.entries())
      // A partial ladder has a meaningless sum: three of eleven buckets always
      // total well under $1 and would read as a free arbitrage.
      .filter(([, v]) => v.prices.length >= 5)
      .map(([key, v]) => {
        const sum = v.prices.reduce((a, b) => a + b, 0);
        const spread = median(v.spreads);
        const verdict =
          sum < 0.99 ? "under $1 — a combination arb, if both legs fill"
          : v.tradeable === 0 ? "priced but nothing passes the edge engine"
          : spread > 0.08 ? "wide — you cross most of the edge getting in and out"
          : sum > 1.1 ? "expensive book — the margin eats the edge"
          : "normal book";
        return { key, city: v.city, day: v.day, n: v.prices.length, sum, spread, tradeable: v.tradeable, verdict };
      })
      .sort((a, b) => a.sum - b.sum);
  }, [opps]);
  const yes = useMemo(
    () => opps.filter((o) => o.side === "YES" && o.market_price !== null && o.model_prob !== null),
    [opps]
  );

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-lg font-semibold">Analytics</h1>
        <p className="mt-1 max-w-3xl text-xs leading-relaxed text-muted">
          Four questions, in the order a desk actually asks them: is the forecast any good, is the
          pricing any good, did it make money, and can it take size. Every chart reads the same
          tables the trading pages do — nothing here is a second calculation of something computed
          elsewhere. The first two groups need no trades at all; the third and fourth are marked,
          so an empty desk reads as early rather than broken.
        </p>
      </div>

      <StatsNotice mode={statsQ.mode} reason={statsQ.reason} viewError={statsQ.viewError} />

      <GroupHeading
        n={1}
        title="Is the forecast any good?"
        body="What the archive can say about itself, with no trade ever placed. If these are weak, nothing below them can be strong — every band probability is built on this."
        reads={["derived_forecast_skill", "weather_forecasts", "weather_observations", "derived_city_day_features"]}
      />

      {/* ===================================== what the desk has learned ==
          Seven views the archive can answer on its own, none of which need a
          single trade to have been placed. They come FIRST because three of
          the panels below need settled paper trades, and on a desk with no
          enabled strategy those are empty - which made the whole page look
          broken rather than early. */}
      <ModelAnalytics />

      {/* ============================================ forecast skill ===== */}
      <section>
        <div className="flex flex-wrap items-baseline gap-2">
          <h2 className="text-sm font-semibold">Forecast skill by city (1-day lead)</h2>
          <Freshness relation="derived_forecast_skill" />
        </div>
        <p className="mb-2 max-w-3xl text-[11px] leading-relaxed text-muted">
          Mean absolute error against what actually happened — the number sigma is built from, so a
          city high on this chart is one where every band probability is necessarily vague. This is
          the one-day view; <a className="text-accent hover:underline" href="/predictive">Predictive</a>{" "}
          has the same measure per lead day, with bias and hit rate beside it, which is where to go
          when a city looks wrong here.
        </p>
        <DataState
          relation="derived_forecast_skill"
          loading={skillQ.loading} error={skillQ.error} isEmpty={skill.length === 0}
          emptyTitle="No forecast skill measured yet"
          emptyBody={<>No independently verified station outcomes have produced a skill estimate yet. Collect final outcome evidence, then run GitHub Actions → <b>Skill</b> (<code>scripts/measure_skill.py</code>). Legacy rows remain preserved in the Data Bank but cannot price trades.</>}
          onRetry={skillQ.refresh}
        >
          <div className="rounded border border-border bg-panel p-3">
            <Histogram
              height={190}
              bins={skill.map((s) => ({
                label: s.city_key,
                value: s.mae_c,
                hint: `${s.city_key}: MAE ${s.mae_c?.toFixed(2)}°C, bias ${s.bias_c > 0 ? "+" : ""}${s.bias_c?.toFixed(2)}°C, over ${s.n_days} days${s.n_days < 200 ? " (thin sample)" : ""}`,
              }))}
              colorFor={(b) => {
                const row = skill.find((s) => s.city_key === b.label);
                if (row && row.n_days < 200) return "var(--c-warn)";
                return b.value > 2 ? "var(--c-bad)" : b.value > 1.2 ? "var(--c-warn)" : "var(--c-good)";
              }}
            />
            <div className="mt-2 flex flex-wrap gap-4 text-[10px] text-muted">
              <span><span className="mr-1 inline-block h-2 w-2 rounded-sm" style={{ background: "var(--c-good)" }} />under 1.2°C — the model knows this city</span>
              <span><span className="mr-1 inline-block h-2 w-2 rounded-sm" style={{ background: "var(--c-warn)" }} />1.2–2°C, or fewer than 200 days measured</span>
              <span><span className="mr-1 inline-block h-2 w-2 rounded-sm" style={{ background: "var(--c-bad)" }} />over 2°C — roughly a band and a half of error</span>
            </div>
          </div>
        </DataState>
      </section>

      <GroupHeading
        n={2}
        title="Is the pricing any good?"
        body="The forecast turned into a probability, and the probability against what the market charges. Still no trades required — this is the model and the book disagreeing on paper."
        reads={["band_probabilities", "edges", "book_snapshots"]}
      />

      {/* ============================================ model vs market ==== */}
      <section>
        <div className="flex flex-wrap items-baseline gap-2">
          <h2 className="text-sm font-semibold">Model against market</h2>
          <Freshness relation="v_opportunities" />
        </div>
        <p className="mb-2 max-w-3xl text-[11px] leading-relaxed text-muted">
          Every tradeable YES band: what it costs against what the model thinks it is worth. The
          diagonal is agreement. <b>Above the line the model is more optimistic than the book</b> —
          that is a buy, and the vertical distance is the raw edge before costs. Below it, the market
          is paying more than the model thinks the band is worth. Colour is the city&apos;s hotness
          against its own normal, so a cluster of buys on an unusually hot day is visible as one.
        </p>
        <DataState
          relation="v_opportunities"
          truncated={oppQ.truncated}
          loading={oppQ.loading} error={oppQ.error} isEmpty={yes.length === 0}
          emptyTitle="No priced bands yet"
          emptyBody={<>Needs both a book snapshot and a model probability — GitHub Actions → <b>Probabilities</b>, after P0.3 has written books.</>}
          onRetry={oppQ.refresh}
        >
          <div className="rounded border border-border bg-panel p-3">
            <ModelVsMarket rows={yes} stats={statsQ.rows} />
          </div>
        </DataState>
      </section>

      {/* ============================================ cost of the book === */}
      <section>
        <div className="flex flex-wrap items-baseline gap-2">
          <h2 className="text-sm font-semibold">What the book costs you, per city</h2>
          <Freshness relation="book_snapshots" />
        </div>
        <p className="mb-2 max-w-3xl text-[11px] leading-relaxed text-muted">
          Exactly one bucket pays, so the YES prices across a city&apos;s ladder should sum to about
          $1. What they actually sum to is the <b>overround</b> — the house&apos;s margin, and the
          first thing subtracted from every edge on that board. Under $1.00 is not a rounding
          artefact: it is a combination arb, which is s2&apos;s entire thesis and needs no forecast
          at all. Beside it, the median spread you cross to get in, and how much of the ladder the
          edge engine will actually let you trade.
        </p>
        <DataState
          relation="v_opportunities"
          truncated={oppQ.truncated}
          loading={oppQ.loading} error={oppQ.error} isEmpty={bookCost.length === 0}
          emptyTitle="No priced ladders yet"
          emptyBody={<>Needs a book snapshot — n8n <b>P0.3</b> — and a model probability from GitHub Actions → <b>Probabilities</b>.</>}
          onRetry={oppQ.refresh}
        >
          <div className="overflow-x-auto rounded border border-border">
            <table className="w-full text-xs">
              <thead className="bg-panel2 text-muted">
                <tr>
                  <th className="px-2 py-1.5 text-left">City · day</th>
                  <th className="px-2 py-1.5 text-right">Bands</th>
                  <th className="px-2 py-1.5 text-right" title="Sum of the YES prices across the whole ladder. Exactly one bucket pays, so $1.00 is a fair book.">
                    Book sum
                  </th>
                  <th className="px-2 py-1.5 text-left">Overround</th>
                  <th className="px-2 py-1.5 text-right" title="Median bid/ask gap across the ladder. You cross half of it going in and half coming out.">
                    Median spread
                  </th>
                  <th className="px-2 py-1.5 text-right" title="Bands the edge engine will let you trade, out of the bands that are priced.">
                    Tradeable
                  </th>
                  <th className="px-2 py-1.5 text-left">Verdict</th>
                </tr>
              </thead>
              <tbody>
                {bookCost.map((r) => (
                  <tr key={r.key} className="border-t border-border">
                    <td className="px-2 py-1.5">
                      {r.city}
                      <span className="ml-1 text-[10px] text-muted">{fmtResolutionDate(r.day)}</span>
                    </td>
                    <td className="px-2 py-1.5 text-right font-mono tabular-nums text-muted">{r.n}</td>
                    <td className={`px-2 py-1.5 text-right font-mono tabular-nums ${
                      r.sum < 1 ? "text-good" : r.sum > 1.1 ? "text-bad" : ""
                    }`}>
                      ${r.sum.toFixed(3)}
                    </td>
                    <td className="px-2 py-1.5">
                      {/* A bar centred on a fair book, so cheap and expensive
                          lean in opposite directions instead of both being a
                          number you have to read. */}
                      <div className="relative h-2 w-28 rounded bg-panel2">
                        <div className="absolute inset-y-0 left-1/2 w-px bg-border" />
                        <div
                          className={`absolute inset-y-0 ${r.sum >= 1 ? "left-1/2 bg-bad/70" : "bg-good/70"}`}
                          style={
                            r.sum >= 1
                              ? { width: `${Math.min(50, (r.sum - 1) * 250)}%` }
                              : { right: "50%", width: `${Math.min(50, (1 - r.sum) * 250)}%` }
                          }
                        />
                      </div>
                      <span className={`text-[10px] ${r.sum < 1 ? "text-good" : r.sum > 1.1 ? "text-bad" : "text-muted"}`}>
                        {r.sum >= 1 ? "+" : ""}{((r.sum - 1) * 100).toFixed(1)}%
                      </span>
                    </td>
                    <td className={`px-2 py-1.5 text-right font-mono tabular-nums ${r.spread > 0.08 ? "text-warn" : "text-muted"}`}>
                      {(r.spread * 100).toFixed(1)}¢
                    </td>
                    <td className={`px-2 py-1.5 text-right font-mono tabular-nums ${
                      r.tradeable === 0 ? "text-bad" : r.tradeable < r.n / 2 ? "text-warn" : "text-muted"
                    }`}>
                      {r.tradeable}/{r.n}
                    </td>
                    <td className={`px-2 py-1.5 text-[11px] ${r.sum < 1 ? "text-good" : r.spread > 0.08 || r.sum > 1.1 ? "text-warn" : "text-muted"}`}>
                      {r.verdict}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </DataState>
      </section>

      {/* =============================================== monte carlo ===== */}
      <section>
        <div className="flex flex-wrap items-baseline gap-2">
          <h2 className="text-sm font-semibold">Monte Carlo — what a position actually does</h2>
          <Freshness relation="band_probabilities" />
        </div>
        <p className="mb-2 max-w-3xl text-[11px] leading-relaxed text-muted">
          The engine already computes each band&apos;s probability analytically, so simulating the
          same normal would only reproduce it. What is <b>not</b> obvious is the shape of a
          position&apos;s outcomes: several legs, a fee that varies with price, and an all-or-nothing
          payoff per band. A basket with positive expected value that loses four times out of five is
          a different proposition from one that loses one time in five, and the average cannot tell
          them apart. {RUNS.toLocaleString()} draws of the day&apos;s maximum from the model&apos;s
          own centre and sigma — the same distribution the desk is betting on.
        </p>
        <MonteCarloPanel opps={opps} />
      </section>

      <GroupHeading
        n={3}
        title="Did it make money?"
        body="The first group where a trade has to have happened. On a desk with every strategy switched off these are empty, and that is early rather than broken - the Strategies page is where that changes."
        reads={["paper_trades", "signals", "ledger"]}
      />

      {/* ================================================= P&L curve ===== */}
      <section>
        <div className="flex flex-wrap items-baseline gap-2">
          <h2 className="text-sm font-semibold">Realised P&amp;L</h2>
          <Freshness relation="paper_trades" />
        </div>
        <p className="mb-2 max-w-3xl text-[11px] leading-relaxed text-muted">
          Cumulative net profit from closed paper trades, in settlement order. Net means after fees
          and spread — the desk quotes nothing gross.
        </p>
        <PnlCurve
          trades={tradesQ.data ?? []}
          enabledCount={(boardQ.data ?? []).filter((b) => b.enabled && b.strategy_id !== "system").length}
          loading={tradesQ.loading}
          error={tradesQ.error}
          onRetry={tradesQ.refresh}
        />
      </section>

      {/* ======================================= strategy attribution ==== */}
      <section>
        <div className="flex flex-wrap items-baseline gap-2">
          <h2 className="text-sm font-semibold">Strategy attribution</h2>
          <Freshness relation="paper_trades" />
        </div>
        <StrategyAttribution
          trades={tradesQ.data ?? []}
          signals={signalsQ.data ?? []}
          board={boardQ.data ?? []}
          loading={tradesQ.loading}
          error={tradesQ.error}
          onRetry={() => { tradesQ.refresh(); boardQ.refresh(); }}
        />
        <InlineError message={signalsQ.error} />
      </section>

      {/* ============================================== the data bank ==== */}
      <section>
        <div className="flex flex-wrap items-baseline gap-2">
          <h2 className="text-sm font-semibold">The desk&apos;s own record</h2>
          <Freshness relation="fact_band_outcome" />
        </div>
        <p className="mb-2 max-w-3xl text-[11px] leading-relaxed text-muted">
          Every other chart on this page reads live tables that are overwritten by their own next
          run. The frozen facts — what was predicted, what the market charged, what actually
          happened — now have their own page, along with the archive underneath them and every
          derived layer built out of it.
        </p>
        <Link
          href="/databank"
          className="inline-block rounded border border-accent/50 bg-accent/10 px-2.5 py-1 text-xs text-accent hover:bg-accent/20"
        >
          Open the Data Bank →
        </Link>
      </section>

      <GroupHeading
        n={4}
        title="Can it take size?"
        body="An edge you cannot fill is not an edge. Needs book snapshots from P0.3 and traded volume from P0.4."
        reads={["trades_observed", "book_snapshots", "derived_capacity"]}
      />

      {/* ================================================= liquidity ===== */}
      <section>
        <div className="flex flex-wrap items-baseline gap-2">
          <h2 className="text-sm font-semibold">Liquidity: quoted against traded</h2>
          <Freshness relation="trades_observed" />
        </div>
        <p className="mb-2 max-w-3xl text-[11px] leading-relaxed text-muted">
          Two different facts, plotted rather than merged. <b>Depth</b> is what the current quotes can
          absorb inside 5¢; <b>volume</b> is what actually changed hands. Up and to the right is a
          real market. Top-left is quoted but not traded — a fat quote nobody hits, and the
          opportunity ranking already discounts it. Bottom-right trades in bursts against a thin
          book, which costs money on the way out rather than the way in.
        </p>
        <DataState
          loading={statsQ.loading} error={statsQ.error}
          isEmpty={(statsQ.rows).length === 0}
          emptyTitle="No liquidity data yet"
          emptyBody={<><code>derived_capacity</code> comes from <b>Derived Recompute</b>; volume comes from <code>trades_observed</code>, which P0.4 fills.</>}
          onRetry={statsQ.refresh}
        >
          <div className="rounded border border-border bg-panel p-3">
            <Scatter
              height={280} logX
              xLabel="24h traded volume (log)"
              yLabel="Book depth inside 5¢ (USD)"
              xTickFormat={(v) => fmtCompactUsd(v)}
              yTickFormat={(v) => fmtCompactUsd(v)}
              points={(statsQ.rows)
                .filter((c) => (c.volume_24h ?? 0) > 0 || (c.depth_5c ?? 0) > 0)
                .map((c) => ({
                  x: Math.max(c.volume_24h ?? 0, 1),
                  y: c.depth_5c ?? 0,
                  label: c.city_key,
                  color: heatColor(c.hotness_sigma),
                  hint: `${c.display_name ?? c.city_key}: ${fmtCompactUsd(c.volume_24h)} traded over ${c.n_trades_24h ?? 0} trades, ${fmtUsd(c.depth_5c)} quoted inside 5c`,
                }))}
            />
          </div>
        </DataState>
      </section>
    </div>
  );
}

/* ------------------------------------------------------ model vs market -- */

/**
 * A group heading, so the page has a spine.
 *
 * Fourteen panels in a flat list is why this read as "all over the place":
 * nothing said which question each answered, or which of them are empty on a
 * desk that has simply not traded yet. The numbering is not decoration - the
 * groups are a dependency order. If group 1 is weak, nothing in group 2 can
 * be strong, and money made in group 3 on a weak group 1 was luck.
 */
function GroupHeading({
  n, title, body, reads = [],
}: {
  n: number;
  title: string;
  body: string;
  /**
   * The tables this whole group stands on. Shown as chips so a weak group can
   * be told apart from an unfilled one at a glance - which is the difference
   * between "the model is bad" and "the job that feeds it stopped".
   */
  reads?: string[];
}) {
  return (
    <div className="border-t border-border pt-5">
      <div className="flex items-baseline gap-2">
        <span className="text-[10px] font-semibold tabular-nums text-accent">{n}</span>
        <h2 className="text-base font-semibold">{title}</h2>
      </div>
      <p className="mt-0.5 max-w-3xl text-xs leading-relaxed text-muted">{body}</p>
      {reads.length > 0 && (
        <div className="mt-1.5">
          <FreshnessRow relations={reads} />
        </div>
      )}
    </div>
  );
}

function ModelVsMarket({ rows, stats }: { rows: Opportunity[]; stats: CityStats[] }) {
  const heatByCity = new Map(stats.map((s) => [s.city_key, s.hotness_sigma]));
  const W = 720, H = 320, PAD = 44;
  const X = (p: number) => PAD + p * (W - PAD - 14);
  const Y = (p: number) => H - PAD - p * (H - PAD - 14);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full" style={{ maxHeight: 460 }}>
      {[0, 0.25, 0.5, 0.75, 1].map((v) => (
        <g key={v}>
          <line x1={PAD} x2={W - 14} y1={Y(v)} y2={Y(v)} stroke="var(--chart-grid)" />
          <line x1={X(v)} x2={X(v)} y1={14} y2={H - PAD} stroke="var(--chart-grid)" />
          <text x={PAD - 6} y={Y(v) + 3} textAnchor="end" fontSize="9" fill="var(--chart-axis)">{(v * 100).toFixed(0)}%</text>
          <text x={X(v)} y={H - PAD + 13} textAnchor="middle" fontSize="9" fill="var(--chart-axis)">{(v * 100).toFixed(0)}¢</text>
        </g>
      ))}
      <line x1={X(0)} y1={Y(0)} x2={X(1)} y2={Y(1)} stroke="var(--chart-axis)" strokeDasharray="4 3" strokeWidth={1} />
      <text x={X(0.72)} y={Y(0.78)} fontSize="9" fill="var(--chart-axis)">model = market</text>
      <text x={W / 2} y={H - 6} textAnchor="middle" fontSize="9" fill="var(--chart-axis)">market price</text>
      <text x={10} y={20} fontSize="9" fill="var(--chart-axis)">model probability</text>

      {rows.map((o, i) => {
        const buyish = (o.model_prob as number) > (o.market_price as number);
        return (
          <g key={`${o.band_id}-${o.side}`} className="chart-pop" style={{ animationDelay: `${Math.min(i, 60) * 8}ms` }}>
            <circle
              cx={X(o.market_price as number)}
              cy={Y(o.model_prob as number)}
              r={3 + Math.min(4, Math.log10(1 + (o.volume_usd ?? 0)))}
              fill={heatColor(heatByCity.get(o.city_key))}
              opacity={buyish ? 0.85 : 0.35}
              stroke={buyish ? "var(--c-good)" : "none"}
              strokeWidth={0.7}
            />
            <title>
              {`${o.display_name ?? o.city_key} ${o.band_label ?? ""} — market ${fmtPrice(o.market_price)}, model ${fmtPct(o.model_prob)}, ` +
               `${fmtCompactUsd(o.volume_usd)} traded, settles ${o.resolution_date}`}
            </title>
          </g>
        );
      })}
    </svg>
  );
}

/* ---------------------------------------------------------- monte carlo -- */

function MonteCarloPanel({ opps }: { opps: Opportunity[] }) {
  const [key, setKey] = useState<string | null>(null);
  const [stakes, setStakes] = useState<Record<string, string>>({});

  const cityDays = useMemo(() => {
    const m = new Map<string, { city_key: string; date: string; display: string; n: number }>();
    for (const o of opps) {
      const k = `${o.city_key}|${o.resolution_date}`;
      const e = m.get(k) ?? { city_key: o.city_key, date: o.resolution_date, display: o.display_name ?? o.city_key, n: 0 };
      e.n++;
      m.set(k, e);
    }
    return Array.from(m.entries()).sort((a, b) => a[1].date.localeCompare(b[1].date));
  }, [opps]);

  useEffect(() => {
    if (!key && cityDays.length) setKey(cityDays[0][0]);
  }, [cityDays, key]);

  const chosen = key ? key.split("|") : null;
  const bandsForDay = useMemo(
    () =>
      chosen
        ? opps.filter((o) => o.city_key === chosen[0] && o.resolution_date === chosen[1] && o.side === "YES")
        : [],
    [opps, chosen]
  );
  const bandIds = bandsForDay.map((b) => b.band_id);

  const probQ = useQuery<ProbRow[]>(
    () =>
      supabase
        .from("band_probabilities")
        .select("band_id,forecast_max_c,sigma_c,bias_applied_c,computed_at")
        .in("band_id", bandIds.length ? bandIds : ["00000000-0000-0000-0000-000000000000"])
        .order("computed_at", { ascending: false })
        .limit(500),
    [bandIds.join(",")]
  );

  const model = useMemo(() => {
    const first = (probQ.data ?? [])[0];
    if (!first || first.forecast_max_c === null || first.sigma_c === null) return null;
    return {
      centre: first.forecast_max_c - (first.bias_applied_c ?? 0),
      sigma: first.sigma_c,
      at: first.computed_at,
    };
  }, [probQ.data]);

  const mcBands: McBand[] = useMemo(
    () =>
      [...bandsForDay]
        .sort((a, b) => (a.band_lo ?? -1e9) - (b.band_lo ?? -1e9))
        .map((b) => ({
          band_id: b.band_id,
          label: b.band_label ?? `${b.band_lo ?? ""}–${b.band_hi ?? ""}`,
          lo: b.open_low ? null : b.band_lo,
          hi: b.open_high ? null : b.band_hi,
          price: b.market_price,
          stake: parseFloat(stakes[b.band_id] ?? "") || 0,
        })),
    [bandsForDay, stakes]
  );

  const result = useMemo(
    () => (model && mcBands.length ? simulate(model.centre, model.sigma, mcBands, RUNS) : null),
    [model, mcBands]
  );

  const unit = (bandsForDay[0]?.unit ?? "C") as Unit;
  const anyStake = mcBands.some((b) => b.stake > 0);

  if (!cityDays.length) {
    return <Empty height={160}>No priced city-days yet — this needs <code>v_opportunities</code> to have rows.</Empty>;
  }

  return (
    <div className="space-y-3 rounded border border-border bg-panel p-3">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <select
          value={key ?? ""}
          onChange={(e) => { setKey(e.target.value); setStakes({}); }}
          className="rounded border border-border bg-panel2 px-2 py-1"
        >
          {cityDays.map(([k, v]) => (
            <option key={k} value={k}>
              {v.display} — {fmtResolutionDate(v.date)} ({fmtDaysAhead(v.date)}), {v.n / 2 | 0} bands
            </option>
          ))}
        </select>
        {model && (
          <span className="font-mono text-[11px] text-muted">
            centre {fmtTemp(model.centre, unit)} · σ {model.sigma.toFixed(2)}°C · priced {fmtDateTime(model.at)}
          </span>
        )}
        {anyStake && (
          <button onClick={() => setStakes({})} className="ml-auto text-[11px] text-muted underline hover:text-text">
            clear stakes
          </button>
        )}
      </div>

      {!model ? (
        <Empty height={140}>
          No <code>band_probabilities</code> for this city-day, so there is no centre or sigma to draw
          from. Run GitHub Actions → <b>Probabilities</b>.
        </Empty>
      ) : (
        <>
          {/* ---- where the day lands, simulated against what it costs ---- */}
          <div>
            <div className="mb-1 flex items-baseline justify-between">
              <h3 className="text-xs font-semibold">Where the day lands</h3>
              <span className="text-[10px] text-muted">
                bars = simulated probability · ticks = the market&apos;s price
                {result && result.offLadder > 0.005 && (
                  <span className="ml-2 text-warn">{fmtPct(result.offLadder, 1)} landed off the ladder</span>
                )}
              </span>
            </div>
            <div className="relative">
              <Histogram
                height={170}
                bins={(result?.landed ?? []).map((l) => ({
                  label: l.label,
                  value: l.share,
                  hint:
                    `${l.label}: simulated ${fmtPct(l.share, 1)}` +
                    (l.price !== null ? `, market ${fmtPrice(l.price)}` : "") +
                    (l.price !== null ? ` → edge ${((l.share - l.price) * 100).toFixed(1)}pp` : ""),
                }))}
                colorFor={(b) => {
                  const l = result?.landed.find((x) => x.label === b.label);
                  if (!l || l.price === null) return "var(--c-muted)";
                  return l.share > l.price ? "var(--c-good)" : "var(--c-accent)";
                }}
              />
            </div>
            <p className="mt-1 text-[10px] leading-relaxed text-muted">
              Green means the simulation lands there more often than the market charges — the same
              edge the Opportunities page ranks, seen as a distribution rather than a row.
            </p>
          </div>

          {/* ---- stake the legs ------------------------------------------ */}
          <div className="overflow-x-auto rounded border border-border">
            <table className="w-full text-xs">
              <thead className="bg-panel2 text-[10px] uppercase tracking-wide text-muted">
                <tr>
                  <th className="p-1.5 text-left">Band</th>
                  <th className="p-1.5 text-right">Market</th>
                  <th className="p-1.5 text-right">Simulated</th>
                  <th className="p-1.5 text-right">Edge</th>
                  <th className="p-1.5 text-right">Stake $</th>
                </tr>
              </thead>
              <tbody>
                {mcBands.map((b) => {
                  const l = result?.landed.find((x) => x.band_id === b.band_id);
                  const edge = l && b.price !== null ? l.share - b.price : null;
                  return (
                    <tr key={b.band_id} className="border-t border-border">
                      <td className="p-1.5 font-mono">{b.label}</td>
                      <td className="p-1.5 text-right font-mono">{fmtPrice(b.price)}</td>
                      <td className="p-1.5 text-right font-mono">{l ? fmtPct(l.share, 1) : "—"}</td>
                      <td className={`p-1.5 text-right font-mono ${(edge ?? 0) > 0 ? "text-good" : "text-muted"}`}>
                        {edge === null ? "—" : `${(edge * 100).toFixed(1)}pp`}
                      </td>
                      <td className="p-1.5 text-right">
                        <input
                          inputMode="decimal"
                          value={stakes[b.band_id] ?? ""}
                          onChange={(e) => setStakes((s) => ({ ...s, [b.band_id]: e.target.value }))}
                          placeholder="—"
                          disabled={b.price === null}
                          className="w-20 rounded border border-border bg-panel2 px-1.5 py-0.5 text-right font-mono text-xs disabled:opacity-40"
                        />
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          {/* ---- the outcome distribution -------------------------------- */}
          {anyStake && result ? (
            <div>
              <div className="mb-1 flex items-baseline justify-between">
                <h3 className="text-xs font-semibold">Outcome distribution</h3>
                <span className="font-mono text-[10px] text-muted">
                  {RUNS.toLocaleString()} runs · {fmtUsd(result.staked)} staked
                </span>
              </div>
              <Histogram
                height={160}
                bins={histogram(result.pnl, 30).map((b) => ({
                  label: b.label,
                  value: b.value,
                  hint: `${fmtUsd(b.lo)} to ${fmtUsd(b.hi)}: ${((b.value / result.pnl.length) * 100).toFixed(1)}% of runs`,
                }))}
                colorFor={(b) => (parseFloat(b.label) >= 0 ? "var(--c-good)" : "var(--c-bad)")}
                xTickFormat={(l) => (Math.abs(parseFloat(l)) >= 1000 ? `${(parseFloat(l) / 1000).toFixed(1)}k` : l)}
              />
              <div className="mt-2 grid grid-cols-3 gap-2 sm:grid-cols-6">
                <Mc label="Mean" v={fmtUsd(result.mean, { signed: true })} tone={result.mean > 0 ? "good" : "bad"}
                    hint="Expected profit per run. Positive is necessary and nowhere near sufficient." />
                <Mc label="Median" v={fmtUsd(result.median, { signed: true })} tone={result.median > 0 ? "good" : "bad"}
                    hint="The middle outcome. A positive mean with a negative median is a lottery ticket." />
                <Mc label="Win rate" v={fmtPct(result.probProfit, 0)}
                    hint="Share of runs that end above zero." />
                <Mc label="5th pct" v={fmtUsd(result.p5, { signed: true })} tone="bad"
                    hint="One run in twenty is worse than this. This is the number that decides position size." />
                <Mc label="95th pct" v={fmtUsd(result.p95, { signed: true })} tone="good" />
                <Mc label="Worst" v={fmtUsd(result.worst, { signed: true })} tone="bad"
                    hint="Every leg loses. On a single-leg position this is simply the stake." />
              </div>
              {result.mean > 0 && result.median < 0 && (
                <p className="mt-2 rounded border border-warn/40 bg-warn/10 px-2 py-1.5 text-[11px] leading-relaxed text-warn">
                  Positive mean, negative median: this position makes money on average and loses money
                  most of the time. That is a real strategy, but it needs a bankroll that survives the
                  common case — size it on the 5th percentile, not on the mean.
                </p>
              )}
            </div>
          ) : (
            <Empty height={110}>
              Type a stake against a band above to simulate what the position does across{" "}
              {RUNS.toLocaleString()} versions of the day.
            </Empty>
          )}
        </>
      )}
      <InlineError message={probQ.error} />
    </div>
  );
}

function Mc({ label, v, tone, hint }: { label: string; v: string; tone?: "good" | "bad"; hint?: string }) {
  return (
    <div className="rounded bg-panel2 px-2 py-1.5" title={hint}>
      <div className="text-[9px] uppercase tracking-wide text-muted">{label}</div>
      <div className={`font-mono text-sm ${tone === "good" ? "text-good" : tone === "bad" ? "text-bad" : ""}`}>{v}</div>
    </div>
  );
}

/* ------------------------------------------------------------ P&L curve -- */

function PnlCurve({ trades, enabledCount, loading, error, onRetry }: { trades: TradeRow[]; enabledCount: number; loading: boolean; error: string | null; onRetry: () => void }) {
  const points = useMemo(() => {
    let cum = 0;
    return trades
      .filter((t) => t.closed_at)
      .map((t) => {
        cum += t.net_pnl ?? 0;
        return { x: new Date(t.closed_at as string).getTime(), y: cum };
      });
  }, [trades]);

  return (
    <DataState
      loading={loading} error={error} isEmpty={points.length === 0}
      emptyTitle="No settled trades yet"
      emptyBody={
        <>
          Nothing settles until a strategy is enabled, fires a signal, and its market resolves.{" "}
          {enabledCount === 0 ? (
            <>
              Every strategy is currently switched off —{" "}
              <a className="text-accent hover:underline" href="/strategies">turn one on</a>.
            </>
          ) : (
            <>
              {enabledCount} strateg{enabledCount === 1 ? "y is" : "ies are"} on; GitHub Actions →{" "}
              <b>Settlement</b> writes the close.
            </>
          )}
        </>
      }
      onRetry={onRetry}
    >
      <div className="rounded border border-border bg-panel p-3">
        <LineChart
          height={220}
          zeroLine
          yLabel="cumulative net P&L (USD)"
          yTickFormat={(v) => fmtUsd(v)}
          xTickFormat={(v) => fmtDateTime(new Date(v).toISOString())}
          series={[{
            label: "net",
            color: points.length && points[points.length - 1].y >= 0 ? "var(--c-good)" : "var(--c-bad)",
            points,
            fill: true,
          }]}
        />
        <div className="mt-1 text-[10px] text-muted">
          {points.length} closed trade{points.length === 1 ? "" : "s"} · final{" "}
          <span className={pnlColor(points[points.length - 1]?.y)}>
            {fmtUsd(points[points.length - 1]?.y, { signed: true })}
          </span>
        </div>
      </div>
    </DataState>
  );
}

/* -------------------------------------------------- strategy attribution -- */

function StrategyAttribution({ trades, signals, board, loading, error, onRetry }: {
  trades: TradeRow[]; signals: Array<{ strategy_id: string }>; board: StrategyBoardRow[];
  loading: boolean; error: string | null; onRetry: () => void;
}) {
  const byStrategy = useMemo(() => {
    const g = new Map<string, { n: number; net: number; wins: number }>();
    for (const t of trades) {
      const e = g.get(t.strategy_id) ?? { n: 0, net: 0, wins: 0 };
      e.n++; e.net += t.net_pnl ?? 0;
      if ((t.net_pnl ?? 0) > 0) e.wins++;
      g.set(t.strategy_id, e);
    }
    return Array.from(g.entries()).map(([id, v]) => ({ id, ...v, winRate: v.n ? v.wins / v.n : null }));
  }, [trades]);

  const freq = useMemo(() => {
    const f: Record<string, number> = {};
    for (const s of signals) f[s.strategy_id] = (f[s.strategy_id] ?? 0) + 1;
    return f;
  }, [signals]);

  // NO TRADES IS NOT AN EMPTY BOX. It has a cause, and the cause decides what
  // to do about it: every strategy off is one click on the Strategies page,
  // firing-but-not-filling is a liquidity or approval problem, and neither
  // looks like the other. Showing the roster instead of a placeholder is the
  // difference between "broken" and "early".
  if (!loading && !error && byStrategy.length === 0 && board.length > 0) {
    const trading = board.filter((b) => b.strategy_id !== "system");
    const on = trading.filter((b) => b.enabled);
    const fired = trading.filter((b) => b.fired_30d > 0);
    const waiting = trading.reduce((a, b) => a + b.waiting, 0);
    return (
      <div className="rounded border border-border bg-panel p-3">
        <p className="text-xs leading-relaxed text-muted">
          {on.length === 0 ? (
            <>
              <b className="text-warn">Nothing has traded because nothing is switched on.</b> All{" "}
              {trading.length} strategies ship disabled — a safety default, not a fault.{" "}
              <a className="text-accent hover:underline" href="/strategies">
                Turn one on
              </a>{" "}
              and this fills from the next engine run.
            </>
          ) : fired.length === 0 ? (
            <>
              <b className="text-warn">
                {on.length} strateg{on.length === 1 ? "y is" : "ies are"} on, and none has fired in 30
                days.
              </b>{" "}
              Their entry conditions have not been met. Opportunities shows which bands would pass
              each one right now.
            </>
          ) : (
            <>
              <b className="text-warn">Signals are firing but nothing has settled.</b>{" "}
              {waiting > 0 && <>{waiting} are waiting for approval. </>}
              The gap is fills and settlement, not signal generation.
            </>
          )}
        </p>
        <div className="mt-2 overflow-x-auto">
          <table className="w-full text-xs">
            <thead className="text-muted">
              <tr>
                <th className="p-1 text-left">Strategy</th>
                <th className="p-1 text-right">On</th>
                <th className="p-1 text-right">Fired 30d</th>
                <th className="p-1 text-right">Waiting</th>
                <th className="p-1 text-right">Filled</th>
                <th className="p-1 text-left">Verdict</th>
              </tr>
            </thead>
            <tbody>
              {trading.map((b) => (
                <tr key={b.strategy_id} className="border-t border-border">
                  <td className="p-1">{b.name ?? b.strategy_id}</td>
                  <td className={`p-1 text-right ${b.enabled ? "text-good" : "text-muted"}`}>
                    {b.enabled ? "yes" : "no"}
                  </td>
                  <td className="p-1 text-right font-mono tabular-nums">{b.fired_30d}</td>
                  <td className={`p-1 text-right font-mono tabular-nums ${b.waiting ? "text-warn" : "text-muted"}`}>
                    {b.waiting}
                  </td>
                  <td className="p-1 text-right font-mono tabular-nums">{b.filled_all_time}</td>
                  <td className="p-1 text-[11px] text-muted">{b.verdict}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    );
  }

  return (
    <DataState
      loading={loading} error={error} isEmpty={byStrategy.length === 0}
      emptyTitle="No settled trades yet"
      emptyBody={
        <>
          Attribution needs closed paper trades.
          {Object.keys(freq).length > 0 && (
            <> Signals <b>are</b> firing ({fmtInt(Object.values(freq).reduce((a, b) => a + b, 0))} in the last 7 days), so the gap is settlement, not signal generation.</>
          )}
        </>
      }
      onRetry={onRetry}
    >
      <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
        {byStrategy.map((s) => {
          const b = board.find((r) => r.strategy_id === s.id);
          return (
            <div key={s.id} className="rounded border border-border bg-panel p-3 text-sm">
              <div className="flex items-baseline gap-2">
                <span className="font-semibold">{b?.name ?? s.id}</span>
                {b && !b.enabled && (
                  <span className="text-[10px] text-warn" title="This strategy has a record but is switched off now, so the curve above stops here.">
                    now off
                  </span>
                )}
              </div>
              <div className={`font-mono text-lg ${pnlColor(s.net)}`}>{fmtUsd(s.net, { signed: true })}</div>
              <div className="text-xs text-muted">
                {s.n} trades · win rate {fmtPct(s.winRate)} · {freq[s.id] ?? 0} signals in 7d
              </div>
              {s.n < MIN_TRADES_FOR_RATE && (
                <div className="mt-1 text-[10px] text-warn">
                  {s.n} trades is too few for a win rate to mean anything — {MIN_TRADES_FOR_RATE} is the
                  threshold the backtest metrics use.
                </div>
              )}
            </div>
          );
        })}
      </div>
    </DataState>
  );
}
