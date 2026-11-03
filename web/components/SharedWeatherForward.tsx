"use client";
import { useQuery } from "@/lib/useQuery";
interface Call {
  city_key: string; target_date: string; checkpoint: string; decided_at: string; frozen_at: string;
  top_probability: number; hit: boolean | null;
  context: { display_name: string; timezone: string; station: string; unit: string;
    candidate_label: string; incumbent_label: string };
}
interface Score {
  checkpoint: string; calls: number; settled: number; settled_dates: number;
  candidate_hits: number; incumbent_hits: number; market_pairs: number;
  candidate_market_hits: number; market_hits: number;
}
interface Snapshot {
  state: string; model?: { sha256: string; registered_at: string;
    training: { through: string; dates: number; rows: number };
    evaluation_first: string; evaluation_last: string; cohort: string[] };
  calls: Call[]; scores: Score[];
  status?: { updated_at: string; detail: { state?: string; error?: string; due?: number; new?: number; skipped?: Record<string, number> } };
}
const slot = (value: string) => value === "d1_eve" ? "Evening before" : "Morning";
const rate = (hits: number, n: number) => n ? `${hits}/${n} (${(100 * hits / n).toFixed(1)}%)` : "Pending outcomes";
export default function SharedWeatherForward() {
  const q = useQuery<Snapshot>(async () => {
    const response = await fetch("/api/shared-weather");
    const data = await response.json();
    return response.ok ? { data, error: null } : { data: null, error: { message: data.error } };
  }, [], 120_000);
  const data = q.data, model = data?.model;
  return <section id="predictive-shared-weather" tabIndex={-1} className="scroll-mt-20 space-y-3 rounded-lg border border-border bg-panel p-4">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <h2 className="text-sm font-semibold">Shared weather model · forward test</h2>
      <button className="text-xs text-accent" onClick={q.refresh}>Refresh</button>
    </div>
    <p className="text-xs text-muted">One model combines forecast state and received hourly weather across all cities. These test calls freeze separately. The main city cards and trading engine still use their recorded production model. Historical research scores do not enter this test.</p>
    {q.error ? <p role="alert" className="text-xs text-warn">{q.error}</p>
      : q.loading && !data ? <p className="text-xs text-muted">Loading frozen calls…</p>
      : !model ? <p className="text-xs text-muted">No model registered yet. No forward accuracy is claimed.</p>
      : <>
        <p className="text-xs text-muted">Trained through {model.training.through} on {model.training.rows.toLocaleString()} original calls across {model.training.dates} dates. Registered {new Date(model.registered_at).toLocaleString()}. Version {model.sha256.slice(0, 12)}.</p>
        <p className="text-xs text-muted">Fixed evaluation: {model.evaluation_first}–{model.evaluation_last}, {model.cohort.length} cities. Earlier live calls appear below but do not enter evaluation. Morning calls require at least one hour before both the saved seasonal peak reference and the received forecast’s expected peak. These references do not prove that the actual maximum is still ahead. Skipped and pending calls do not count as hits.</p>
        <div className="overflow-x-auto"><table className="w-full text-left text-xs">
          <thead className="text-muted"><tr><th>Entry · city clock</th><th>Captured / planned</th><th>Settled</th><th>New model</th><th>Production model</th><th>Matched market</th></tr></thead>
          <tbody>{["d1_eve", "morning"].map(checkpoint => {
            const s = data.scores.find(x => x.checkpoint === checkpoint);
            return <tr key={checkpoint} className="border-t border-border"><td className="py-2">{slot(checkpoint)}</td>
              <td>{s?.calls ?? 0} / {model.cohort.length * 20}</td><td>{s?.settled ?? 0} · {s?.settled_dates ?? 0} dates</td>
              <td>{rate(s?.candidate_hits ?? 0, s?.settled ?? 0)}</td><td>{rate(s?.incumbent_hits ?? 0, s?.settled ?? 0)}</td>
              <td>{s?.market_pairs ? `New ${rate(s.candidate_market_hits, s.market_pairs)} · Market ${rate(s.market_hits, s.market_pairs)}` : "No settled matched pairs"}</td></tr>;
          })}</tbody>
        </table></div>
        {data.status && <p className="text-xs text-muted">Last collection {new Date(data.status.updated_at).toLocaleString()}: {data.status.detail.new ?? 0} new of {data.status.detail.due ?? 0} due. {Object.entries(data.status.detail.skipped ?? {}).map(([reason, count]) => `${reason.replaceAll("_", " ")}: ${count}`).join("; ")}</p>}
        {data.status?.detail.error && <p role="alert" className="text-xs text-warn">Collection failed: {data.status.detail.error}</p>}
        {data.status && Date.now() - new Date(data.status.updated_at).valueOf() > 2 * 3600_000 && <p role="alert" className="text-xs text-warn">Collection is overdue. The saved calls remain frozen.</p>}
        {!data.calls.length ? <p className="text-xs text-muted">Waiting for the next eligible evening-before or morning call after registration. No past prediction will be inserted.</p> : <div className="overflow-x-auto">
          <p className="mb-2 text-xs text-muted">Latest {data.calls.length} frozen calls. Entry times use each city’s timezone. Pending outcomes do not count as misses.</p>
          <table className="w-full text-left text-xs"><thead className="text-muted"><tr><th>City · station</th><th>Target day</th><th>Entry</th><th>Issued · local time</th><th>New forecast</th><th>Production forecast</th><th>Result</th></tr></thead>
            <tbody>{data.calls.map(c => <tr className="border-t border-border" key={`${c.city_key}|${c.target_date}|${c.checkpoint}`}>
              <td className="py-2">{c.context.display_name} · {c.context.station}</td><td>{c.target_date}</td><td>{slot(c.checkpoint)}</td>
              <td title={`Server freeze: ${c.frozen_at}`}>{new Date(c.decided_at).toLocaleString(undefined, { timeZone: c.context.timezone })} · {c.context.timezone}</td>
              <td>{c.context.candidate_label} · {(Number(c.top_probability) * 100).toFixed(1)}%</td><td>{c.context.incumbent_label}</td>
              <td>{c.hit == null ? "Pending" : c.hit ? "Hit" : "Miss"}</td>
            </tr>)}</tbody></table>
        </div>}
      </>}
  </section>;
}
