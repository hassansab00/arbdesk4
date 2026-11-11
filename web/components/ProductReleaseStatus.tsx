"use client";

import { supabase } from "@/lib/supabase";
import { useQuery } from "@/lib/useQuery";

type Release = { city_key: string; checkpoint: string; kind: string; receipt_id: number;
  release_id: string; status: string; recorded_at: string; expires_at: string };

export default function ProductReleaseStatus() {
  const q = useQuery<Release[]>(() => supabase.rpc("predictive_product_status"), [], 60000);
  return <div className="rounded border border-border bg-panel p-3 text-xs">
    <div className="font-semibold">Validated model releases</div>
    {q.error ? <p className="mt-1 text-warn">Release status unavailable. This does not establish which model priced a card; inspect its recorded source.</p>
      : !q.data ? <p className="mt-1 text-muted">Checking production release receipts…</p>
      : !q.data.length ? <p className="mt-1 text-muted">No approved release for the new seven-source or current-weather models. Incumbent city predictions continue. Shadow learning is not an applied improvement.</p>
      : <><p className="mt-1 text-muted">Approval permits serving only when the exact city, station, horizon and fresh original inputs match. Each card names its actual source; approval alone does not establish that a release was applied.</p>
        <div className="mt-2 overflow-x-auto"><table className="w-full text-left"><thead><tr><th>City</th><th>Checkpoint</th><th>Release</th><th>Status</th></tr></thead>
          <tbody>{q.data.map(r => <tr key={r.receipt_id}><td>{r.city_key}</td><td>{r.checkpoint}</td><td title={r.release_id}>{r.kind} · {r.release_id.slice(0, 10)}</td><td>{r.status}</td></tr>)}</tbody></table></div></>}
  </div>;
}
