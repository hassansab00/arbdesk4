"use client";

import { useQuery } from "../lib/useQuery";
import { supabase } from "../lib/supabase";
import { settlementCoverage, type SettlementMarket, type SettlementCity, type SettlementCoverage,
  type SettlementState } from "../lib/predictive";
import { fmtResolutionDate } from "../lib/time";
import { DataState } from "./DataState";

const LABEL: Record<SettlementState, string> = {
  recorded: "Result recorded",
  awaiting_score: "Venue confirmed; result not recorded",
  awaiting_venue: "Awaiting venue settlement",
  day_not_ended: "Local day still in progress",
  clock_unavailable: "City timezone unavailable",
};

export function SettlementCoverageTable({ rows, selected }: { rows: SettlementCoverage[]; selected: number }) {
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
          <td className="px-3 py-2 font-mono">{r.recorded} / {r.tracked}</td>
          <td className="px-3 py-2 font-mono">{r.awaiting_score}</td>
          <td className="px-3 py-2 font-mono">{r.awaiting_venue}</td>
          <td className="px-3 py-2 font-mono">{r.day_not_ended}</td>
        </tr>)}</tbody>
      </table>
    </div>
    {rows.map(r => <details key={r.date} className="rounded border border-border bg-panel px-3 py-2 text-xs">
      <summary className="cursor-pointer">{fmtResolutionDate(r.date)} · status for every city</summary>
      {r.tracked < selected && <p className="mt-2 text-warn">Market dates are missing for {selected - r.tracked} selected cities.</p>}
      {r.clock_unavailable > 0 && <p className="mt-2 text-warn">{r.clock_unavailable} cities need a valid timezone.</p>}
      <ul className="mt-2 grid gap-1 sm:grid-cols-2 lg:grid-cols-3">
        {r.details.map(d => <li key={d.city_key}><span className="font-medium">{d.name}</span>
          <span className="text-muted"> · {LABEL[d.state]}</span></li>)}
      </ul>
    </details>)}
  </div>;
}

export default function SettlementStatus({ cities, calls, moment }: {
  cities: SettlementCity[]; calls: Array<{ city_key: string; for_date: string; called_when: string }>; moment: string;
}) {
  const keys = cities.map(c => c.city_key);
  const query = useQuery<SettlementMarket[]>(() => keys.length
    ? supabase.from("markets").select("city_key,resolution_date,resolution_verified_at")
      .in("city_key", keys)
      .gte("resolution_date", new Date(Date.now() - 3 * 86400000).toISOString().slice(0, 10))
      .lte("resolution_date", new Date().toISOString().slice(0, 10))
      .order("resolution_date", { ascending: false }).order("city_key").order("market_id").limit(500)
    : Promise.resolve({ data: [], error: null }), [keys.join("|")], 60000, 500);
  const rows = settlementCoverage(query.data ?? [], cities, calls, moment, Date.now());
  return <section className="space-y-2 rounded border border-border bg-panel p-4">
    <h2 className="text-sm font-semibold">Recent settlement dates</h2>
    <p className="max-w-4xl text-xs text-muted">Coverage for the selected cities and prediction time.
      A final weather reading and a venue settlement can arrive at different times. Accuracy totals use recorded results;
      pending dates stay visible here. Each city uses its own local calendar.</p>
    <DataState relation="markets" loading={query.loading} error={query.error} truncated={query.truncated}
      isEmpty={!rows.length} emptyTitle="No recent market dates for this selection"
      emptyBody="Select active cities or refresh their market dates." onRetry={query.refresh}>
      <SettlementCoverageTable rows={rows} selected={cities.length} />
    </DataState>
  </section>;
}
