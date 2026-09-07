"use client";

import { useMemo, useState } from "react";
import { supabase } from "@/lib/supabase";
import { useQuery } from "@/lib/useQuery";
import { DataState } from "@/components/DataState";
import { Freshness, FreshnessRow } from "@/components/Provenance";
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
  const cities = citiesQ.data ?? [];
  const [city, setCity] = useState<string>("");
  // Declared HERE, above the queries, because the convergence and ladder
  // queries are now filtered by it rather than filtered in the browser.
  const active = city || cities[0]?.city_key || "";
  /**
   * WHY THESE ARE SPLIT AND FILTERED, and why it was showing one city.
   *
   * This page used to ask for `v_forecast_convergence` with `.limit(20000)`
   * and then filter by city in the browser. That view carries one row per
   * city, per day, per model, per lead: on a real desk it is 55,000 rows at
   * three weeks of history and grows every day. PostgREST does not warn when
   * it truncates - it returns the first 20,000 rows in view order, and that
   * view is ordered by city_key. So the browser received the alphabetically
   * first cities and nothing else, and every other city's funnel and 3D
   * layers were correctly reported as "no data".
   *
   * It looked like a bug in one city. It was the twentieth-thousandth row.
   *
   * The two panels want different slices, so they are now two queries:
   *   convQ    ONE city, every model and lead - the funnel and its ladder
   *   settledQ EVERY city, but only settled days at lead 1 - the scatter
   * Both are bounded by what they draw rather than by a number, and both
   * carry a truncation guard (useQuery's `limit` option) so if either ever
   * does hit its ceiling the page says so instead of quietly showing less.
   */
  const convQ = useQuery<ConvRow[]>(
    // NEWEST DAYS FIRST, and capped at what the server will actually return.
    // One city over 62 days at three models and eight leads is ~1,500 rows -
    // still past Supabase's 1,000-row ceiling - so the order matters: the
    // chart draws the most recent days, and those are the ones that arrive.
    () => supabase.from("v_forecast_convergence").select("*")
            .eq("city_key", active).order("for_date", { ascending: false }).limit(1000),
    [active], 300000, 1000
  );
  const settledQ = useQuery<ConvRow[]>(
    () => supabase.from("v_forecast_convergence").select("*")
            .eq("is_settled", true).eq("lead_days", 1)
            .order("for_date", { ascending: false }).limit(1000),
    [], 300000, 1000
  );
  // Forward only: the ladder is drawn for days that have not resolved, and
  // the whole table is one row per band per day for every city.
  const ladderQ = useQuery<LadderRow[]>(
    () => supabase.from("v_prediction_ladder").select("*")
            .gte("for_date", new Date().toISOString().slice(0, 10)).limit(1000),
    [], 120000, 1000
  );
  const cityLadderQ = useQuery<Array<{ band_lo: number | null; band_hi: number | null }>>(
    () => supabase.from("v_prediction_ladder").select("band_lo,band_hi")
            .eq("city_key", active).limit(1000),
    [active], 120000, 1000
  );
  const scoreQ = useQuery<ScoreRow[]>(
    () => supabase.from("v_prediction_scorecard").select("*").limit(4000), [], undefined, 4000
  );
  const bankQ = useQuery<BankrollRow[]>(
    () => supabase.from("v_bankroll_curve").select("*").limit(2000), [], undefined, 2000
  );
  const scaleQ = useQuery<ScalingRow[]>(
    () => supabase.from("v_edge_scaling").select("*"), []
  );

  const conv = convQ.data ?? [];
  const unit = (cities.find((c) => c.city_key === active)?.unit ?? "C") as Unit;

  /* ------------------------------------------------- the convergence funnel */
  const funnel = useMemo<ConvergencePoint[]>(
    () => conv.map((r) => ({
      for_date: r.for_date, lead_days: r.lead_days,
      forecast_max_c: r.forecast_max_c, observed_max_c: r.observed_max_c,
      model: r.model, is_past: r.is_past,
    })),
    [conv]
  );

  // The ladder edges for this city, so the planes are the real buckets rather
  // than a pretty grid.
  const bandEdges = useMemo(() => {
    const edges = new Set<number>();
    for (const r of cityLadderQ.data ?? []) {
      if (r.band_lo !== null) edges.add(r.band_lo);
      if (r.band_hi !== null) edges.add(r.band_hi);
    }
    return Array.from(edges).sort((a, b) => a - b);
  }, [cityLadderQ.data]);

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
  const settled = settledQ.data ?? [];
  const scatter = useMemo(
    () => settled
      .filter((r) => r.observed_max_c !== null)
      .map((r) => ({
        x: r.forecast_max_c, y: r.observed_max_c as number,
        label: r.city_key,
        color: Math.abs((r.error_c ?? 0)) <= 1 ? "var(--good, #7ee081)" : "var(--bad, #ff6b8a)",
        hint: `${r.city_key} ${r.for_date} · said ${r.forecast_max_c.toFixed(1)}, got ${(r.observed_max_c as number).toFixed(1)}`,
      })),
    [settled]
  );

  /**
   * The six numbers the scatter is hiding.
   *
   * A cloud of dots cannot be argued with in either direction: it looks
   * roughly diagonal whether the desk is calling days to a quarter of a degree
   * or missing whole buckets. These are computed from the same settled rows
   * the chart draws, so the picture and the numbers can never disagree.
   *
   * ERROR AND BIAS ARE KEPT APART. A forecast that is 1.5 °C hot every day is
   * a correction you can apply; one that is 1.5 °C off in random directions is
   * not, and pooling them into "1.5 °C error" throws away which you have.
   */
  const accuracy = useMemo(() => {
    const rows = settled.filter(
      (r) => r.observed_max_c !== null && r.error_c !== null
    );
    const n = rows.length;
    if (!n) {
      return {
        n: 0, nCities: 0, mae: 0, bias: 0, within1: 0, worst: 0, worstWhere: null as string | null,
        hotPct: 50, byCity: [] as Array<{ city: string; bias: number; n: number }>, maxCityBias: 1,
        verdict: "Nothing has settled yet, so there is nothing to score.",
        verdictTone: "text-muted",
      };
    }
    // error_c is signed: observed minus forecast, so positive = hotter than said.
    const errs = rows.map((r) => r.error_c as number);
    const mae = errs.reduce((a, e) => a + Math.abs(e), 0) / n;
    const bias = errs.reduce((a, e) => a + e, 0) / n;
    const within1 = errs.filter((e) => Math.abs(e) <= 1).length / n;
    const hotPct = (errs.filter((e) => e > 0).length / n) * 100;
    let worst = 0;
    let worstWhere: string | null = null;
    for (const r of rows) {
      const a = Math.abs(r.error_c as number);
      if (a > worst) {
        worst = a;
        worstWhere = `${r.city_key} ${r.for_date}`;
      }
    }
    const per = new Map<string, { sum: number; n: number }>();
    for (const r of rows) {
      const cur = per.get(r.city_key) ?? { sum: 0, n: 0 };
      cur.sum += r.error_c as number;
      cur.n += 1;
      per.set(r.city_key, cur);
    }
    const byCity = Array.from(per.entries())
      .map(([city, v]) => ({ city, bias: v.sum / v.n, n: v.n }))
      .sort((a, b) => Math.abs(b.bias) - Math.abs(a.bias));
    const maxCityBias = Math.max(0.5, ...byCity.map((c) => Math.abs(c.bias)));

    // One sentence, ordered by what actually disqualifies the forecast first.
    let verdict: string;
    let verdictTone = "text-muted";
    if (n < 20) {
      verdict = `Only ${n} settled day(s) so far — enough to look at, not enough to conclude from. Treat every number here as provisional until there are a few weeks.`;
      verdictTone = "text-warn";
    } else if (mae > 2) {
      verdict = `Missing by ${mae.toFixed(2)} °C on average is wider than a bucket, so the forecast is not resolving which bucket wins. Sizing off this edge is sizing off noise.`;
      verdictTone = "text-bad";
    } else if (Math.abs(bias) >= 0.5) {
      verdict = `A steady ${Math.abs(bias).toFixed(2)} °C lean ${bias > 0 ? "cool" : "hot"} — days come in ${bias > 0 ? "hotter" : "cooler"} than forecast far more often than not. That is systematic, which means it is correctable: it is exactly what the fitted model absorbs. Until it does, the desk is paying for it every day.`;
      verdictTone = "text-warn";
    } else if (within1 >= 0.6) {
      verdict = `${(within1 * 100).toFixed(0)}% of days land within one bucket of the call, with no meaningful lean either way. This is a forecast worth pricing against.`;
      verdictTone = "text-good";
    } else {
      verdict = `No systematic lean, but only ${(within1 * 100).toFixed(0)}% of days land within a bucket. The direction is honest and the sharpness is not — edges here will be real but small.`;
      verdictTone = "text-muted";
    }
    return { n, nCities: per.size, mae, bias, within1, worst, worstWhere, hotPct, byCity, maxCityBias, verdict, verdictTone };
  }, [settled]);

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

  /** Where the forecast stops resolving buckets, read off the same series. */
  const decay = useMemo(() => {
    const all = errByLead.flatMap((s) => s.points);
    if (!all.length) return { text: "" };
    const byLead = new Map<number, number[]>();
    for (const p of all) {
      if (!byLead.has(p.x)) byLead.set(p.x, []);
      byLead.get(p.x)!.push(p.y);
    }
    const avg = Array.from(byLead.entries())
      .map(([lead, ys]) => ({ lead, mae: ys.reduce((a, b) => a + b, 0) / ys.length }))
      .sort((a, b) => a.lead - b.lead);
    const crossed = avg.find((a) => a.mae > 1);
    const best = avg[0];
    if (!crossed) {
      return {
        text: `Every lead day shown still averages under 1 °C — the forecast holds its resolution across the whole window, so there is no lead day at which the desk has to stop trusting it.`,
      };
    }
    if (crossed.lead === best?.lead) {
      return {
        text: `Even at ${crossed.lead} day(s) out the average miss is ${crossed.mae.toFixed(2)} °C — already wider than a bucket. The forecast is not resolving which bucket wins at any lead shown here.`,
      };
    }
    return {
      text: `The average miss crosses one bucket (1 °C) at ${crossed.lead} days out, at ${crossed.mae.toFixed(2)} °C. Inside that the forecast is picking buckets; past it, it is picking a range. Day ${best.lead} is the sharpest at ${best.mae.toFixed(2)} °C.`,
    };
  }, [errByLead]);


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
        {/* WHAT THIS PAGE STANDS ON. A thin page and an unfed page look
            identical, and only one of them is worth investigating. */}
        <div className="mt-2">
          <FreshnessRow relations={["cities", "fact_forecast_outcome", "weather_forecasts", "bands", "markets", "edges", "band_probabilities", "fact_signal_outcome"]} />
        </div>
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
          relation="v_prediction_ladder"
          truncated={ladderQ.truncated}
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
            {cities.map((c) => (
              <option key={c.city_key} value={c.city_key}>
                {c.display_name ?? c.city_key}
              </option>
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
          relation="v_forecast_convergence"
          truncated={convQ.truncated}
          loading={convQ.loading} error={convQ.error} isEmpty={funnel.length === 0}
          emptyTitle="No forecast series for this city"
          emptyBody={MISSING("sql/ad4_31_predictive.sql", "and check v_forecast_coverage — a city with no forward forecast has nothing to draw.")}
          onRetry={convQ.refresh}
        >
          <Convergence3D points={funnel} bands={bandEdges} unit={unit} />
        </DataState>
      </section>

      {/* =========================================== 3. ACTUAL AGAINST PREDICTED
          Its own section, full width, because it is the one question that
          decides whether anything else on this page is worth reading. It used
          to be a half-width chart sharing a row with error-by-lead, with two
          sentences and no numbers - a picture of a cloud of dots that could
          not be argued with either way. */}
      <section className="space-y-3">
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <h2 className="text-sm font-semibold">Actual against predicted</h2>
          <Freshness relation="v_forecast_convergence" />
          <span className="text-[11px] text-muted">
            the only question that decides whether the rest of this page is worth reading
          </span>
        </div>
        <p className="max-w-3xl text-xs leading-relaxed text-muted">
          Every settled day, one dot: what was forecast a day out against what the day actually
          did. On the diagonal is a perfect call; above it the day came in hotter than said, below
          it cooler. Green is within 1 °C, which is roughly one bucket — the resolution the market
          actually pays at, so a dot being green matters more than it being close.{" "}
          <strong className="text-text">Bias and error are read separately</strong>: a forecast that
          is 1.5 °C hot every day is a correction you can apply, and one that is 1.5 °C off in
          random directions is not. The numbers below split them.
        </p>

        <DataState
          relation="v_forecast_convergence"
          truncated={settledQ.truncated}
          loading={settledQ.loading}
          error={settledQ.error}
          isEmpty={scatter.length === 0}
          emptyTitle="No settled days yet"
          emptyBody="Nothing has settled, so there is nothing to score. This fills in once a market resolves and the day is frozen."
          onRetry={convQ.refresh}
        >
          <div className="grid gap-4 lg:grid-cols-[minmax(0,1.35fr)_minmax(0,1fr)]">
            <div className="rounded border border-border bg-panel/60 p-3">
              <Scatter
                points={scatter}
                xLabel="forecast °C"
                yLabel="observed °C"
                height={340}
                xTickFormat={(v) => v.toFixed(0)}
                yTickFormat={(v) => v.toFixed(0)}
                // 2,134 dots each carrying a city name is a mat of overlapping
                // words with the data underneath it. Names on hover, and the
                // diagonal drawn, which is the only reference the chart has.
                maxLabels={40}
                zoomable
                diagonal
              />
            </div>

            <div className="space-y-3">
              {/* ---- the six numbers the cloud of dots is hiding ---------- */}
              <div className="grid grid-cols-2 gap-2">
                <Readout
                  label="Days scored"
                  value={fmtInt(accuracy.n)}
                  sub={`${accuracy.nCities} cit${accuracy.nCities === 1 ? "y" : "ies"}`}
                />
                <Readout
                  label="Within 1 °C"
                  value={accuracy.n ? `${(accuracy.within1 * 100).toFixed(0)}%` : "—"}
                  sub="one bucket wide"
                  tone={accuracy.within1 >= 0.6 ? "good" : accuracy.within1 >= 0.4 ? "warn" : "bad"}
                />
                <Readout
                  label="Average miss"
                  value={accuracy.n ? `${accuracy.mae.toFixed(2)} °C` : "—"}
                  sub="how far off, ignoring direction"
                  tone={accuracy.mae <= 1 ? "good" : accuracy.mae <= 2 ? "warn" : "bad"}
                />
                <Readout
                  label="Bias"
                  value={accuracy.n ? `${accuracy.bias > 0 ? "+" : ""}${accuracy.bias.toFixed(2)} °C` : "—"}
                  sub={
                    Math.abs(accuracy.bias) < 0.25
                      ? "no systematic lean"
                      : accuracy.bias > 0
                        ? "days run hotter than said"
                        : "days run cooler than said"
                  }
                  tone={Math.abs(accuracy.bias) < 0.25 ? "good" : "warn"}
                />
                <Readout
                  label="Worst miss"
                  value={accuracy.n ? `${accuracy.worst.toFixed(1)} °C` : "—"}
                  sub={accuracy.worstWhere ?? ""}
                  tone={accuracy.worst >= 5 ? "bad" : accuracy.worst >= 3 ? "warn" : "good"}
                />
                <Readout
                  label="Too hot / too cool"
                  value={accuracy.n ? `${accuracy.hotPct.toFixed(0)} / ${(100 - accuracy.hotPct).toFixed(0)}` : "—"}
                  sub="a 50/50 split means no lean"
                  tone={Math.abs(accuracy.hotPct - 50) < 12 ? "good" : "warn"}
                />
              </div>

              {/* ---- what those six numbers mean, in one sentence -------- */}
              <div className="rounded border border-border bg-panel2/40 p-2.5">
                <div className="text-[10px] uppercase tracking-wide text-muted">What this says</div>
                <p className={`mt-1 text-xs leading-relaxed ${accuracy.verdictTone}`}>
                  {accuracy.verdict}
                </p>
              </div>

              {/* ---- and where the misses are concentrated --------------- */}
              {accuracy.byCity.length > 1 && (
                <div className="rounded border border-border bg-panel/60 p-2.5">
                  <div className="text-[10px] uppercase tracking-wide text-muted">
                    Lean per city — bars right of the line ran hotter than forecast
                  </div>
                  <div className="mt-1.5 space-y-1">
                    {accuracy.byCity.slice(0, 8).map((c) => (
                      <BiasBar key={c.city} city={c.city} bias={c.bias} n={c.n} max={accuracy.maxCityBias} />
                    ))}
                  </div>
                  <p className="mt-1.5 text-[10px] leading-snug text-muted">
                    A city with a consistent lean is a correction waiting to be applied, not noise —
                    it is exactly what the fitted model in <code>derived_weather_model</code> exists
                    to absorb.
                  </p>
                </div>
              )}
            </div>
          </div>
        </DataState>
      </section>

      {/* ---------------------------------------------- error against lead day */}
      <section className="space-y-2">
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <h2 className="text-sm font-semibold">How fast the forecast decays</h2>
          <Freshness relation="v_prediction_scorecard" />
        </div>
        <p className="max-w-3xl text-xs leading-relaxed text-muted">
          The same error, split by how far ahead the call was made. A model that is sharp tomorrow
          and useless on Friday averages out to &ldquo;fine&rdquo; — this is the only place that
          shows. The practical use is picking the lead day at which to stop trusting it: where the
          line crosses one bucket wide, the forecast has stopped resolving which bucket wins.
        </p>
        <DataState
          relation="v_prediction_scorecard"
          loading={scoreQ.loading}
          error={scoreQ.error}
          isEmpty={errByLead.length === 0}
          emptyTitle="No scorecard yet"
          emptyBody={MISSING("sql/ad4_31_predictive.sql", "and let a few days settle so there is something to score.")}
          onRetry={scoreQ.refresh}
        >
          <div className="rounded border border-border bg-panel/60 p-3">
            <LineChart
              series={errByLead}
              height={260}
              yLabel="average miss °C"
              xTickFormat={(v) => `${v}d`}
              yTickFormat={(v) => v.toFixed(1)}
            />
            <p className="mt-2 text-[11px] leading-relaxed text-muted">
              {decay.text}
            </p>
          </div>
        </DataState>
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
          relation="v_prediction_scorecard"
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
          relation="v_bankroll_curve"
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
          relation="v_edge_scaling"
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

/** One number with what it means under it. Colour is a verdict, not decoration. */
function Readout({
  label,
  value,
  sub,
  tone,
}: {
  label: string;
  value: string;
  sub?: string;
  tone?: "good" | "warn" | "bad";
}) {
  const toneClass =
    tone === "good" ? "text-good" : tone === "warn" ? "text-warn" : tone === "bad" ? "text-bad" : "text-text";
  return (
    <div className="rounded border border-border bg-panel/60 p-2">
      <div className="text-[10px] uppercase tracking-wide text-muted">{label}</div>
      <div className={`mt-0.5 text-base font-semibold tabular-nums ${toneClass}`}>{value}</div>
      {sub ? <div className="mt-0.5 text-[10px] leading-snug text-muted">{sub}</div> : null}
    </div>
  );
}

/**
 * A city's lean, drawn from a centre line so direction is visible without
 * reading a sign. Right of the line = days ran hotter than forecast.
 */
function BiasBar({ city, bias, n, max }: { city: string; bias: number; n: number; max: number }) {
  const frac = Math.min(1, Math.abs(bias) / max);
  const pct = frac * 50;
  const hot = bias > 0;
  return (
    <div className="flex items-center gap-2 text-[10px]">
      <span className="w-16 shrink-0 truncate font-mono text-muted" title={`${city} · ${n} day(s)`}>
        {city}
      </span>
      <span className="relative h-2.5 flex-1 rounded-sm bg-panel2">
        <span className="absolute inset-y-0 left-1/2 w-px bg-border" />
        <span
          className={`absolute inset-y-0 rounded-sm ${hot ? "bg-bad/70" : "bg-accent/70"}`}
          style={hot ? { left: "50%", width: `${pct}%` } : { right: "50%", width: `${pct}%` }}
        />
      </span>
      <span className={`w-14 shrink-0 text-right tabular-nums ${Math.abs(bias) >= 0.5 ? "text-warn" : "text-muted"}`}>
        {bias > 0 ? "+" : ""}
        {bias.toFixed(2)}°
      </span>
    </div>
  );
}
