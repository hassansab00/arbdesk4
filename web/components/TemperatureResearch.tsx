"use client";
import { useQuery } from "@/lib/useQuery";
import { temperatureResearchPhase, type TemperatureResearchSummary } from "@/lib/temperatureResearch";

export default function TemperatureResearch() {
  const q = useQuery<TemperatureResearchSummary>(async () => {
    const response = await fetch('/api/temperature-research');
    const data = await response.json();
    return response.ok ? { data, error: null } : { data: null, error: { message: data.error } };
  }, [], 300_000);
  const data = q.data, model = data?.model, phase = data ? temperatureResearchPhase(data) : null;
  return <section id="predictive-temperature-research" tabIndex={-1} className="scroll-mt-20 space-y-3 rounded-lg border border-border bg-panel p-4">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <h2 className="text-sm font-semibold">Temperature model · research readiness</h2>
      <button className="text-xs text-accent" onClick={q.refresh}>Refresh</button>
    </div>
    <p className="text-xs text-muted">This separate study predicts maximum temperature and uncertainty across cities. City-card predictions continue using the current serving model. Status refreshes every five minutes while this page is visible.</p>
    {q.error ? <p role="alert" className="text-xs text-warn">{q.error}</p>
      : q.loading && !data ? <p className="text-xs text-muted">Loading research status…</p>
      : !model ? <p className="text-xs text-muted">No temperature reference is registered.</p>
      : data && <>
        <p className={`text-xs ${phase?.warning ? 'text-warn' : 'text-muted'}`} role={phase?.warning ? 'alert' : undefined}>{phase?.label}. {model.state === 'unfitted_reference' ? 'The active reference is unfitted; no learned accuracy is claimed.' : 'A fitted candidate is under a separate future test; registration does not prove improvement.'}</p>
        <div className="grid grid-cols-2 gap-3 text-xs md:grid-cols-4">
          <div><span className="text-muted">All research captures</span><div className="mt-1 font-semibold">{data.calls.toLocaleString()}</div></div>
          <div><span className="text-muted">All research scores</span><div className="mt-1 font-semibold">{data.scored.toLocaleString()}</div></div>
          <div><span className="text-muted">Qualifying training dates</span><div className="mt-1 font-semibold">{data.learning.dates} / 7 minimum</div></div>
          <div><span className="text-muted">Eligible training rows</span><div className="mt-1 font-semibold">{data.learning.rows.toLocaleString()} / 200 minimum</div></div>
        </div>
        <p className="text-xs text-muted">At least 20 cities on every qualifying training date are required; current minimum is {data.learning.cities_per_date}. Training eligibility does not establish forecast skill. A fitted candidate needs its own 20-date forward evaluation for each horizon.</p>
        <p className="text-xs text-muted">{model.state === 'unfitted_reference' ? 'Reference collection' : 'Candidate evaluation'}: {model.evaluation_first}–{model.evaluation_last} · {model.cities} cities · version {model.version}. Captures use each city’s clock: day before at 09:00 and 18:00, and target day at 09:00. Earlier warmups do not enter training.</p>
        {data.capture.updated_at && <p className="text-xs text-muted">Last capture report: {new Date(data.capture.updated_at).toLocaleString()} · {data.capture.new} new of {data.capture.due} due in that run.</p>}
        {!!data.capture.skipped.length && <p className="text-xs text-warn">Last-run exclusions: {data.capture.skipped.map(x => `${x.label}: ${x.count}`).join('; ')}.</p>}
        {data.learning.staged && <p className="text-xs text-muted">A fitted candidate is staged for review. It has not replaced the active study model.</p>}
      </>}
  </section>;
}
