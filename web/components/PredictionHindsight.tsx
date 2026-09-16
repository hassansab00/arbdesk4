"use client";

import { useMemo } from "react";
import { supabase } from "@/lib/supabase";
import { useQuery } from "@/lib/useQuery";
import { DataState } from "@/components/DataState";

/**
 * WAS THE DESK RIGHT? - which the platform could not answer at all.
 *
 * "What the desk expects" showed a prediction for every open day and then said
 * nothing, ever, about how those predictions turned out. Not by choice: the
 * ladder took its outcome from markets.settled_value and markets.winning_
 * band_id, and nothing has ever written either column - the only writer,
 * scripts/settlement.py, is in no workflow. 1,217 closed markets, zero settled
 * values. The answer was being recorded the whole time in the venue proofs and
 * in fact_band_outcome, one join away.
 *
 * This panel is the other half of the sentence. Same modal band, but scored.
 *
 * THE HEADLINE IS THE CALIBRATION GAP, not the hit rate. A hit rate alone
 * cannot be judged - 23% sounds poor until you know the desk only claimed 31%,
 * and eleven buckets means blind guessing scores 9%. What matters is whether
 * the stated confidence matches the realised frequency, and the difference
 * between those two numbers is the one thing a forecaster can actually fix.
 *
 * The interval is shown because 189 days is not many. A gap smaller than it is
 * not yet evidence of anything, and presenting one as though it were is how a
 * desk talks itself into a correction it does not need.
 */

type Row = {
  city_key: string; for_date: string;
  predicted_band: string | null; predicted_pct: number | null;
  actual_band: string | null; observed_max_c: number | null;
  forecast_max_c: number | null; forecast_error_c: number | null;
  hit: boolean | null; stated_sigma_c: number | null;
  regime_label: string | null; outcome_source: string | null;
};

const n = (v: unknown) => {
  const x = typeof v === "number" ? v : parseFloat(String(v ?? ""));
  return Number.isFinite(x) ? x : null;
};

export function summarise(rows: Row[]) {
  const scored = rows.filter(r => r.hit !== null && n(r.predicted_pct) !== null);
  const days = scored.length;
  const hits = scored.filter(r => r.hit).length;
  if (!days) return null;
  const actual = hits / days;
  const claimed = scored.reduce((s, r) => s + (n(r.predicted_pct) ?? 0), 0) / days / 100;
  // Two standard errors on the realised rate. 189 days is not many, and a gap
  // inside this band is not yet evidence of miscalibration.
  const ci = 2 * Math.sqrt((actual * (1 - actual)) / days);
  const errs = scored.map(r => n(r.forecast_error_c)).filter((x): x is number => x !== null);
  return {
    days, hits, actual, claimed, ci,
    gap: actual - claimed,
    significant: Math.abs(actual - claimed) > ci,
    mae: errs.length ? errs.reduce((s, e) => s + Math.abs(e), 0) / errs.length : null,
    bias: errs.length ? errs.reduce((s, e) => s + e, 0) / errs.length : null,
  };
}

const pct = (x: number) => `${(x * 100).toFixed(1)}%`;

export default function PredictionHindsight() {
  const q = useQuery<Row[]>(
    () => supabase.from("v_prediction_hindsight").select("*").limit(2000),
    [], 60000, 2000);

  const rows = useMemo(() => q.data ?? [], [q.data]);
  const s = useMemo(() => summarise(rows), [rows]);

  return <section className="space-y-2">
    <h2 className="text-sm font-semibold">Was it right?</h2>
    <p className="max-w-3xl text-xs leading-relaxed text-muted">
      The same modal bucket as above, for days that have since settled, against the bucket that
      actually paid. <strong>The number that matters is the gap</strong> between what the desk
      claimed and what happened — a hit rate on its own says nothing, since eleven buckets means
      blind guessing scores about 9%.
    </p>
    <DataState
      relation="v_prediction_hindsight"
      truncated={q.truncated}
      loading={q.loading} error={q.error} isEmpty={!rows.length}
      emptyTitle="Nothing has settled yet"
      emptyBody={<>A day appears here once the venue confirms its bands or the weather outcome is
        banked into <code className="rounded bg-panel2 px-1">fact_band_outcome</code>, which the
        daily pipeline does after the day ends.</>}
      onRetry={q.refresh}
    >
      {s && <div className="grid gap-3 rounded border border-border bg-panel p-4 sm:grid-cols-2 lg:grid-cols-5">
        <div>
          <div className="text-xs text-muted">Top pick was right</div>
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
        <div title="Average absolute error of the raw forecast on the day it settled.">
          <div className="text-xs text-muted">Forecast error</div>
          <div className="font-mono text-xl">{s.mae === null ? "—" : `${s.mae.toFixed(2)}°C`}</div>
          <div className="text-xs text-muted">mean absolute</div>
        </div>
        <div title="Positive means the day came out warmer than the raw forecast said. The engine already subtracts a measured bias before pricing; this is the uncorrected figure.">
          <div className="text-xs text-muted">Raw bias</div>
          <div className="font-mono text-xl">
            {s.bias === null ? "—" : `${s.bias > 0 ? "+" : ""}${s.bias.toFixed(2)}°C`}
          </div>
          <div className="text-xs text-muted">{s.bias === null ? "" : s.bias > 0 ? "runs cold" : "runs warm"}</div>
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
            {["Day", "City", "Called", "Sure", "Actual", "Observed", "Forecast", "Error", "", "Source"]
              .map((h, i) => <th key={i} className="px-2 py-1.5 text-left font-normal">{h}</th>)}
          </tr></thead>
          <tbody>
            {rows.slice(0, 300).map(r => <tr key={`${r.city_key}-${r.for_date}`} className="border-t border-border">
              <td className="whitespace-nowrap px-2 py-1">{r.for_date}</td>
              <td className="px-2 py-1">{r.city_key}</td>
              <td className="px-2 py-1">{r.predicted_band ?? "—"}</td>
              <td className="px-2 py-1 font-mono">{r.predicted_pct === null ? "—" : `${r.predicted_pct}%`}</td>
              <td className="px-2 py-1">{r.actual_band ?? "—"}</td>
              <td className="px-2 py-1 font-mono">{r.observed_max_c === null ? "—" : `${r.observed_max_c}°C`}</td>
              <td className="px-2 py-1 font-mono text-muted">{r.forecast_max_c === null ? "—" : `${r.forecast_max_c}°C`}</td>
              <td className={`px-2 py-1 font-mono ${Math.abs(n(r.forecast_error_c) ?? 0) > 2 ? "text-warn" : "text-muted"}`}>
                {r.forecast_error_c === null ? "—" : `${n(r.forecast_error_c)! > 0 ? "+" : ""}${r.forecast_error_c}`}
              </td>
              <td className={`px-2 py-1 ${r.hit ? "text-good" : "text-bad"}`}>{r.hit ? "hit" : "miss"}</td>
              <td className="px-2 py-1 text-muted" title={r.outcome_source === "venue"
                ? "The venue confirmed which contract paid."
                : "Banked from the weather outcome; the venue has not confirmed this band."}>
                {r.outcome_source ?? "—"}
              </td>
            </tr>)}
          </tbody>
        </table>
      </div>
      {rows.length > 300 && <p className="text-xs text-muted">
        Showing the 300 most recent of {rows.length} settled city-days; the figures above use all of them.
      </p>}
    </DataState>
  </section>;
}
