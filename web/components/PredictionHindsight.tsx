"use client";

import { useMemo, useState } from "react";
import { supabase } from "@/lib/supabase";
import { useQuery } from "@/lib/useQuery";
import { DataState } from "@/components/DataState";

/**
 * WAS THE DESK RIGHT? - graded only on calls frozen before the answer.
 *
 * Until 26 Sep this panel graded each band's LATEST probability, with no time
 * limit. The engine re-prices through the day and after it, reading the
 * running maximum, so what it graded was mostly the thermometer read back:
 * over its last 30 days 627 of 647 city-days had last been priced after 15:00
 * local, 306 after the day had ended, and it showed 25 Sep as 32 hits of 35.
 *
 * Every row now names the moment its call was frozen (called_when):
 *   day_ahead    the last probability computed before the city's local day
 *                began - the forecast in the only sense that matters
 *   d1_eve ...   the prediction checkpoints, each written once at a fixed
 *                local time and never changed, graded against the bucket the
 *                venue confirmed
 *   postpeak_1h  an hour after the peak the day has mostly answered itself;
 *                shown apart and never as the headline
 *
 * THE HEADLINE IS THE CALIBRATION GAP of the day-ahead call, not the hit rate.
 * A hit rate alone cannot be judged - eleven buckets means blind guessing
 * scores about 9% - and what matters is whether the stated confidence matches
 * the realised frequency. The market's record on the same days sits beside
 * the engine's, because that is the price any edge has to beat.
 *
 * The totals are added up in the database (v_prediction_hindsight_summary):
 * PostgREST returns at most 1,000 rows, and the row view passes that in days.
 */

type Summary = {
  called_when: string; call_order: number; after_peak: boolean;
  days: number; hits: number; claimed_pct: number | null;
  market_days: number; market_hits: number; model_hits_on_market_days: number;
  mae_c: number | null; bias_c: number | null;
  first_day: string | null; last_day: string | null;
};

type Row = {
  city_key: string; for_date: string; called_when: string; called_at: string | null;
  predicted_band: string | null; predicted_pct: number | null;
  actual_band: string | null; observed_max_c: number | null;
  forecast_max_c: number | null; forecast_error_c: number | null;
  hit: boolean | null; market_band: string | null; market_hit: boolean | null;
  outcome_source: string | null;
};

const n = (v: unknown) => {
  const x = typeof v === "number" ? v : parseFloat(String(v ?? ""));
  return Number.isFinite(x) ? x : null;
};

export const MOMENT: Record<string, string> = {
  day_ahead: "Day ahead",
  d1_eve: "Evening before",
  morning: "Morning",
  noon: "Noon",
  prepeak_2h: "2 h before peak",
  prepeak_1h: "1 h before peak",
  postpeak_1h: "1 h after peak",
};

const WHEN: Record<string, string> = {
  day_ahead: "the last pricing before the city's local day began",
  d1_eve: "frozen the evening before, local time",
  morning: "frozen at the city's morning checkpoint",
  noon: "frozen at local noon",
  prepeak_2h: "frozen two hours before the usual peak",
  prepeak_1h: "frozen one hour before the usual peak",
  postpeak_1h: "an hour after the peak - the day has mostly answered itself",
};

/** One moment's record, with the interval that says whether its gap means anything. */
export function summarise(s: Summary) {
  const days = Number(s.days);
  const hits = Number(s.hits);
  if (!days) return null;
  const actual = hits / days;
  const claimed = (n(s.claimed_pct) ?? 0) / 100;
  // Two standard errors on the realised rate. A gap inside this band is not
  // yet evidence of miscalibration.
  const ci = 2 * Math.sqrt((actual * (1 - actual)) / days);
  const md = Number(s.market_days);
  return {
    days, hits, actual, claimed, ci,
    gap: actual - claimed,
    significant: Math.abs(actual - claimed) > ci,
    marketDays: md,
    marketRate: md ? Number(s.market_hits) / md : null,
    modelRateOnMarketDays: md ? Number(s.model_hits_on_market_days) / md : null,
    mae: n(s.mae_c),
    bias: n(s.bias_c),
  };
}

const pct = (x: number) => `${(x * 100).toFixed(1)}%`;

export default function PredictionHindsight() {
  const [moment, setMoment] = useState("day_ahead");
  const sq = useQuery<Summary[]>(
    () => supabase.from("v_prediction_hindsight_summary").select("*").order("call_order"),
    [], 60000, 50);
  const rq = useQuery<Row[]>(
    () => supabase.from("v_prediction_hindsight")
      .select("city_key,for_date,called_when,called_at,predicted_band,predicted_pct,actual_band,observed_max_c,forecast_max_c,forecast_error_c,hit,market_band,market_hit,outcome_source")
      .eq("called_when", moment)
      .order("for_date", { ascending: false }).order("city_key")
      .limit(300),
    [moment], 60000, 300);

  const summaries = useMemo(() => sq.data ?? [], [sq.data]);
  const rows = useMemo(() => rq.data ?? [], [rq.data]);
  const head = useMemo(() => {
    const d = summaries.find(x => x.called_when === "day_ahead");
    return d ? summarise(d) : null;
  }, [summaries]);
  const before = summaries.filter(x => !x.after_peak);
  const after = summaries.filter(x => x.after_peak);
  const s = head;

  const line = (x: Summary) => {
    const r = summarise(x);
    if (!r) return null;
    return <tr key={x.called_when} className="border-t border-border">
      <td className="px-2 py-1" title={WHEN[x.called_when] ?? ""}>{MOMENT[x.called_when] ?? x.called_when}</td>
      <td className="px-2 py-1 font-mono">{r.days}</td>
      <td className="px-2 py-1 font-mono">{pct(r.actual)}</td>
      <td className="px-2 py-1 font-mono text-muted">{pct(r.claimed)}</td>
      <td className={`px-2 py-1 font-mono ${r.significant ? (r.gap < 0 ? "text-bad" : "text-good") : "text-muted"}`}
          title={`±${(r.ci * 100).toFixed(1)}pp at 95% on ${r.days} days`}>
        {r.gap > 0 ? "+" : ""}{(r.gap * 100).toFixed(1)} pp{r.significant ? "" : " (noise)"}
      </td>
      <td className="px-2 py-1 font-mono">
        {r.marketRate === null ? "—" : `${pct(r.marketRate)} vs ${pct(r.modelRateOnMarketDays ?? 0)}`}
      </td>
      <td className="px-2 py-1 font-mono text-muted">{r.marketDays || "—"}</td>
      <td className="px-2 py-1 text-muted">{x.first_day === x.last_day ? x.first_day : `${x.first_day} – ${x.last_day}`}</td>
    </tr>;
  };

  return <section className="space-y-2">
    <h2 className="text-sm font-semibold">Was it right?</h2>
    <p className="max-w-3xl text-xs leading-relaxed text-muted">
      Every call graded here was <strong>frozen before the answer was known</strong> and scored only
      after the day settled: the day-ahead call is the last pricing before the city&apos;s local day
      began, and each checkpoint is written once at a fixed local time and never changed. Nothing
      priced after the moment it names counts. <strong>The number that matters is the gap</strong>{" "}
      between what the desk claimed and what happened — a hit rate on its own says nothing, since
      eleven buckets means blind guessing scores about 9%.
    </p>
    <DataState
      relation="v_prediction_hindsight_summary"
      truncated={sq.truncated}
      loading={sq.loading} error={sq.error} isEmpty={!summaries.length}
      emptyTitle="Nothing has settled yet"
      emptyBody={<>A day appears here once its outcome is banked - into{" "}
        <code className="rounded bg-panel2 px-1">fact_band_outcome</code> for the day-ahead call and{" "}
        <code className="rounded bg-panel2 px-1">fact_checkpoint_outcome</code> for the checkpoints -
        which the daily pipeline does after the day ends and the venue confirms the winner.</>}
      onRetry={sq.refresh}
    >
      {s && <div className="grid gap-3 rounded border border-border bg-panel p-4 sm:grid-cols-2 lg:grid-cols-5">
        <div>
          <div className="text-xs text-muted">Day-ahead top pick was right</div>
          <div className="font-mono text-xl">{pct(s.actual)}</div>
          <div className="text-xs text-muted">{s.hits} of {s.days} city-days</div>
        </div>
        <div>
          <div className="text-xs text-muted">The desk claimed</div>
          <div className="font-mono text-xl">{pct(s.claimed)}</div>
          <div className="text-xs text-muted">average stated confidence</div>
        </div>
        <div title="Realised minus claimed. Negative means it was more confident than it turned out to deserve.">
          <div className="text-xs text-muted">Calibration gap</div>
          <div className={`font-mono text-xl ${s.significant ? (s.gap < 0 ? "text-bad" : "text-good") : "text-muted"}`}>
            {s.gap > 0 ? "+" : ""}{(s.gap * 100).toFixed(1)} pp
          </div>
          <div className="text-xs text-muted">±{(s.ci * 100).toFixed(1)} at 95%</div>
        </div>
        <div title="On the days the market had a favourite before the day began: how often its favourite won, against how often the engine's did on those same days.">
          <div className="text-xs text-muted">Market vs engine, same days</div>
          <div className="font-mono text-xl">
            {s.marketRate === null ? "—" : `${pct(s.marketRate)} / ${pct(s.modelRateOnMarketDays ?? 0)}`}
          </div>
          <div className="text-xs text-muted">{s.marketDays} city-days both called</div>
        </div>
        <div title="Positive means the day came out warmer than the centre the engine priced (recorded from 23 Sep; days without one are left out).">
          <div className="text-xs text-muted">Priced centre error</div>
          <div className="font-mono text-xl">{s.mae === null ? "—" : `${s.mae.toFixed(2)}°C`}</div>
          <div className="text-xs text-muted">
            mean absolute{s.bias === null ? "" : `; bias ${s.bias > 0 ? "+" : ""}${s.bias.toFixed(2)}°C`}
          </div>
        </div>
      </div>}

      {s && <p className={`text-xs ${s.significant ? "text-warn" : "text-muted"}`}>
        {s.significant
          ? s.gap < 0
            ? `Overconfident: it states ${pct(s.claimed)} on its best bucket and hits ${pct(s.actual)}. `
              + `The gap is larger than the ${(s.ci * 100).toFixed(1)}pp interval on ${s.days} days, so it is `
              + `unlikely to be noise — the stated probabilities are too high, not the forecast too wrong.`
            : `Underconfident: it hits ${pct(s.actual)} while only claiming ${pct(s.claimed)}, by more than `
              + `the ${(s.ci * 100).toFixed(1)}pp interval. Edges computed from these probabilities are understated.`
          : `Claimed ${pct(s.claimed)} and hit ${pct(s.actual)} over ${s.days} days. The difference is inside `
            + `the ±${(s.ci * 100).toFixed(1)}pp interval, so there is no measured miscalibration yet — `
            + `not the same as being well calibrated, only that this much evidence cannot tell.`}
      </p>}

      <div className="overflow-x-auto rounded border border-border">
        <table className="w-full text-xs">
          <thead className="bg-panel2 text-muted"><tr>
            {["Call frozen", "City-days", "Engine hit", "Claimed", "Gap", "Market / engine, same days", "Market days", "Settled days"]
              .map((h, i) => <th key={i} className="px-2 py-1.5 text-left font-normal">{h}</th>)}
          </tr></thead>
          <tbody>
            {before.map(line)}
            {after.length > 0 && <tr className="border-t border-border bg-panel2">
              <td colSpan={8} className="px-2 py-1 text-muted">
                After the peak — not a forecast: the running maximum is mostly known by now.
              </td>
            </tr>}
            {after.map(line)}
          </tbody>
        </table>
      </div>
      <p className="text-xs text-muted">
        &quot;(noise)&quot; means the gap is inside two standard errors of the hit rate on that many
        days. The checkpoints began on 24 Sep, so their samples are still small.
      </p>
    </DataState>

    <div className="flex flex-wrap items-center gap-2 pt-2 text-xs">
      <span className="text-muted">Calls frozen at</span>
      {Object.keys(MOMENT).map(k =>
        <button key={k} onClick={() => setMoment(k)}
          className={`rounded border px-2 py-0.5 ${moment === k ? "border-accent text-accent" : "border-border text-muted"}`}>
          {MOMENT[k]}
        </button>)}
    </div>
    <DataState
      relation="v_prediction_hindsight"
      truncated={false}
      loading={rq.loading} error={rq.error} isEmpty={!rows.length}
      emptyTitle="No settled calls at this moment yet"
      emptyBody={<>{WHEN[moment]}. Rows appear after the day ends and its outcome is banked.</>}
      onRetry={rq.refresh}
    >
      <div className="overflow-x-auto rounded border border-border">
        <table className="w-full text-xs">
          <thead className="bg-panel2 text-muted"><tr>
            {["Day", "City", "Engine called", "Sure", "Market called", "Actual", "Observed", "Priced centre", "Error", "", "Source"]
              .map((h, i) => <th key={i} className="px-2 py-1.5 text-left font-normal">{h}</th>)}
          </tr></thead>
          <tbody>
            {rows.map(r => <tr key={`${r.city_key}-${r.for_date}`} className="border-t border-border">
              <td className="whitespace-nowrap px-2 py-1" title={r.called_at ? `called ${r.called_at}` : ""}>{r.for_date}</td>
              <td className="px-2 py-1">{r.city_key}</td>
              <td className="px-2 py-1">{r.predicted_band ?? "—"}</td>
              <td className="px-2 py-1 font-mono">{r.predicted_pct === null ? "—" : `${r.predicted_pct}%`}</td>
              <td className={`px-2 py-1 ${r.market_hit === null ? "text-muted" : r.market_hit ? "text-good" : "text-bad"}`}>
                {r.market_band ?? "—"}
              </td>
              <td className="px-2 py-1">{r.actual_band ?? "—"}</td>
              <td className="px-2 py-1 font-mono">{r.observed_max_c === null ? "—" : `${r.observed_max_c}°C`}</td>
              {/* the centre that was priced, in every row (v_prediction_hindsight, audit repair 3) */}
              <td className="px-2 py-1 font-mono text-muted">{r.forecast_max_c === null ? "not recorded" : `${r.forecast_max_c}°C`}</td>
              <td className={`px-2 py-1 font-mono ${Math.abs(n(r.forecast_error_c) ?? 0) > 2 ? "text-warn" : "text-muted"}`}>
                {r.forecast_error_c === null ? "—" : `${n(r.forecast_error_c)! > 0 ? "+" : ""}${r.forecast_error_c}`}
              </td>
              <td className={`px-2 py-1 ${r.hit ? "text-good" : "text-bad"}`}>{r.hit ? "hit" : "miss"}</td>
              <td className="px-2 py-1 text-muted" title={r.outcome_source === "venue"
                ? "The venue confirmed which contract paid."
                : "Banked from the weather outcome after the day ended."}>
                {r.outcome_source ?? "—"}
              </td>
            </tr>)}
          </tbody>
        </table>
      </div>
      {rows.length >= 300 && <p className="text-xs text-muted">
        Showing the 300 most recent calls at this moment; the figures above use all of them.
      </p>}
    </DataState>
  </section>;
}
