"use client";

import { useEffect, useState } from "react";
import { supabase } from "@/lib/supabase";
import { useQuery } from "@/lib/useQuery";
import { DataState } from "@/components/DataState";
import { displayTz, fmtCityHour, fmtDateTime } from "@/lib/time";

type City = { city_key: string; display_name: string | null; timezone: string | null };
type Peak = { city_key: string; peak_hour_local: number | null; window_width_h: number | null;
  n_days: number | null; computed_at: string | null };

export default function PredictionClocks({ cities }: { cities: City[] }) {
  const [viewerZone, setViewerZone] = useState("UTC");
  useEffect(() => setViewerZone(displayTz()), []);
  const q = useQuery<Peak[]>(() => supabase.from("v_peak_hour_coverage")
    .select("city_key,peak_hour_local,window_width_h,n_days,computed_at").order("city_key"), [], 300000, 1000);
  return <section className="space-y-2 rounded border border-border bg-panel p-4">
    <h2 className="text-sm font-semibold">Prediction clocks · each city’s local time</h2>
    <p className="max-w-4xl text-xs leading-relaxed text-muted">
      Filters use the <strong>city’s timezone</strong>. Day ahead means the last pricing before its
      midnight; evening before is 18:00 on the previous local day; morning is 09:00; noon is 12:00.
      Peak-relative checkpoints use that city’s measured <strong>typical peak for the month</strong>.
      Noon can therefore coincide with, or follow, a city’s typical peak.
      These are scheduled times: the hourly worker captures the call afterwards, with up to 75 minutes allowed.
      A “1 h before” schedule does not guarantee a full hour of lead at the actual freeze.
    </p>
    <p className="max-w-4xl text-xs text-muted">
      The typical peak is a timing reference, not a claim that today’s maximum is final.
      The live model uses the reports and forecast curve. A later rise remains possible;
      final settlement requires venue confirmation. Your display clock: {viewerZone}.
    </p>
    <details>
      <summary className="cursor-pointer text-xs text-accent">Show timing for {cities.length} selected cities</summary>
      <DataState relation="v_peak_hour_coverage" loading={q.loading} error={q.error} truncated={q.truncated}
        isEmpty={!q.data?.length} emptyTitle="Peak timing unavailable"
        emptyBody="Peak-relative calls require a measured monthly peak." onRetry={q.refresh}>
        <div className="mt-2 overflow-x-auto">
          <table className="w-full whitespace-nowrap text-xs">
            <thead className="text-muted"><tr>
              {["City / timezone", "Typical peak", "2 h before · scheduled", "1 h before · scheduled", "1 h after · scheduled", "12:00 local vs peak", "Evidence"].map(h =>
                <th key={h} className="px-2 py-1 text-left font-normal">{h}</th>)}
            </tr></thead>
            <tbody>{cities.map(c => {
              const p = q.data?.find(p => p.city_key === c.city_key);
              const peak = p?.peak_hour_local == null ? null : Number(p.peak_hour_local);
              return <tr key={c.city_key} className="border-t border-border">
                <td className="px-2 py-1">{c.display_name ?? c.city_key}<span className="block text-[10px] text-muted">{c.timezone ?? "timezone missing"}</span></td>
                {[0, -2, -1, 1].map(off => <td key={off} className="px-2 py-1 font-mono">
                  {peak === null ? "not measured" : fmtCityHour(peak + off, c.timezone, viewerZone)}</td>)}
                <td className={`px-2 py-1 ${peak !== null && peak <= 13.25 ? 'text-warn' : 'text-muted'}`}>
                  {peak === null ? 'Unknown' : peak <= 12 ? 'Scheduled at / after typical peak'
                    : peak <= 13.25 ? 'Capture window reaches typical peak' : 'Scheduled before typical peak'}
                </td>
                <td className="px-2 py-1 text-muted">{p?.n_days ?? 0} days · {p?.window_width_h ?? "—"} h spread
                  <span className="block text-[10px]">Updated {fmtDateTime(p?.computed_at)}</span></td>
              </tr>;
            })}</tbody>
          </table>
        </div>
      </DataState>
    </details>
  </section>;
}
