"use client";

import { useMemo, useState } from "react";
import { supabase } from "@/lib/supabase";
import { DataState } from "@/components/DataState";
import { useQuery } from "@/lib/useQuery";
import { fmtAge, fmtInt } from "@/lib/format";

interface ReadinessRow {
  city_key: string;
  display_name: string;
  resolution_date: string;
  market_count: number;
  band_count: number;
  fresh_forecast_models: number;
  fresh_book_bands: number;
  executable_book_bands: number;
  fresh_probability_bands: number;
  fresh_edge_bands: number;
  fresh_tradeable_edge_bands: number;
  execution_state: "ready" | "attention" | "blocked" | "no_market";
  execution_issues: string[] | null;
  pricing_eligible_bands: number;
  cold_start_bands: number;
  forecast_at: string | null;
  book_at: string | null;
  probability_at: string | null;
  edge_at: string | null;
}

interface OperationalHealth {
  target_city_days: number;
  ready: number;
  attention: number;
  blocked: number;
  no_market: number;
  verdict: string;
}

interface BookCaptureHealth {
  resolution_date: string;
  capture_state: string;
  bands: number;
  cities: number;
  latest_at: string | null;
}

interface MetadataHealth {
  cities: number;
  coordinates_verified: number;
  coordinates_to_verify: number;
  coordinates_missing: number;
  timezones_verified: number;
  timezones_to_verify: number;
  timezones_missing: number;
}

const stateTone: Record<ReadinessRow["execution_state"], string> = {
  ready: "text-good",
  attention: "text-warn",
  blocked: "text-bad",
  no_market: "text-muted",
};

export default function CityReadiness() {
  const [chosenDate, setChosenDate] = useState<string | null>(null);
  const healthQ = useQuery<OperationalHealth[]>(
    () => supabase.from("v_execution_health").select("target_city_days,ready,attention,blocked,no_market,verdict"),
    [],
    60000
  );
  const rowsQ = useQuery<ReadinessRow[]>(
    () =>
      supabase
        .from("v_city_day_execution_readiness")
        .select(
          "city_key,display_name,resolution_date,market_count,band_count,fresh_forecast_models,fresh_book_bands,executable_book_bands,fresh_probability_bands,fresh_edge_bands,fresh_tradeable_edge_bands,pricing_eligible_bands,cold_start_bands,execution_state,execution_issues,forecast_at,book_at,probability_at,edge_at"
        )
        .order("resolution_date")
        .order("display_name"),
    [],
    60000,
    500
  );
  const booksQ = useQuery<BookCaptureHealth[]>(
    () => supabase.from("v_book_capture_health").select("resolution_date,capture_state,bands,cities,latest_at").order("resolution_date"),
    [],
    60000
  );
  const metadataQ = useQuery<MetadataHealth[]>(
    () => supabase.from("v_city_metadata_health").select("*"),
    [],
    300000
  );

  const dates = useMemo(
    () => Array.from(new Set((rowsQ.data ?? []).map((r) => r.resolution_date))).sort(),
    [rowsQ.data]
  );
  const activeDate = chosenDate && dates.includes(chosenDate) ? chosenDate : dates[0] ?? null;
  const rows = useMemo(
    () => (rowsQ.data ?? []).filter((r) => r.resolution_date === activeDate),
    [rowsQ.data, activeDate]
  );
  const health = healthQ.data?.[0];
  const bookStates = (booksQ.data ?? []).filter((r) => r.resolution_date === activeDate);
  const metadata = metadataQ.data?.[0];

  function refresh() {
    healthQ.refresh();
    rowsQ.refresh();
    booksQ.refresh();
    metadataQ.refresh();
  }

  return (
    <section className="space-y-3 rounded border border-border bg-panel p-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold">City readiness</h2>
          <p className="mt-0.5 max-w-3xl text-[11px] leading-relaxed text-muted">
            Current evidence needed to evaluate each city-day. A fresh evaluation with no tradeable
            edge is valid; missing or stale inputs are shown as blockers.
          </p>
        </div>
        <div className="flex gap-1">
          {dates.map((date, index) => (
            <button
              key={date}
              onClick={() => setChosenDate(date)}
              className={`rounded border px-2 py-1 text-[11px] ${
                activeDate === date ? "border-accent text-accent" : "border-border text-muted hover:bg-panel2"
              }`}
            >
              {index === 0 ? "Today" : index === 1 ? "Tomorrow" : date}
            </button>
          ))}
          <button onClick={refresh} className="rounded border border-border px-2 py-1 text-[11px] text-muted hover:bg-panel2">
            Refresh
          </button>
        </div>
      </div>

      <DataState
        loading={healthQ.loading || rowsQ.loading}
        error={healthQ.error ?? rowsQ.error}
        isEmpty={!rowsQ.loading && rows.length === 0}
        emptyTitle="No active cities"
        emptyBody="The readiness view returned no active city rows for this date."
        onRetry={refresh}
        relation="markets"
        truncated={rowsQ.truncated}
      >
        <>
          {health && (
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-5">
              <Summary label="Ready" value={health.ready} tone="text-good" />
              <Summary label="Attention" value={health.attention} tone="text-warn" />
              <Summary label="Blocked" value={health.blocked} tone="text-bad" />
              <Summary label="No market" value={health.no_market} tone="text-muted" />
              <div className="rounded border border-border bg-panel2 px-2 py-1.5 sm:col-span-1">
                <div className="text-[10px] uppercase tracking-wide text-muted">Desk verdict</div>
                <div className={`mt-0.5 text-xs ${health.blocked ? "text-bad" : health.attention ? "text-warn" : "text-good"}`}>
                  {health.verdict}
                </div>
              </div>
            </div>
          )}

          {(bookStates.length > 0 || metadata) && (
            <div className="flex flex-wrap gap-x-4 gap-y-1 rounded border border-border bg-panel2 px-2 py-1.5 text-[10px] text-muted">
              {bookStates.length > 0 && (
                <span>
                  Book targets: {bookStates.map((r) => `${r.capture_state} ${fmtInt(r.bands)}`).join(" · ")}
                </span>
              )}
              {metadata && (
                <span>
                  Metadata review: {fmtInt(metadata.coordinates_to_verify)} coordinate set(s),{" "}
                  {fmtInt(metadata.timezones_to_verify)} timezone(s) awaiting authority evidence
                  {(metadata.coordinates_missing || metadata.timezones_missing) ?
                    ` · ${fmtInt(metadata.coordinates_missing + metadata.timezones_missing)} missing` : ""}
                </span>
              )}
            </div>
          )}

          <div className="max-h-[32rem] overflow-auto rounded border border-border">
            <table className="w-full text-xs">
              <thead className="sticky top-0 bg-panel2 text-muted">
                <tr>
                  <th className="p-2 text-left">City</th>
                  <th className="p-2 text-left">State</th>
                  <th className="p-2 text-right">Contracts</th>
                  <th className="p-2 text-right">Forecasts</th>
                  <th className="p-2 text-right">Books</th>
                  <th className="p-2 text-right">Probabilities</th>
                  <th className="p-2 text-right">Edge check</th>
                  <th className="p-2 text-left">Reason</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={`${r.city_key}:${r.resolution_date}`} className="border-t border-border align-top">
                    <td className="p-2 font-medium">{r.display_name}</td>
                    <td className={`p-2 font-medium ${stateTone[r.execution_state]}`}>
                      {r.execution_state.replace("_", " ")}
                    </td>
                    <td className="p-2 text-right font-mono">{fmtInt(r.band_count)}</td>
                    <td className="p-2 text-right font-mono" title={r.forecast_at ?? "No forecast"}>
                      {fmtInt(r.fresh_forecast_models)} model{r.fresh_forecast_models === 1 ? "" : "s"}
                      <Age value={r.forecast_at} />
                    </td>
                    <td className="p-2 text-right font-mono" title={r.book_at ?? "No book"}>
                      {r.fresh_book_bands}/{r.band_count}
                      <div className="text-[10px] text-muted">{r.executable_book_bands} executable</div>
                    </td>
                    <td className="p-2 text-right font-mono" title={r.probability_at ?? "No probability"}>
                      {r.fresh_probability_bands}/{r.band_count}
                      {r.cold_start_bands > 0 && (
                        <div className="text-[10px] text-warn">{r.cold_start_bands} cold-start</div>
                      )}
                      <Age value={r.probability_at} />
                    </td>
                    <td className="p-2 text-right font-mono" title={r.edge_at ?? "No edge evaluation"}>
                      {r.fresh_edge_bands}/{r.band_count}
                      <div className="text-[10px] text-muted">{r.fresh_tradeable_edge_bands} tradeable</div>
                    </td>
                    <td className="max-w-sm p-2 text-[11px] leading-relaxed text-muted">
                      {r.execution_issues?.length ? r.execution_issues.join("; ") : "All required evidence is current."}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      </DataState>
    </section>
  );
}

function Summary({ label, value, tone }: { label: string; value: number; tone: string }) {
  return (
    <div className="rounded border border-border bg-panel2 px-2 py-1.5">
      <div className="text-[10px] uppercase tracking-wide text-muted">{label}</div>
      <div className={`font-mono text-base ${tone}`}>{fmtInt(value)}</div>
    </div>
  );
}

function Age({ value }: { value: string | null }) {
  return <div className="text-[10px] text-muted">{fmtAge(value)}</div>;
}
