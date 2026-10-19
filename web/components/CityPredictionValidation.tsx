"use client";
import { useEffect, useState } from 'react';
import { supabase } from '@/lib/supabase';
import { useQuery } from '@/lib/useQuery';
import { DataState } from '@/components/DataState';
import { fmtDateTime } from '@/lib/time';
import { HORIZONS, CityReport, rankCities, recentCityScores, evidenceWords } from '@/lib/cityValidation';

type Row = { city_key: string; checkpoint: string; scope: string; as_of: string; report: CityReport };
const pct = (n: number | null | undefined) => n == null ? '—' : `${(100*n).toFixed(1)}%`;
const num = (n: number | null | undefined) => n == null ? '—' : n.toFixed(3);
export default function CityPredictionValidation({ cities, moment }: { cities: string[]; moment: string }) {
  const [horizon,setHorizon] = useState('day_ahead');
  const [scope,setScope] = useState('served');
  useEffect(()=>{ if(HORIZONS.some(([k])=>k===moment)) setHorizon(moment); },[moment]);
  const q = useQuery<Row[]>(()=>supabase.from('v_city_prediction_validation').select('*')
    .eq('checkpoint',horizon).eq('scope',scope).in('city_key',cities).limit(500),[horizon,scope,cities.join(',')],60000,500);
  const ranked = rankCities((q.data ?? []).map(r=>r.report));
  const recent = recentCityScores(ranked);
  const asOf = q.data?.map(r=>r.as_of).sort()[0];
  const overdue = asOf && Date.now()-Date.parse(asOf)>36*3600000;
  const total = ranked.reduce((a,r)=>({hits:a.hits+r.hits,n:a.n+r.n}),{hits:0,n:0});
  return <section className="space-y-3 rounded border border-border bg-panel p-4">
    <div className="flex flex-wrap items-center justify-between gap-3">
      <h2 className="text-sm font-semibold">City accuracy · original frozen calls</h2>
      <div className="flex flex-wrap gap-2 text-xs">
        <select aria-label="Accuracy horizon" className="rounded border border-border bg-panel p-1" value={horizon} onChange={e=>setHorizon(e.target.value)}>
          {HORIZONS.map(([k,v])=><option key={k} value={k}>{v}</option>)}
        </select>
        <select aria-label="Accuracy predictor" className="rounded border border-border bg-panel p-1" value={scope} onChange={e=>setScope(e.target.value)}>
          <option value="served">All served predictors</option><option value="wxrd3">Served WXPredict v3</option>
          <option value="wxrd3_raw">WXPredict v3 · all raw paired calls</option>
        </select>
      </div>
    </div>
    <p className="max-w-5xl text-xs leading-relaxed text-muted">
      Predict → freeze → settle → score → learn → validate. Only the first call counts, after its local day ends and the venue confirms the winner.
      Rank is observed winning-bucket accuracy, with uncertainty and sample counts. It does not measure trading returns.
      Overall uses dates with {scope === 'served' ? 'all six common scheduled calls' : 'noon and both before-peak checkpoints'}, excluding the explicit after-peak call; noon can coincide with or follow a city’s typical peak.
      Day ahead is the clean comparison before the local day starts.
      Raw paired calls include WXPredict even when the baseline was selected, so model selection cannot hide its misses.
    </p>
    {scope !== 'served' && ['day_ahead','d1_eve','morning'].includes(horizon) && <p className="text-xs text-muted">WXPredict v3 is not used at this horizon. Choose noon or a peak-relative checkpoint to inspect its record.</p>}
    {scope === 'served' && ['day_ahead','d1_eve'].includes(horizon) && <p className="text-xs text-muted">
      Early calls use the corrected weather forecast. Its nightly station and MOS learning is separate from the WXPredict noon/peak selector.
      New evening-before calls also freeze a raw-input-centre comparison with the same width and calibration. Older calls and day-ahead rows have no such paired receipt;
      a missing comparison does not mean the correction helped. Recent dates can have incomplete settlements.
    </p>}
    <div className="text-xs">{total.hits}/{total.n} correct · {pct(total.n ? total.hits/total.n : null)}
      <span className={overdue ? 'ml-3 text-orange-400' : 'ml-3 text-muted'}>{overdue ? 'Scoring overdue · ' : 'Scored '}{fmtDateTime(asOf)}</span>
    </div>
    <DataState relation="v_city_prediction_validation" loading={q.loading} error={q.error} truncated={q.truncated}
      isEmpty={!q.data?.length} emptyTitle="City validation awaiting its first run"
      emptyBody="The validation job publishes every active city, including cities with no eligible settled calls." onRetry={q.refresh}>
      {!!recent.length && <div className="overflow-x-auto rounded border border-border p-3">
        <h3 className="mb-2 text-xs font-semibold">Recent daily results · selected cities and horizon</h3>
        <table className="w-full text-xs"><thead className="text-muted"><tr>
          {['Target date (city local)','Correct / scored','Hit rate','Cities with scores','Known pending calls'].map(h=><th key={h} className="px-2 py-1 text-left font-normal">{h}</th>)}
        </tr></thead><tbody>{recent.map(d=><tr key={d.date} className="border-t border-border">
          <td className="px-2 py-1">{d.date}</td><td className="px-2 py-1">{d.hits}/{d.n}</td>
          <td className="px-2 py-1">{pct(d.hit_rate)}</td><td className="px-2 py-1">{d.cities}/{cities.length}</td>
          <td className="px-2 py-1">{d.pending == null ? 'Not measured' : d.pending}</td>
        </tr>)}</tbody></table>
        <p className="mt-2 text-[11px] text-muted">A date can be partially settled. Missing city outcomes are excluded from accuracy; zero known pending calls does not prove complete coverage.</p>
      </div>}
      <div className="overflow-x-auto"><table className="w-full text-xs">
        <thead className="text-muted"><tr>{['Rank / city','Correct / settled','Hit rate · 95% interval','Log loss / Brier','Confidence gap','Evidence / why'].map(h=><th key={h} className="px-2 py-2 text-left font-normal">{h}</th>)}</tr></thead>
        <tbody>{ranked.map((r,i)=><tr key={r.city} className="border-t border-border align-top">
          <td className="whitespace-nowrap px-2 py-2">{r.n ? i+1 : '—'}. {r.display_name}<span className="block text-[10px] text-muted">{r.timezone}</span></td>
          <td className="whitespace-nowrap px-2 py-2">{r.hits}/{r.n}<span className="block text-muted">{r.dates} dates · {r.pending == null ? "pending not measured" : `${r.pending} pending`}</span></td>
          <td className="whitespace-nowrap px-2 py-2">{pct(r.hit_rate)}<span className="block text-muted">{r.interval95 ? r.interval95.map(pct).join('–') : 'Not measured'}</span></td>
          <td className="whitespace-nowrap px-2 py-2">{num(r.metrics.log_loss)} / {num(r.metrics.brier)}</td>
          <td className="px-2 py-2">{pct(r.calibration_gap)}</td>
          <td className="min-w-72 px-2 py-2"><p>{evidenceWords(r)}</p>
            {!!r.recent_dates?.length && <p className="mt-1 text-muted">Recent target dates: {r.recent_dates.map(d=>`${d.date}: ${d.hits}/${d.n}${d.pending == null ? ' (pending not measured)' : d.pending ? `, ${d.pending} pending` : ''}`).join(' · ')}</p>}
            {r.model_selection && <p className="mt-1">Horizon choice: {r.model_selection.effective.selected === 'baseline' ? 'corrected weather baseline' : 'WXPredict v3'} · {r.model_selection.effective.active ? 'validated selection' : 'horizon prior'}</p>}
            <details className="mt-1 text-muted"><summary className="cursor-pointer text-accent">Freeze, learning and validation</summary>
            <p className="mt-1">{r.period_start}–{r.period_end} · {r.verified} server-receipted / {r.legacy} legacy calls. Legacy timestamps are not new training evidence.</p>
            {r.horizon === 'd1_eve' && <p>Early correction comparison: {r.early_comparison?.calls ?? 0} frozen pairs · raw input centre right {r.early_comparison?.raw_centre_hits ?? 0}, corrected right {r.early_comparison?.corrected_hits ?? 0}; correction helped {r.early_comparison?.correction_helped ?? 0}, hurt {r.early_comparison?.correction_hurt ?? 0}. Log-loss gain {num(r.early_comparison?.mean_log_loss_gain)} (positive favours correction). Only the centre changes; this is a diagnostic, not a validated replacement model.</p>}
            {r.early_learning?.map(s=><p key={s.lead_days} className="mt-1">Current weather-model learning · lead {s.lead_days} day(s): {s.version}, fitted {s.as_of}, refreshed {fmtDateTime(s.computed_at)}. Width calibration {s.calibration?.applies ? 'validated for this blend' : 'held at existing width'} · {s.calibration?.reason ?? 'no calibration evidence recorded'}. {s.calibration?.n_fit ?? 0} earlier fit dates / {s.calibration?.n_validation ?? 0} later validation dates. This is the current recipe state, not a change to any historical call.</p>)}
            <p>Exact paired baseline: {r.baseline.n} calls · {pct(r.baseline.hit)} · log loss {num(r.baseline.log_loss)}. Raw v3: {r.raw.n} · {pct(r.raw.hit)} · {num(r.raw.log_loss)}.</p>
            {r.paired_audit && <p>Matched forward audit: {r.paired_audit.calls} calls across {r.paired_audit.dates} dates · baseline alone right {r.paired_audit.baseline_only_hits}, raw v3 alone right {r.paired_audit.raw_only_hits}, both missed {r.paired_audit.both_missed}. Raw misses warmer than settlement {r.paired_audit.raw_warmer_misses}, cooler {r.paired_audit.raw_cooler_misses}. Mean saved report age {r.paired_audit.mean_report_age_min?.toFixed(1) ?? '—'} min. These describe the frozen evidence, not a causal explanation.</p>}
            {r.model_selection && <p>Model selection: {r.model_selection.effective.reason}. Training {r.model_selection.train_dates} older dates / validation {r.model_selection.validation_dates} later dates. Fit {fmtDateTime(r.model_selection.fit_at)}, validated through {r.model_selection.validated_through ?? 'not yet'}. Version {r.model_selection.version}.</p>}
            <p>Reviewed-miss public forecast MAE {r.why.public_forecast_mae_c?.toFixed(2) ?? '—'} °C; bias {r.why.public_forecast_bias_c?.toFixed(2) ?? '—'} °C. Associations are not causal explanations of the model.</p>
            {r.learning.map(s=><p key={s.phase} className="mt-1">{s.phase.replaceAll('_',' ')}: {s.active ? 'active' : 'raw prediction'} · {s.decision} · {s.reason}. Training {s.train_dates} dates / validation {s.validation_dates} dates. T={s.effective_params.temperature.toFixed(3)}, tilt={s.effective_params.tilt.toFixed(3)}. Fit {s.as_of}, validated through {s.validated_through ?? 'not yet'}. Version {s.version}.</p>)}
            <p className="mt-1">Predictors: {Object.entries(r.versions).map(([v,n])=>`${v}: ${n}`).join('; ') || 'Awaiting settled calls'}</p>
          </details></td>
        </tr>)}</tbody>
      </table></div>
    </DataState>
  </section>;
}
