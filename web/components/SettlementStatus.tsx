"use client";

import { useQuery } from "../lib/useQuery";
import { supabase } from "../lib/supabase";
import { settlementCoverage, type SettlementMarket, type SettlementCity, type SettlementCoverage,
  type SettlementState, type SettlementResult } from "../lib/predictive";
import { fmtResolutionDate } from "../lib/time";
import { DataState } from "./DataState";

const LABEL: Record<SettlementState, string> = {
  recorded: "Result recorded",
  awaiting_score: "Venue confirmed; result not recorded",
  awaiting_venue: "Awaiting venue settlement",
  day_not_ended: "Local day still in progress",
  clock_unavailable: "City timezone unavailable",
  record_unavailable: "Venue confirmed; record status not loaded",
};

export function SettlementCoverageTable({ rows, selected, recordsReady = true }: {
  rows: SettlementCoverage[]; selected: number; recordsReady?: boolean;
}) {
  const latestRecordedDate = rows.find(r => r.recorded > 0)?.date;
  return <div className="space-y-2">
    <div className="overflow-x-auto rounded border border-border">
      <table className="w-full text-left text-xs">
        <thead className="bg-panel2 text-muted"><tr>
          <th className="px-3 py-2">City-local date</th><th className="px-3 py-2">Results recorded</th>
          <th className="px-3 py-2">Venue confirmed, awaiting record</th>
          <th className="px-3 py-2">Awaiting venue</th><th className="px-3 py-2">Local day ongoing</th>
        </tr></thead>
        <tbody>{rows.map(r => <tr key={r.date} className="border-t border-border">
          <td className="px-3 py-2 font-medium">{fmtResolutionDate(r.date)}</td>
          <td className="px-3 py-2 font-mono">{recordsReady ? `${r.recorded} / ${r.tracked}` : "—"}</td>
          <td className="px-3 py-2 font-mono">{recordsReady ? r.awaiting_score : "—"}</td>
          <td className="px-3 py-2 font-mono">{r.awaiting_venue}</td>
          <td className="px-3 py-2 font-mono">{r.day_not_ended}</td>
        </tr>)}</tbody>
      </table>
    </div>
    {rows.map(r => <details key={r.date} open={r.date === latestRecordedDate}
      className="rounded border border-border bg-panel px-3 py-2 text-xs">
      <summary className="cursor-pointer">{fmtResolutionDate(r.date)} · status for every city</summary>
      {r.tracked < selected && <p className="mt-2 text-warn">Market dates are missing for {selected - r.tracked} selected cities.</p>}
      {r.clock_unavailable > 0 && <p className="mt-2 text-warn">{r.clock_unavailable} cities need a valid timezone.</p>}
      <ul className="mt-2 grid gap-1 sm:grid-cols-2 lg:grid-cols-3">
        {r.details.map(d => <li key={d.city_key}><span className="font-medium">{d.name}</span>
          <span className="text-muted"> · {LABEL[d.state]}</span>
          {d.actual_band && <strong> · {d.actual_band}</strong>}
          {d.event_slug && <a className="ml-2 text-accent underline" target="_blank" rel="noreferrer"
            href={`https://polymarket.com/event/${encodeURIComponent(d.event_slug)}`}>Polymarket</a>}
        </li>)}
      </ul>
    </details>)}
  </div>;
}

export default function SettlementStatus({ cities, moment }: {
  cities: SettlementCity[]; moment: string;
}) {
  const keys = cities.map(c => c.city_key);
  const since = new Date(Date.now() - 3 * 86400000).toISOString().slice(0, 10);
  const until = new Date().toISOString().slice(0, 10);
  const query = useQuery<SettlementMarket[]>(() => keys.length
    ? supabase.from("markets").select("city_key,resolution_date,resolution_verified_at,event_slug")
      .in("city_key", keys)
      .gte("resolution_date", since).lte("resolution_date", until)
      .order("resolution_date", { ascending: false }).order("city_key").order("market_id").limit(500)
    : Promise.resolve({ data: [], error: null }), [keys.join("|"), since, until], 60000, 500);
  // Recent settlement availability must not wait for every historical prediction
  // page, or disappear when an unrelated older page fails. Read result metadata
  // only, bounded to this selection, moment and date window.
  const results = useQuery<SettlementResult[]>(() => keys.length
    ? supabase.from("v_prediction_hindsight").select("city_key,for_date,called_when,actual_band")
      .in("city_key", keys).eq("called_when", moment)
      .not("actual_band", "is", null)
      .gte("for_date", since).lte("for_date", until)
      .order("for_date", { ascending: false }).order("city_key").limit(500)
    : Promise.resolve({ data: [], error: null }), [keys.join("|"), moment, since, until], 60000, 500);
  const recordsReady = !results.loading && !results.error && !results.truncated;
  const rows = settlementCoverage(query.data ?? [], cities, results.data ?? [], moment, Date.now(), recordsReady);
  return <section className="space-y-2 rounded border border-border bg-panel p-4">
    <h2 className="text-sm font-semibold">Recent settlement dates</h2>
    <p className="max-w-4xl text-xs text-muted">Coverage for the selected cities and prediction time.
      A final weather reading and a venue settlement can arrive at different times. Accuracy totals use recorded results;
      pending dates stay visible here. Each city uses its own local calendar.</p>
    {!recordsReady && <DataState relation="v_prediction_hindsight" loading={results.loading}
      error={results.error} truncated={results.truncated} isEmpty={false}
      emptyTitle="Recent recorded results unavailable" emptyBody="Retry the recent result check."
      onRetry={results.refresh}>
      <p className="text-xs text-warn">Recorded-result counts are unavailable until the recent result check completes.</p>
    </DataState>}
    <DataState relation="markets" loading={query.loading} error={query.error} truncated={query.truncated}
      isEmpty={!rows.length} emptyTitle="No recent market dates for this selection"
      emptyBody="Select active cities or refresh their market dates." onRetry={query.refresh}>
      <SettlementCoverageTable rows={rows} selected={cities.length} recordsReady={recordsReady} />
    </DataState>
  </section>;
}
