"use client";

import { supabase } from "@/lib/supabase";
import { useQuery } from "@/lib/useQuery";
import { ErrorBox, Loading } from "@/components/DataState";

/**
 * TWO FORECAST CLOCKS, PER CITY (audit P3, 5 Oct 2026).
 *
 * The engine reads two kinds of forecast, fetched on different clocks:
 *
 *   main forecast   weather_forecasts, every 3 h for every city. What the
 *                   engine prices today from; each call records its age.
 *   model inputs    the seven named models' current run, fetched once a night.
 *                   They reach pricing through the station-corrected
 *                   combination (P3.9) and the MOS blend.
 *
 * Table-level freshness cannot tell them apart or see one city: the newest row
 * of a table is green while a single city missed its nightly run. And the
 * corrected combination is stamped when it was computed, not when its inputs
 * were fetched, so a city combined from the night before's run looked fresh.
 * v_city_forecast_inputs judges each clock on its own, per city.
 */

interface Row {
  city_key: string;
  display_name: string | null;
  local_date: string | null;
  main_model: string | null;
  main_age_h: number | null;
  main_verdict: "fresh" | "stale" | "absent";
  n_models: number | null;
  models_age_h: number | null;
  models_behind_h: number | null;
  models_verdict: "fresh" | "partial" | "behind" | "stale" | "absent";
  today_inputs_behind_h: number | null;
  today_inputs_verdict: Corrected;
  tomorrow_inputs_behind_h: number | null;
  tomorrow_inputs_verdict: Corrected;
}

type Corrected = "current" | "earlier_run" | "not_recorded" | "none";

const TONE: Record<string, string> = {
  fresh: "text-good",
  current: "text-good",
  behind: "text-warn",
  partial: "text-warn",
  earlier_run: "text-warn",
  stale: "text-bad",
  absent: "text-bad",
  not_recorded: "text-muted",
  none: "text-muted",
};

const LABEL: Record<string, string> = {
  fresh: "fresh",
  stale: "stale",
  absent: "none",
  behind: "missed last night",
  partial: "models missing",
  current: "tonight's runs",
  earlier_run: "an earlier run",
  not_recorded: "not recorded",
  none: "no row",
};

function hours(h: number | null): string {
  if (h === null || h === undefined) return "—";
  if (h < 1) return `${Math.max(1, Math.round(h * 60))} min`;
  if (h < 48) return `${Math.round(h)} h`;
  return `${Math.round(h / 24)} d`;
}

function corrected(v: Corrected, behind: number | null) {
  return (
    <span className={TONE[v]}>
      {LABEL[v]}
      {v === "earlier_run" && behind !== null && <span className="opacity-70"> · {hours(behind)} older</span>}
    </span>
  );
}

const BAD = { stale: 2, absent: 2, behind: 1, partial: 1, earlier_run: 1 } as Record<string, number>;

export default function CityForecastInputs() {
  const q = useQuery<Row[]>(() => supabase.from("v_city_forecast_inputs").select("*"), [], 120000);

  if (q.loading && !q.data) return <Loading compact />;
  if (q.error) return <ErrorBox message={q.error} onRetry={q.refresh} compact />;

  const rows = q.data ?? [];
  if (!rows.length) return null;

  const mainOk = rows.filter((r) => r.main_verdict === "fresh").length;
  const modelsOk = rows.filter((r) => r.models_verdict === "fresh").length;
  const earlier = rows.filter(
    (r) => r.today_inputs_verdict === "earlier_run" || r.tomorrow_inputs_verdict === "earlier_run"
  ).length;
  const score = (r: Row) =>
    (BAD[r.main_verdict] ?? 0) + (BAD[r.models_verdict] ?? 0)
    + (BAD[r.today_inputs_verdict] ?? 0) + (BAD[r.tomorrow_inputs_verdict] ?? 0);
  const sorted = rows.slice().sort(
    (a, b) => score(b) - score(a) || (a.display_name ?? a.city_key).localeCompare(b.display_name ?? b.city_key)
  );
  const worst = mainOk < rows.length || modelsOk < rows.length || earlier > 0;

  return (
    <details className="group mb-3 rounded border border-border bg-panel/60">
      <summary className="flex cursor-pointer list-none flex-wrap items-center gap-2 px-3 py-2 text-xs">
        <span className={`h-2 w-2 rounded-full ${worst ? "bg-warn" : "bg-good"}`} />
        <span className="text-text">
          Forecasts: main <b>{mainOk} of {rows.length}</b> fresh · model inputs{" "}
          <b>{modelsOk} of {rows.length}</b> fresh
        </span>
        {earlier > 0 && (
          <span className="text-warn">
            · {earlier} {earlier === 1 ? "city's" : "cities'"} corrected forecast combined from an earlier run
          </span>
        )}
        <span className="ml-auto text-[10px] text-muted group-open:hidden">show</span>
        <span className="ml-auto hidden text-[10px] text-muted group-open:inline">hide</span>
      </summary>

      <div className="border-t border-border px-3 py-2">
        <p className="mb-2 max-w-4xl text-[11px] leading-relaxed text-muted">
          Two clocks, judged separately. The <b>main forecast</b> is fetched every three hours and is
          what today&rsquo;s price starts from; it is stale past 8 hours. The <b>model inputs</b> are the
          seven named models&rsquo; current run, fetched once a night; they feed the station-corrected
          forecast. A city whose nightly run was missed is <i>missed last night</i> (more than 6 hours
          behind the newest any city has), stale past 36 hours; a run holding fewer than the seven
          models is <i>models missing</i> (under four, the corrected forecast combines nothing). The corrected columns say whether
          that city&rsquo;s combination used tonight&rsquo;s runs or an earlier one; rows computed
          before 6 Oct did not record it.
        </p>

        <table className="w-full text-left text-[11px]">
          <thead className="text-[10px] uppercase tracking-wide text-muted">
            <tr>
              <th className="py-1 pr-2">City</th>
              <th className="py-1 pr-2">Main forecast</th>
              <th className="py-1 pr-2">Model inputs</th>
              <th className="py-1 pr-2">Corrected, today</th>
              <th className="py-1">Corrected, tomorrow</th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((r) => (
              <tr key={r.city_key} className="border-t border-border/40 align-top">
                <td className="py-1 pr-2">
                  {r.display_name ?? r.city_key}
                  <span className="opacity-60"> · {r.local_date ?? "—"}</span>
                </td>
                <td className="py-1 pr-2 tabular-nums">
                  <span className={TONE[r.main_verdict]}>{LABEL[r.main_verdict]}</span>
                  <span className="text-muted"> · {hours(r.main_age_h)}</span>
                </td>
                <td className="py-1 pr-2 tabular-nums">
                  <span className={TONE[r.models_verdict]}>{LABEL[r.models_verdict]}</span>
                  <span className="text-muted">
                    {" "}
                    · {hours(r.models_age_h)}
                    {r.n_models !== null && ` · ${r.n_models} of 7 models`}
                  </span>
                </td>
                <td className="py-1 pr-2 tabular-nums">{corrected(r.today_inputs_verdict, r.today_inputs_behind_h)}</td>
                <td className="py-1 tabular-nums">{corrected(r.tomorrow_inputs_verdict, r.tomorrow_inputs_behind_h)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </details>
  );
}
