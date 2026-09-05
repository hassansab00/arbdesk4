"use client";

import { useMemo, useState } from "react";
import { supabase } from "@/lib/supabase";
import { useQuery } from "@/lib/useQuery";
import { DataState } from "@/components/DataState";
import { Empty, LineChart, Scatter } from "@/components/charts";
import Convergence3D, { type ConvergencePoint } from "@/components/Convergence3D";
import { fmtInt, fmtPct, fmtPrice, fmtUsd, pnlColor } from "@/lib/format";
import { fmtTemp, type Unit } from "@/lib/units";
import { fmtResolutionDate } from "@/lib/time";

/**
 * PREDICTIVE - the same question asked in both directions.
 *
 * Forward: what does the desk expect, which bucket does that land in, what is
 * the market charging, and how much of the bankroll would that justify.
 * Backward: when it expected that before, was it right - and did being right
 * pay, which is a different question with a different answer.
 *
 * Every panel reads a view from sql/ad4_31_predictive.sql. Nothing here is a
 * second calculation of something a trading page already computes: two
 * implementations of "edge" drift apart and then nobody knows which is real.
 */

interface ConvRow {
  city_key: string; for_date: string; model: string; lead_days: number;
  forecast_max_c: number; observed_max_c: number | null;
  error_c: number | null; is_past: boolean; is_settled: boolean;
}
interface LadderRow {
  city_key: string; for_date: string; band_id: string; band_index: number | null;
  band_label: string | null; band_lo: number | null; band_hi: number | null;
  open_low: boolean | null; open_high: boolean | null; closed: boolean | null;
  won: boolean | null; model_prob: number | null; forecast_max_c: number | null;
  sigma_c: number | null; confidence: number | null; market_price: number | null;
  edge_net_pp: number | null; depth_5c: number | null; tradeable: boolean | null;
}
interface ScoreRow {
  city_key: string; model: string; lead_days: number; n_days: number;
  mae_c: number; bias_c: number; error_sd_c: number | null; worst_c: number;
  hit_rate_pct: number | null; within_1c_pct: number | null;
}
interface BankrollRow {
  day: string; day_pnl: number; n_trades: number; n_won: number;
  cumulative_pnl: number; cumulative_trades: number; cumulative_won: number;
  win_rate_pct: number | null;
}
interface ScalingRow {
  edge_bucket: number; edge_band: string; n: number;
  claimed_edge_pp: number; realised_pp: number;
  settled_yes_pct: number; mean_model_prob_pct: number;
}
interface CityRow { city_key: string; display_name: string | null; unit: string | null }

const MISSING = (file: string, extra?: string) => (
  <>
    Run <code className="rounded bg-panel2 px-1">{file}</code>
    {extra ? <> {extra}</> : null}
  </>
);

export default function PredictivePage() {
  const citiesQ = useQuery<CityRow[]>(
    () => supabase.from("cities").select("city_key,display_name,unit").order("city_key"), []
  );
  const convQ = useQuery<ConvRow[]>(
    () => supabase.from("v_forecast_convergence").select("*").limit(20000), [], 300000
  );
  const ladderQ = useQuery<LadderRow[]>(
    () => supabase.from("v_prediction_ladder").select("*").limit(8000), [], 120000
  );
  const scoreQ = useQuery<ScoreRow[]>(
    () => supabase.from("v_prediction_scorecard").select("*").limit(4000), []
  );
  const bankQ = useQuery<BankrollRow[]>(
    () => supabase.from("v_bankroll_curve").select("*").limit(2000), []
  );
  const scaleQ = useQuery<ScalingRow[]>(
    () => supabase.from("v_edge_scaling").select("*"), []
  );

  const cities = citiesQ.data ?? [];
  const conv = convQ.data ?? [];
  const [city, setCity] = useState<string>("");
  const active = city || conv[0]?.city_key || cities[0]?.city_key || "";
  const unit = (cities.find((c) => c.city_key === active)?.unit ?? "C") as Unit;

  /* ------------------------------------------------- the convergence funnel */
  const funnel = useMemo<ConvergencePoint[]>(
    () => conv.filter((r) => r.city_key === active)
              .map((r) => ({
                for_date: r.for_date, lead_days: r.lead_days,
                forecast_max_c: r.forecast_max_c, observed_max_c: r.observed_max_c,
                model: r.model, is_past: r.is_past,
              })),
    [conv, active]
  );

  // The ladder edges for this city, so the planes are the real buckets rather
  // than a pretty grid.
  const bandEdges = useMemo(() => {
    const edges = new Set<number>();
    for (const r of ladderQ.data ?? []) {
      if (r.city_key !== active) continue;
      if (r.band_lo !== null) edges.add(r.band_lo);
      if (r.band_hi !== null) edges.add(r.band_hi);
    }
    return Array.from(edges).sort((a, b) => a - b);
  }, [ladderQ.data, active]);

  /* --------------------------------------------------------------- forward */
  const forward = useMemo(() => {
    const today = new Date().toISOString().slice(0, 10);
    const rows = (ladderQ.data ?? []).filter((r) => r.for_date >= today && !r.closed);
    const byCityDay = new Map<string, LadderRow[]>();
    for (const r of rows) {
      const k = `${r.city_key}|${r.for_date}`;
      if (!byCityDay.has(k)) byCityDay.set(k, []);
      byCityDay.get(k)!.push(r);
    }
    return Array.from(byCityDay.entries())
      .map(([k, bands]) => {
        const [city_key, for_date] = k.split("|");
        const priced = bands.filter((b) => b.model_prob !== null);
        const best = priced.slice().sort((a, b) => (b.model_prob ?? 0) - (a.model_prob ?? 0))[0];
        const bestEdge = bands
          .filter((b) => b.edge_net_pp !== null && b.tradeable !== false)
          .sort((a, b) => (b.edge_net_pp ?? 0) - (a.edge_net_pp ?? 0))[0];
        return {
          city_key, for_date, n_bands: bands.length, n_priced: priced.length,
          forecast_max_c: best?.forecast_max_c ?? null,
          sigma_c: best?.sigma_c ?? null,
          best_band: best?.band_label ?? null,
          best_prob: best?.model_prob ?? null,
          best_price: best?.market_price ?? null,
          top_edge_pp: bestEdge?.edge_net_pp ?? null,
          top_edge_band: bestEdge?.band_label ?? null,
        };
      })
      .sort((a, b) => (a.for_date === b.for_date
        ? (b.top_edge_pp ?? -99) - (a.top_edge_pp ?? -99)
        : a.for_date.localeCompare(b.for_date)));
  }, [ladderQ.data]);

  /* -------------------------------------------------- actual vs predicted */
  const scatter = useMemo(
    () => conv
      .filter((r) => r.is_settled && r.lead_days === 1 && r.observed_max_c !== null)
      .map((r) => ({
        x: r.forecast_max_c, y: r.observed_max_c as number,
        label: r.city_key,
        color: Math.abs((r.error_c ?? 0)) <= 1 ? "var(--good, #7ee081)" : "var(--bad, #ff6b8a)",
        hint: `${r.city_key} ${r.for_date} · said ${r.forecast_max_c.toFixed(1)}, got ${(r.observed_max_c as number).toFixed(1)}`,
      })),
    [conv]
  );

  const errByLead = useMemo(() => {
    const byModel = new Map<string, Map<number, { sum: number; n: number }>>();
    for (const r of scoreQ.data ?? []) {
      if (!byModel.has(r.model)) byModel.set(r.model, new Map());
      const m = byModel.get(r.model)!;
      const cur = m.get(r.lead_days) ?? { sum: 0, n: 0 };
      cur.sum += r.mae_c * r.n_days; cur.n += r.n_days;
      m.set(r.lead_days, cur);
    }
    const colors = ["var(--accent, #4da3ff)", "#f0a35e", "#7ee081", "#c792ea"];
    return Array.from(byModel.entries()).map(([label, m], i) => ({
      label, color: colors[i % colors.length],
      points: Array.from(m.entries())
        .map(([lead, v]) => ({ x: lead, y: v.sum / v.n }))
        .sort((a, b) => a.x - b.x),
    }));
  }, [scoreQ.data]);

  const bank = bankQ.data ?? [];
  const scaling = scaleQ.data ?? [];

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-lg font-semibold">Predictive</h1>
        <p className="mt-1 max-w-3xl text-xs leading-relaxed text-muted">
          The same question in both directions. <strong className="text-text">Forward</strong>: what
          the desk expects, which bucket that lands in, and what the market is charging for it.
          <strong className="text-text"> Backward</strong>: when it expected that before, was it
          right — and separately, did being right pay. Those are different questions with different
          answers, because entry price, fees and fill size all sit between them.
        </p>
      </div>

      {/* ======================================================== 1. FORWARD == */}
      <section className="space-y-2">
        <h2 className="text-sm font-semibold">What the desk expects</h2>
        <p className="max-w-3xl text-xs leading-relaxed text-muted">
          One row per city-day still open. The bucket the model puts most weight on, what the
          market charges for that bucket, and the largest tradeable edge anywhere on the ladder.
          A row with no probability has a forecast but no priced market yet.
        </p>
        <DataState
          loading={ladderQ.loading} error={ladderQ.error} isEmpty={forward.length === 0}
          emptyTitle="No open markets ahead"
          emptyBody={MISSING("sql/ad4_31_predictive.sql", "then let n8n P0.2 discover markets and P1.5 forecast them.")}
          onRetry={ladderQ.refresh}
        >
          <div className="overflow-x-auto rounded border border-border">
            <table className="w-full text-xs">
              <thead className="bg-panel2 text-muted">
                <tr>
                  <th className="px-2 py-1.5 text-left">City</th>
                  <th className="px-2 py-1.5 text-left">Resolves</th>
                  <th className="px-2 py-1.5 text-right">Forecast</th>
                  <th className="px-2 py-1.5 text-right">σ</th>
                  <th className="px-2 py-1.5 text-left">Most likely bucket</th>
                  <th className="px-2 py-1.5 text-right">Model</th>
                  <th className="px-2 py-1.5 text-right">Market</th>
                  <th className="px-2 py-1.5 text-right">Best edge</th>
                </tr>
              </thead>
              <tbody>
                {forward.slice(0, 60).map((r) => (
                  <tr key={`${r.city_key}|${r.for_date}`} className="border-t border-border">
                    <td className="px-2 py-1.5">
                      <button className="text-accent hover:underline" onClick={() => setCity(r.city_key)}>
                        {r.city_key}
                      </button>
                    </td>
                    <td className="px-2 py-1.5 text-muted">{fmtResolutionDate(r.for_date)}</td>
                    <td className="px-2 py-1.5 text-right tabular-nums">
                      {r.forecast_max_c === null ? "—" : fmtTemp(r.forecast_max_c, unit, 1)}
                    </td>
                    <td className="px-2 py-1.5 text-right tabular-nums text-muted">
                      {r.sigma_c === null ? "—" : `±${r.sigma_c.toFixed(2)}`}
                    </td>
                    <td className="px-2 py-1.5">{r.best_band ?? <span className="text-muted">not priced</span>}</td>
                    <td className="px-2 py-1.5 text-right tabular-nums">{fmtPct(r.best_prob)}</td>
                    <td className="px-2 py-1.5 text-right tabular-nums">{fmtPrice(r.best_price)}</td>
                    <td className={`px-2 py-1.5 text-right tabular-nums ${pnlColor(r.top_edge_pp)}`}>
                      {r.top_edge_pp === null ? "—" : `${r.top_edge_pp > 0 ? "+" : ""}${r.top_edge_pp.toFixed(1)} pp`}
                      {r.top_edge_band ? <span className="ml-1 text-muted">{r.top_edge_band}</span> : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </DataState>
      </section>

      {/* ==================================================== 2. THE FUNNEL == */}
      <section className="space-y-2">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h2 className="text-sm font-semibold">How the forecast converged</h2>
          <select
            value={active} onChange={(e) => setCity(e.target.value)}
            className="rounded border border-border bg-panel2 px-2 py-1 text-xs"
          >
            {(cities.length ? cities.map((c) => c.city_key)
                            : Array.from(new Set(conv.map((c) => c.city_key)))).map((k) => (
              <option key={k} value={k}>{k}</option>
            ))}
          </select>
        </div>
        <p className="max-w-3xl text-xs leading-relaxed text-muted">
          Depth is how many days before resolution, height is temperature, width is the
          resolution day. Each ribbon is one model walking from what it said a week out to what it
          said that morning; the green bar is what actually happened. A good model&apos;s ribbons
          narrow toward that bar. A biased one runs parallel to it and never arrives — and those
          two look identical in a table of mean error. The shaded planes are the real bucket
          boundaries, because a 0.6 °C miss across a line loses and a 0.9 °C miss inside one wins.
        </p>
        <DataState
          loading={convQ.loading} error={convQ.error} isEmpty={funnel.length === 0}
          emptyTitle="No forecast series for this city"
          emptyBody={MISSING("sql/ad4_31_predictive.sql", "and check v_forecast_coverage — a city with no forward forecast has nothing to draw.")}
          onRetry={convQ.refresh}
        >
          <Convergence3D points={funnel} bands={bandEdges} unit={unit} />
        </DataState>
      </section>

      {/* ================================================== 3. DID WE NAIL IT */}
      <section className="grid gap-6 lg:grid-cols-2">
        <div className="space-y-2">
          <h2 className="text-sm font-semibold">Actual against predicted</h2>
          <p className="text-xs leading-relaxed text-muted">
            Day-ahead forecasts against what the day did. On the diagonal is perfect; above it the
            day came in hotter than said. Green is within 1 °C — one bucket.
          </p>
          <DataState
            loading={convQ.loading} error={convQ.error} isEmpty={scatter.length === 0}
            emptyTitle="No settled days yet"
            emptyBody="Actions → Data Bank freezes a day once it settles. This fills in from there."
            onRetry={convQ.refresh}
          >
            <Scatter points={scatter} xLabel="forecast °C" yLabel="observed °C" height={330}
                     xTickFormat={(v) => v.toFixed(0)} yTickFormat={(v) => v.toFixed(0)} />
          </DataState>
        </div>

        <div className="space-y-2">
          <h2 className="text-sm font-semibold">Error by lead day</h2>
          <p className="text-xs leading-relaxed text-muted">
            Mean absolute error against how far ahead the call was made. A model that is sharp
            tomorrow and useless on Friday looks fine averaged together — this is where that shows.
          </p>
          <DataState
            loading={scoreQ.loading} error={scoreQ.error} isEmpty={errByLead.length === 0}
            emptyTitle="No scorecard yet"
            emptyBody={MISSING("sql/ad4_31_predictive.sql", "and run Actions → Data Bank so there are settled days to score.")}
            onRetry={scoreQ.refresh}
          >
            <LineChart series={errByLead} height={280} yLabel="MAE °C"
                       xTickFormat={(v) => `${v}d`} yTickFormat={(v) => v.toFixed(1)} />
          </DataState>
        </div>
      </section>

      {/* ------------------------------------------------ the scorecard table */}
      <section className="space-y-2">
        <h2 className="text-sm font-semibold">Hit rate, per city, per lead</h2>
        <p className="max-w-3xl text-xs leading-relaxed text-muted">
          <strong className="text-text">Bias</strong> is kept separate from error on purpose: a
          model 1.5 °C hot every single day is fixable, one that is 1.5 °C off in random directions
          is not, and pooling them into &ldquo;1.5 °C error&rdquo; throws away which you have.
          <strong className="text-text"> Hit rate</strong> is the only accuracy the market pays
          for — did the day land in the bucket the forecast pointed at.
        </p>
        <DataState
          loading={scoreQ.loading} error={scoreQ.error} isEmpty={(scoreQ.data ?? []).length === 0}
          emptyTitle="Nothing scored yet"
          emptyBody="Needs at least 5 settled days per city, model and lead."
          onRetry={scoreQ.refresh}
        >
          <div className="overflow-x-auto rounded border border-border">
            <table className="w-full text-xs">
              <thead className="bg-panel2 text-muted">
                <tr>
                  <th className="px-2 py-1.5 text-left">City</th>
                  <th className="px-2 py-1.5 text-left">Model</th>
                  <th className="px-2 py-1.5 text-right">Lead</th>
                  <th className="px-2 py-1.5 text-right">Days</th>
                  <th className="px-2 py-1.5 text-right">MAE</th>
                  <th className="px-2 py-1.5 text-right">Bias</th>
                  <th className="px-2 py-1.5 text-right">Worst</th>
                  <th className="px-2 py-1.5 text-right">Hit rate</th>
                  <th className="px-2 py-1.5 text-right">Within 1 °C</th>
                </tr>
              </thead>
              <tbody>
                {(scoreQ.data ?? []).slice(0, 120).map((r) => (
                  <tr key={`${r.city_key}|${r.model}|${r.lead_days}`} className="border-t border-border">
                    <td className="px-2 py-1.5">{r.city_key}</td>
                    <td className="px-2 py-1.5 text-muted">{r.model}</td>
                    <td className="px-2 py-1.5 text-right tabular-nums">{r.lead_days}d</td>
                    <td className="px-2 py-1.5 text-right tabular-nums text-muted">{fmtInt(r.n_days)}</td>
                    <td className="px-2 py-1.5 text-right tabular-nums">{r.mae_c.toFixed(2)}</td>
                    <td className={`px-2 py-1.5 text-right tabular-nums ${Math.abs(r.bias_c) > 0.5 ? "text-warn" : "text-muted"}`}>
                      {r.bias_c > 0 ? "+" : ""}{r.bias_c.toFixed(2)}
                    </td>
                    <td className="px-2 py-1.5 text-right tabular-nums text-muted">{r.worst_c.toFixed(1)}</td>
                    <td className="px-2 py-1.5 text-right tabular-nums">
                      {r.hit_rate_pct === null ? "—" : `${r.hit_rate_pct.toFixed(0)}%`}
                    </td>
                    <td className="px-2 py-1.5 text-right tabular-nums text-muted">
                      {r.within_1c_pct === null ? "—" : `${r.within_1c_pct.toFixed(0)}%`}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </DataState>
      </section>

      {/* ============================================ 4. DID BEING RIGHT PAY == */}
      <section className="grid gap-6 lg:grid-cols-2">
        <div className="space-y-2">
          <h2 className="text-sm font-semibold">Bankroll</h2>
          <p className="text-xs leading-relaxed text-muted">
            Realised P&amp;L, cumulative, from filled signals only — a signal that never filled cost
            nothing and proved nothing. The win rate beside it is running, not final.
          </p>
          <DataState
            loading={bankQ.loading} error={bankQ.error} isEmpty={bank.length === 0}
            emptyTitle="No filled trades yet"
            emptyBody="Every strategy ships disabled. Turn one on, then Actions → Signal Engine."
            onRetry={bankQ.refresh}
          >
            <>
              <LineChart
                height={240} yLabel="cumulative $" zeroLine
                series={[{
                  label: "net P&L", color: "var(--accent, #4da3ff)", fill: true,
                  points: bank.map((r, i) => ({ x: i, y: r.cumulative_pnl })),
                }]}
                xTickFormat={(i) => bank[Math.round(i)]?.day?.slice(5) ?? ""}
                yTickFormat={(v) => fmtUsd(v)}
              />
              <div className="mt-2 grid grid-cols-3 gap-2 text-xs">
                <Stat label="Net" value={fmtUsd(bank[bank.length - 1]?.cumulative_pnl ?? 0)}
                      tone={pnlColor(bank[bank.length - 1]?.cumulative_pnl)} />
                <Stat label="Trades" value={fmtInt(bank[bank.length - 1]?.cumulative_trades ?? 0)} />
                <Stat label="Win rate"
                      value={bank[bank.length - 1]?.win_rate_pct == null ? "—"
                             : `${bank[bank.length - 1].win_rate_pct!.toFixed(0)}%`} />
              </div>
            </>
          </DataState>
        </div>

        <div className="space-y-2">
          <h2 className="text-sm font-semibold">Can it scale?</h2>
          <p className="text-xs leading-relaxed text-muted">
            Claimed edge against what that edge actually returned. If realised tracks claimed, size
            can go up. If realised is flat whatever was claimed, the edge estimate is noise — and
            sizing up multiplies noise, not profit.
          </p>
          <DataState
            loading={scaleQ.loading} error={scaleQ.error} isEmpty={scaling.length === 0}
            emptyTitle="Nothing to compare yet"
            emptyBody="Needs settled bands with both a claimed edge and a market price — Actions → Data Bank writes them."
            onRetry={scaleQ.refresh}
          >
            <>
              <LineChart
                height={240} yLabel="points" zeroLine
                series={[
                  { label: "claimed", color: "var(--muted, #8a93a6)", dashed: true,
                    points: scaling.map((r) => ({ x: r.edge_bucket, y: r.claimed_edge_pp })) },
                  { label: "realised", color: "var(--accent, #4da3ff)", fill: true,
                    points: scaling.map((r) => ({ x: r.edge_bucket, y: r.realised_pp })) },
                ]}
                xTickFormat={(i) => scaling.find((s) => s.edge_bucket === Math.round(i))?.edge_band ?? ""}
                yTickFormat={(v) => v.toFixed(0)}
              />
              <div className="mt-2 space-y-1 text-[11px] text-muted">
                {scaling.map((r) => (
                  <div key={r.edge_bucket} className="flex justify-between tabular-nums">
                    <span>{r.edge_band} · {fmtInt(r.n)} bands</span>
                    <span>
                      claimed {r.claimed_edge_pp.toFixed(1)} → realised{" "}
                      <span className={pnlColor(r.realised_pp)}>{r.realised_pp.toFixed(1)}</span>
                    </span>
                  </div>
                ))}
              </div>
            </>
          </DataState>
        </div>
      </section>
    </div>
  );
}

function Stat({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return (
    <div className="rounded border border-border bg-panel2 px-2 py-1.5">
      <div className="text-[10px] uppercase tracking-wide text-muted">{label}</div>
      <div className={`tabular-nums ${tone ?? ""}`}>{value}</div>
    </div>
  );
}
