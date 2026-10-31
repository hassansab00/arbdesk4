"use client";
import { useEffect, useState } from 'react';
import { supabase } from '@/lib/supabase';
import { useQuery } from '@/lib/useQuery';
import { DataState } from '@/components/DataState';
import { fmtDateTime } from '@/lib/time';
import { HORIZONS, CityReport, rankCities, recentCityScores, evidenceWords, timingView, timingReason, readinessWords } from '@/lib/cityValidation';

type Row = { city_key: string; checkpoint: string; scope: string; as_of: string; report: CityReport };
const pct = (n: number | null | undefined) => n == null ? '—' : `${(100*n).toFixed(1)}%`;
const num = (n: number | null | undefined) => n == null ? '—' : n.toFixed(3);
export default function CityPredictionValidation({ cities, moment, setMoment }: {
  cities: string[]; moment: string; setMoment: (moment: string) => void;
}) {
  const [overall,setOverall] = useState(false);
  const horizon = overall ? 'overall' : moment;
  const [scope,setScope] = useState('served');
  const [strict,setStrict] = useState(false);
  useEffect(()=>{ setOverall(false); },[moment]);
  const q = useQuery<Row[]>(()=>supabase.from('v_city_prediction_validation').select('*')
    .eq('checkpoint',horizon).eq('scope',scope).in('city_key',cities).limit(500),[horizon,scope,cities.join(',')],60000,500);
  const ranked = rankCities((q.data ?? []).map(r=>timingView(r.report,strict)));
  const recent = recentCityScores(ranked);
  const asOf = q.data?.map(r=>r.as_of).sort()[0];
  const overdue = asOf && Date.now()-Date.parse(asOf)>36*3600000;
  const total = ranked.reduce((a,r)=>({hits:a.hits+r.hits,n:a.n+r.n}),{hits:0,n:0});
  return <section className="space-y-3 rounded border border-border bg-panel p-4">
    <div className="flex flex-wrap items-center justify-between gap-3">
      <h2 className="text-sm font-semibold">City accuracy · original frozen calls</h2>
      <div className="flex flex-wrap gap-2 text-xs">
        <select aria-label="Accuracy horizon" className="rounded border border-border bg-panel p-1" value={horizon} onChange={e=>{
          const next=e.target.value;
          setOverall(next==='overall');
          if(next!=='overall') setMoment(next);
        }}>
          {HORIZONS.map(([k,v])=><option key={k} value={k}>{v}</option>)}
        </select>
        <select aria-label="Accuracy predictor" className="rounded border border-border bg-panel p-1" value={scope} onChange={e=>setScope(e.target.value)}>
          <option value="served">All served predictors</option><option value="wxrd3">Served WXPredict v3</option>
          <option value="wxrd3_raw">WXPredict v3 · all raw paired calls</option>
        </select>
        <select aria-label="Capture timing" className="rounded border border-border bg-panel p-1" value={strict ? 'strict' : 'all'} onChange={e=>setStrict(e.target.value==='strict')}>
          <option value="all">All scheduled calls</option><option value="strict">Before expected peak · verified</option>
        </select>
      </div>
    </div>
    <p className="text-xs text-muted">
      {overall
        ? <>Overall combines checkpoints. The cards and Was it right? remain on {HORIZONS.find(([key])=>key===moment)?.[1] ?? moment}.</>
        : <>This checkpoint selection is shared with the city cards and Was it right?. Compare the original frozen pick; the live forecast can update between captures.</>}
    </p>
    <p className="max-w-5xl text-xs leading-relaxed text-muted">
      Predict → freeze → settle → score → learn → validate. Only the first call counts, after its local day ends and the venue confirms the winner.
      Rank is observed winning-bucket accuracy, with uncertainty and sample counts. It does not measure trading returns.
      Overall uses dates with {scope === 'served' ? 'all six common scheduled calls' : 'noon and both before-peak checkpoints'}, excluding the explicit after-peak call; noon can coincide with or follow a city’s typical peak.
      Day ahead is the clean comparison before the local day starts.
      Raw paired calls include WXPredict even when the baseline was selected, so model selection cannot hide its misses.
    </p>
    <p className="max-w-5xl text-xs leading-relaxed text-muted">
      Scheduled time is a label; the actual server freeze can be up to 75 minutes later. The verified timing view requires a server receipt before
      the earliest peak estimate saved with that call (or before the target day begins for an early call). It excludes explicit after-peak calls,
      missing receipts and unknown saved peaks. It never substitutes today&apos;s forecast or peak table.
      <strong> Expected peak is an estimate, not proof that today&apos;s final maximum was still ahead.</strong> This is a reporting filter; model fitting and promotion are unchanged.
      New calls preserve the monthly city peak even when the baseline supplies the prediction. Older missing evidence stays unknown.
      {strict && horizon === 'overall' && ' Overall requires every common scheduled horizon on the date to pass this timing filter; incomplete dates remain excluded.'}
    </p>
    {strict && ranked.some(r=>!r.capture_timing) && <p className="text-xs text-warn">Some reports await the timing-aware scoring run. Their strict scores are unavailable; scheduled accuracy is not substituted.</p>}
    {strict && horizon === 'day_ahead' && <p className="text-xs text-muted">The day-ahead archive supplies pre-day call timestamps without server freeze receipts. Those rows remain available under All scheduled calls and do not qualify for this verified filter.</p>}
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
      {!strict && <p className="max-w-5xl text-xs text-warn">
        Scheduled accuracy includes late-information calls. The reviewed-high split below separates captures before the observed station high from those at or after it.
        This is hindsight analysis of reviewed dates, not a real-time finality test or proof of tradeable skill. Missing reviews are unknown.
      </p>}
      <div className="overflow-x-auto"><table className="w-full text-xs">
        <thead className="text-muted"><tr>{['Rank / city','Correct / settled','Hit rate · 95% interval','Log loss / Brier','Reliability error','Evidence / why'].map(h=><th key={h} className="px-2 py-2 text-left font-normal">{h}</th>)}</tr></thead>
        <tbody>{ranked.map((r,i)=><tr key={r.city} className="border-t border-border align-top">
          <td className="whitespace-nowrap px-2 py-2">{r.n ? i+1 : '—'}. {r.display_name}<span className="block text-[10px] text-muted">{r.timezone}</span></td>
          <td className="whitespace-nowrap px-2 py-2">{r.hits}/{r.n}<span className="block text-muted">{r.dates} dates · {r.pending == null ? "pending not measured" : `${r.pending} pending`}</span></td>
          <td className="whitespace-nowrap px-2 py-2">{pct(r.hit_rate)}<span className="block text-muted">{r.interval95 ? r.interval95.map(pct).join('–') : 'Not measured'}</span></td>
          <td className="whitespace-nowrap px-2 py-2">{num(r.metrics.log_loss)} / {num(r.metrics.brier)}</td>
          <td className="px-2 py-2" title="Weighted absolute difference between stated confidence and hit rate, grouped by confidence; smaller is better. This is not the signed calibration gap on the cards.">{pct(r.calibration_gap)}</td>
          <td className="min-w-72 px-2 py-2"><p>{strict ? (r.n ? 'Timing-qualified subset · compare the same horizon and city' : 'Awaiting timing-qualified settled calls') : evidenceWords(r)}</p>
            {!strict && r.observed_peak_timing && <p className="mt-1 text-muted">
              Before reviewed high: {r.observed_peak_timing.before.hits}/{r.observed_peak_timing.before.n} correct · {pct(r.observed_peak_timing.before.hit_rate)}.
              {r.observed_peak_timing.before_60m && <> At least 1 h before: {r.observed_peak_timing.before_60m.hits}/{r.observed_peak_timing.before_60m.n} · {pct(r.observed_peak_timing.before_60m.hit_rate)}.</>}
              {r.observed_peak_timing.before_120m && <> At least 2 h before: {r.observed_peak_timing.before_120m.hits}/{r.observed_peak_timing.before_120m.n} · {pct(r.observed_peak_timing.before_120m.hit_rate)}.</>}
              {' '}At / after reviewed high: {r.observed_peak_timing.at_or_after.hits}/{r.observed_peak_timing.at_or_after.n} · {pct(r.observed_peak_timing.at_or_after.hit_rate)}.
              {' '}{r.observed_peak_timing.unknown_calls} unknown timing · {r.observed_peak_timing.pending_calls} pending.
            </p>}
            {r.capture_timing && <p className="mt-1 text-muted">Timing: {r.capture_timing.eligible_calls}/{r.capture_timing.calls} calls qualify · {r.capture_timing.excluded_calls} excluded.
              {Object.entries(r.capture_timing.exclusions).map(([why,n])=>` ${timingReason(why)}: ${n}.`).join('')}</p>}
            {!!r.recent_dates?.length && <p className="mt-1 text-muted">Recent target dates: {r.recent_dates.map(d=>`${d.date}: ${d.hits}/${d.n}${d.pending == null ? ' (pending not measured)' : d.pending ? `, ${d.pending} pending` : ''}`).join(' · ')}</p>}
            {r.model_selection && <p className="mt-1">Horizon choice: {r.model_selection.effective.selected === 'baseline' ? 'corrected weather baseline' : 'WXPredict v3'} · {r.model_selection.effective.active ? 'validated selection' : 'horizon prior'}</p>}
            {r.market_comparison && <p className="mt-1 text-muted">Same-call market comparison: {r.market_comparison.n} pairs across {r.market_comparison.dates} dates · model {pct(r.market_comparison.model.hit)}, saved market favourite {pct(r.market_comparison.market.hit)}.
              {' '}Log loss {num(r.market_comparison.model.log_loss)} / {num(r.market_comparison.market.log_loss)} (lower is better). This compares saved complete quotes, not executable returns.</p>}
            <details className="mt-1 text-muted"><summary className="cursor-pointer text-accent">Freeze, learning and validation</summary>
            <p className="mt-1">{r.period_start}–{r.period_end} · {r.verified} server-receipted / {r.legacy} legacy calls. Legacy timestamps are not new training evidence.</p>
            {!!r.capture_timing?.recent_calls.length && <div className="my-2 overflow-x-auto">
              <p>Recent original captures · city clock ({r.timezone}); includes calls excluded by the timing filter.</p>
              <table className="mt-1 text-[11px]"><thead><tr>{['Target / checkpoint','Scheduled local','Actual freeze local','Delay','Saved timing cutoff local','Reviewed station high local','Timing / result'].map(h=><th key={h} className="px-2 py-1 text-left font-normal">{h}</th>)}</tr></thead>
                <tbody>{r.capture_timing.recent_calls.map(c=><tr key={`${c.date}-${c.checkpoint}`} className="border-t border-border">
                  <td className="whitespace-nowrap px-2 py-1">{c.date} · {HORIZONS.find(([k])=>k===c.checkpoint)?.[1] ?? c.checkpoint}</td>
                  <td className="whitespace-nowrap px-2 py-1">{c.scheduled_local ? fmtDateTime(c.scheduled_local,r.timezone) : 'Not recorded'}</td>
                  <td className="whitespace-nowrap px-2 py-1" title={c.frozen_at ?? ''}>{c.frozen_at ? fmtDateTime(c.frozen_at,r.timezone) : 'No server receipt'}</td>
                  <td className="whitespace-nowrap px-2 py-1">{c.delay_minutes == null ? '—' : `${c.delay_minutes.toFixed(1)} min`}</td>
                  <td className="whitespace-nowrap px-2 py-1">{c.cutoff_local ? fmtDateTime(c.cutoff_local,r.timezone) : 'Unknown'}
                    {c.minutes_before_expected_peak != null && <span className="block text-muted">
                      Frozen {Math.abs(c.minutes_before_expected_peak).toFixed(0)} min {c.minutes_before_expected_peak > 0 ? 'before' : 'at / after'} cutoff
                    </span>}
                  </td>
                  <td className="whitespace-nowrap px-2 py-1">{c.observed_timing?.peak_local ? <>
                    {fmtDateTime(c.observed_timing.peak_local,r.timezone)}
                    <span className="block text-muted">{c.observed_timing.relation === 'before' ? 'Frozen before high' : 'Frozen at / after high'}</span>
                  </> : c.observed_timing?.relation === 'pending' ? 'Awaiting outcome' : 'Not measured'}</td>
                  <td className={`px-2 py-1 ${c.before_expected_peak ? '' : 'text-warn'}`}>{timingReason(c.reason)} · {c.hit == null ? 'pending' : c.hit ? 'hit' : 'miss'}
                    {c.forecast_input && <span className="block text-muted">Input: {c.forecast_input.source_model ?? 'unknown model'} · run {fmtDateTime(c.forecast_input.run_at ?? null,r.timezone)} · raw {c.forecast_input.raw_max_c?.toFixed(2) ?? '—'} °C.
                      {c.forecast_input.uncertainty?.applied && <> Width uses the measured advance-lead error prior.</>}
                    </span>}
                  </td>
                </tr>)}</tbody></table>
            </div>}
            {!strict && ['d1_eve','morning'].includes(r.horizon) && <p>Early correction comparison: {r.early_comparison?.calls ?? 0} frozen pairs · raw input centre right {r.early_comparison?.raw_centre_hits ?? 0}, corrected right {r.early_comparison?.corrected_hits ?? 0}; correction helped {r.early_comparison?.correction_helped ?? 0}, hurt {r.early_comparison?.correction_hurt ?? 0}. Log-loss gain {num(r.early_comparison?.mean_log_loss_gain)} (positive favours correction). Only the centre changes; the comparison itself does not approve an adjustment.</p>}
            {r.early_adaptation?.map(s=><p key={s.phase+s.version} className="mt-1">Early correction strength · {s.phase}: {s.active ? 'validated strength available for this recipe' : 'baseline retained'} · {s.reason}. Strength {s.effective_strength.toFixed(1)} (1 = existing correction, 0 = raw centre). {readinessWords(s.readiness)} Version {s.version}.</p>)}
            {!strict && ['d1_eve','morning'].includes(r.horizon) && !r.early_adaptation?.length && <p>Early correction adaptation: awaiting original candidate ladders with known station, source recipe and settlement rules. Older diagnostic pairs keep their scores but cannot approve a new strength.</p>}
            {r.early_learning?.map(s=><p key={s.lead_days} className="mt-1">Current weather-model learning · lead {s.lead_days} day(s): {s.version}, fitted {s.as_of}, refreshed {fmtDateTime(s.computed_at)}. Width calibration {s.calibration?.applies ? 'validated for this blend' : 'held at existing width'} · {s.calibration?.reason ?? 'no calibration evidence recorded'}. {s.calibration?.n_fit ?? 0} earlier fit dates / {s.calibration?.n_validation ?? 0} later validation dates. This is the current recipe state, not a change to any historical call.</p>)}
            <p>Exact paired baseline: {r.baseline.n} calls · {pct(r.baseline.hit)} · log loss {num(r.baseline.log_loss)}. Raw v3: {r.raw.n} · {pct(r.raw.hit)} · {num(r.raw.log_loss)}.</p>
            {r.paired_audit && <p>Matched forward audit: {r.paired_audit.calls} calls across {r.paired_audit.dates} dates · baseline alone right {r.paired_audit.baseline_only_hits}, raw v3 alone right {r.paired_audit.raw_only_hits}, both missed {r.paired_audit.both_missed}. Raw misses warmer than settlement {r.paired_audit.raw_warmer_misses}, cooler {r.paired_audit.raw_cooler_misses}. Mean saved report age {r.paired_audit.mean_report_age_min?.toFixed(1) ?? '—'} min. These describe the frozen evidence, not a causal explanation.</p>}
            {r.model_selection && <p>Model selection: {r.model_selection.effective.reason}. Training {r.model_selection.train_dates} older dates / validation {r.model_selection.validation_dates} later dates. Fit {fmtDateTime(r.model_selection.fit_at)}, validated through {r.model_selection.validated_through ?? 'not yet'}. Version {r.model_selection.version}.</p>}
            {r.model_selection && <p>{readinessWords(r.model_selection.readiness)}</p>}
            <p>Reviewed-miss public forecast MAE {r.why.public_forecast_mae_c?.toFixed(2) ?? '—'} °C; bias {r.why.public_forecast_bias_c?.toFixed(2) ?? '—'} °C. Associations are not causal explanations of the model.</p>
            {r.learning_pipeline && <p className="mt-1">Raw model fitted through {r.learning_pipeline.raw_model_fit_through ?? 'unknown'}. Challenger: {r.learning_pipeline.challenger_status}. Refreshing scores or weather inputs does not retrain these weights.</p>}
            {r.learning.map(s=><div key={s.phase} className="mt-1"><p>{s.phase.replaceAll('_',' ')}: {s.active ? 'calibration active' : 'calibration held · neutral adjustment'} · {s.decision} · {s.reason}. Training {s.train_dates} dates / validation {s.validation_dates} dates. {s.eligibility && <>Eligible original calls: {s.eligibility.eligible_rows}/{s.eligibility.input_rows}. Excluded: {Object.entries(s.eligibility.exclusions).map(([k,v])=>`${k.replaceAll('_',' ')}: ${v}`).join(' · ') || 'none'}.</>} T={s.effective_params.temperature.toFixed(3)}, tilt={s.effective_params.tilt.toFixed(3)}. Fit {s.as_of}, validated through {s.validated_through ?? 'not yet'}. Version {s.version}.</p>
              <p>{readinessWords(s.readiness)}</p></div>)}
            <p className="mt-1">Predictors: {Object.entries(r.versions).map(([v,n])=>`${v}: ${n}`).join('; ') || 'Awaiting settled calls'}</p>
          </details></td>
        </tr>)}</tbody>
      </table></div>
    </DataState>
  </section>;
}
