"use client";

import { useEffect, useMemo, useState } from "react";
import { supabase } from "@/lib/supabase";
import { useQuery } from "@/lib/useQuery";
import { DataState, InlineError } from "@/components/DataState";
import { Empty, Histogram, LineChart, Scatter } from "@/components/charts";
import { fmtCompactUsd, fmtInt, fmtPct, fmtPrice, fmtUsd, pnlColor } from "@/lib/format";
import { fmtTemp, type Unit } from "@/lib/units";
import { fmtDateTime, fmtDaysAhead, fmtResolutionDate } from "@/lib/time";
import { heatColor } from "@/lib/heat";
import { histogram, simulate, type McBand } from "@/lib/montecarlo";
import type { CityStats, Opportunity } from "@/lib/types";

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
        .eq("lead_days", 1).order("computed_at", { ascending: false }).limit(500),
    []
  );
  const oppQ = useQuery<Opportunity[]>(
    () => supabase.from("v_opportunities").select("*").limit(4000), [], 60000
  );
  const statsQ = useQuery<CityStats[]>(() => supabase.from("v_city_stats").select("*"), [], 60000);
  const tradesQ = useQuery<TradeRow[]>(
    () => supabase.from("paper_trades").select("strategy_id,net_pnl,gross_pnl,closed_at").not("closed_at", "is", null).order("closed_at", { ascending: true }),
    []
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
  const yes = useMemo(
    () => opps.filter((o) => o.side === "YES" && o.market_price !== null && o.model_prob !== null),
    [opps]
  );

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-lg font-semibold">Analytics</h1>
        <p className="mt-1 max-w-3xl text-xs leading-relaxed text-muted">
          Where the model and the market disagree, how well the model has actually done, and what a
          position&apos;s outcomes look like rather than just its average. Every chart draws from the
          same tables the trading pages do — nothing here is a separate calculation.
        </p>
      </div>

      {/* ============================================ model vs market ==== */}
      <section>
        <h2 className="text-sm font-semibold">Model against market</h2>
        <p className="mb-2 max-w-3xl text-[11px] leading-relaxed text-muted">
          Every tradeable YES band: what it costs against what the model thinks it is worth. The
          diagonal is agreement. <b>Above the line the model is more optimistic than the book</b> —
          that is a buy, and the vertical distance is the raw edge before costs. Below it, the market
          is paying more than the model thinks the band is worth. Colour is the city&apos;s hotness
          against its own normal, so a cluster of buys on an unusually hot day is visible as one.
        </p>
        <DataState
          loading={oppQ.loading} error={oppQ.error} isEmpty={yes.length === 0}
          emptyTitle="No priced bands yet"
          emptyBody={<>Needs both a book snapshot and a model probability — GitHub Actions → <b>Probabilities</b>, after P0.3 has written books.</>}
          onRetry={oppQ.refresh}
        >
          <div className="rounded border border-border bg-panel p-3">
            <ModelVsMarket rows={yes} stats={statsQ.data ?? []} />
          </div>
        </DataState>
      </section>

      {/* =============================================== monte carlo ===== */}
      <section>
        <h2 className="text-sm font-semibold">Monte Carlo — what a position actually does</h2>
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

      {/* ============================================ forecast skill ===== */}
      <section>
        <h2 className="text-sm font-semibold">Forecast skill by city (1-day lead)</h2>
        <p className="mb-2 max-w-3xl text-[11px] leading-relaxed text-muted">
          Mean absolute error of the forecast against what actually happened. This is the number
          sigma is built from, so a city high on this chart is one where every band probability is
          necessarily vague. A thin sample is marked: under 200 days, the figure itself is uncertain.
        </p>
        <DataState
          loading={skillQ.loading} error={skillQ.error} isEmpty={skill.length === 0}
          emptyTitle="No forecast skill measured yet"
          emptyBody={<>Run GitHub Actions → <b>Skill</b> (<code>scripts/measure_skill.py</code>). It needs both forecast and observation history before it can measure anything. See <code>docs/skill_baseline.md</code>.</>}
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

      {/* ================================================= P&L curve ===== */}
      <section>
        <h2 className="text-sm font-semibold">Realised P&amp;L</h2>
        <p className="mb-2 max-w-3xl text-[11px] leading-relaxed text-muted">
          Cumulative net profit from closed paper trades, in settlement order. Net means after fees
          and spread — the desk quotes nothing gross.
        </p>
        <PnlCurve trades={tradesQ.data ?? []} loading={tradesQ.loading} error={tradesQ.error} onRetry={tradesQ.refresh} />
      </section>

      {/* ======================================= strategy attribution ==== */}
      <section>
        <h2 className="text-sm font-semibold">Strategy attribution</h2>
        <StrategyAttribution trades={tradesQ.data ?? []} signals={signalsQ.data ?? []} loading={tradesQ.loading} error={tradesQ.error} onRetry={tradesQ.refresh} />
        <InlineError message={signalsQ.error} />
      </section>

      {/* ================================================= liquidity ===== */}
      <section>
        <h2 className="text-sm font-semibold">Liquidity: quoted against traded</h2>
        <p className="mb-2 max-w-3xl text-[11px] leading-relaxed text-muted">
          Two different facts, plotted rather than merged. <b>Depth</b> is what the current quotes can
          absorb inside 5¢; <b>volume</b> is what actually changed hands. Up and to the right is a
          real market. Top-left is quoted but not traded — a fat quote nobody hits, and the
          opportunity ranking already discounts it. Bottom-right trades in bursts against a thin
          book, which costs money on the way out rather than the way in.
        </p>
        <DataState
          loading={statsQ.loading} error={statsQ.error}
          isEmpty={(statsQ.data ?? []).length === 0}
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
              points={(statsQ.data ?? [])
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

function PnlCurve({ trades, loading, error, onRetry }: { trades: TradeRow[]; loading: boolean; error: string | null; onRetry: () => void }) {
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
      emptyBody={<>Nothing settles until a strategy is enabled, fires a signal, and its market resolves. All six strategies ship <code>enabled = false</code>; GitHub Actions → <b>Settlement</b> writes the close.</>}
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

function StrategyAttribution({ trades, signals, loading, error, onRetry }: {
  trades: TradeRow[]; signals: Array<{ strategy_id: string }>; loading: boolean; error: string | null; onRetry: () => void;
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
        {byStrategy.map((s) => (
          <div key={s.id} className="rounded border border-border bg-panel p-3 text-sm">
            <div className="font-semibold">{s.id}</div>
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
        ))}
      </div>
    </DataState>
  );
}
