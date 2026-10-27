"use client";
import React, { useEffect, useMemo, useState } from 'react';
import { supabase } from '@/lib/supabase';
import { useQuery } from '@/lib/useQuery';
import { momentLabel, type HindsightRow } from '@/lib/focus';
import { fmtPct, fmtPp, fmtPrice } from '@/lib/format';
import { fmtTempDelta, type Unit } from '@/lib/units';
import { cityInstant, fmtDateTime, fmtResolutionDate } from '@/lib/time';
import type { CityCard, StatusRow } from '@/lib/cityCards';
import { buildManualGuide, guideReviewTime, MIN_REFERENCE_DATES, readGuideReports,
  type GuideReportRow, type ManualGuide } from '@/lib/cityManualGuide';

type Peak = {peak_hour_local:number|null;window_width_h:number|null;n_days:number|null}|null;
type Props = {card:CityCard;moment:string;history:HindsightRow[];status:StatusRow|null;peak:Peak};

export default function CityManualGuide(props:Props) {
  const {card,moment,history,status}=props;
  const q=useQuery<GuideReportRow[]>(()=>readGuideReports(supabase,card.city_key),[card.city_key]);
  const [now,setNow]=useState(()=>Date.now());
  // Refresh local freshness checks without another network polling loop.
  useEffect(()=>{const timer=setInterval(()=>setNow(Date.now()),60000);return()=>clearInterval(timer);},[]);
  const guide=useMemo(()=>buildManualGuide({card,moment,history,status,rows:q.data ?? [],now}),
    [card,moment,history,status,q.data,now]);
  return <CityManualGuideView guide={guide} card={card} moment={moment} peak={props.peak}
    loading={q.loading} error={q.error} onRetry={q.refresh} />;
}

function reviewAt(date:string,checkpoint:string,peak:number|null,zone:string) {
  const clock=guideReviewTime(checkpoint,peak);
  if(!clock) return checkpoint==='day_ahead'
    ? `Before ${fmtResolutionDate(date)} 00:00 · ${zone} (start of the target day)`
    : 'Measured peak time unavailable';
  const d=new Date(`${date}T00:00:00Z`);d.setUTCDate(d.getUTCDate()+clock.dayOffset);
  const instant=cityInstant(d.toISOString().slice(0,10),clock.hour,zone);
  return instant?`${fmtDateTime(instant.toISOString(),zone)} · ${zone}`:'City-local review time unavailable';
}

/** Pure view also exercised with synthetic reports by the render tests. */
export function CityManualGuideView({guide,card,moment,peak,loading,error,onRetry}: {
  guide:ManualGuide;card:CityCard;moment:string;peak:Peak;loading:boolean;error:string|null;onRetry?:()=>void;
}) {
  const reference=guide.reference,unit:Unit=card.unit,zone=card.timezone ?? 'UTC';
  const peakAt=peak?.peak_hour_local!=null?cityInstant(card.for_date,peak.peak_hour_local,zone):null;
  return <section aria-label="City manual trading guide" className="mt-3 rounded border border-accent/40 bg-panel2 p-3">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <h3 className="text-xs font-semibold">Manual trading guide · {card.name}</h3>
      <span className={`rounded border px-2 py-0.5 text-[11px] ${loading||error||guide.state==='Wait'?'border-warn/50 text-warn':'border-accent/50 text-accent'}`}>
        {loading?'Reading history':error?'History unavailable':guide.state}
      </span>
    </div>
    <p className="mt-1 text-[11px] text-muted">
      For {fmtResolutionDate(card.for_date)}. A city-specific review plan from observed history;
      its entry timing has not passed a forward profitability test.
    </p>
    {loading?<p className="mt-2 text-muted">Reading this city&rsquo;s original-call record…</p>
      :error?<p className="mt-2 text-bad">Could not read the city history: {error}{' '}
        {onRetry&&<button className="text-accent underline" onClick={onRetry}>Retry</button>}</p>
      :<>
        {!!guide.blockers.length&&<ul className="mt-2 list-disc space-y-0.5 pl-4 text-[11px] text-warn">
          {guide.blockers.map(b=><li key={b}>{b}</li>)}
        </ul>}
        <div className="mt-2 grid gap-3 sm:grid-cols-2">
          <div>
            <h4 className="text-[11px] font-semibold">When to review</h4>
            {reference.comparable?<>
              <p>{reference.leaders.length>1?'Joint strongest recorded checkpoints: ':'Strongest recorded checkpoint: '}
                <b>{reference.leaders.map(momentLabel).join(' / ')}</b>.</p>
              {reference.leaders.map(h=><p key={h} className="mt-0.5 text-[11px] text-muted">
                {momentLabel(h)}: {reviewAt(card.for_date,h,peak?.peak_hour_local ?? null,zone)}
              </p>)}
              <p className="mt-1 text-[11px] text-muted">
                Same {reference.dates.length} past dates across {reference.table.length} available checkpoints.
                These are scheduled references; actual capture can be up to 75 minutes later.
              </p>
              {reference.table.filter(r=>reference.leaders.includes(r.checkpoint)).map(r=><p key={r.checkpoint} className="mt-1 text-[11px] text-muted">
                {momentLabel(r.checkpoint)}: {r.verifiedBeforePeak}/{r.n} of those calls have a server receipt before the saved expected peak.
              </p>)}
            </>:<p className="text-muted">At least {MIN_REFERENCE_DATES} matched past dates across two checkpoints are needed.
              Missing evidence never becomes a recommended time.</p>}
            {peak?.peak_hour_local!=null&&<p className="mt-1 text-[11px] text-muted">
              Typical monthly peak: {peakAt?fmtDateTime(peakAt.toISOString(),zone):'clock unavailable'}
              {' '}· {zone}{peak.n_days!=null?` · ${peak.n_days} observed days`:''}.
              Today can peak earlier or later; this is not proof the maximum is finished.
            </p>}
          </div>
          <div>
            <h4 className="text-[11px] font-semibold">What to look for · {momentLabel(moment)}</h4>
            {guide.trend.n>0&&guide.trend.biasC!==null?<p className="text-[11px]">
              Original calls over {guide.trend.n} earlier dates: {Math.abs(guide.trend.biasC)<.05?'little mean temperature bias':<>
                actual maxima averaged <b>{fmtTempDelta(Math.abs(guide.trend.biasC),unit).replace('+','')}{' '}
                  {guide.trend.biasC>0?'above':'below'}</b> the predicted centre
                {' '}({guide.trend.biasC>0?'a cold tendency':'a warm tendency'})</>}.
              Do not mechanically shift today&rsquo;s bucket by that average.
              {guide.trend.n<MIN_REFERENCE_DATES&&<> Limited temperature-error sample.</>}
            </p>:<p className="text-[11px] text-muted">No earlier temperature-error record for this selected moment.</p>}
            {!!guide.weather.length&&<p className="mt-1 text-[11px]">
              Watch {guide.weather.map(([name,n])=>`${name} disagreement (${n} reviewed misses)`).join(' and ')}.
              {' '}{guide.reviewed}/{guide.missed} missed dates reviewed at this moment; these are associations, not proven causes.
            </p>}
            {!guide.weather.length&&<p className="mt-1 text-[11px] text-muted">No supported weather-specific warning for this moment yet.</p>}
          </div>
        </div>
        {!!guide.cautions.length&&<ul className="mt-2 list-disc space-y-0.5 pl-4 text-[11px] text-muted">
          {guide.cautions.map(c=><li key={c}>{c}</li>)}
        </ul>}
        <div className="mt-2 border-t border-border pt-2 text-[11px]">
          <b>Price and execution check.</b>{' '}
          {card.best&&<>The screen&rsquo;s model candidate is {card.best.side} {card.best.band ?? 'an unlabelled bucket'}:
            {' '}{fmtPrice(card.best.price)} stored mark, {fmtPp(card.best.edge)} stored net edge. </>}
          A stored mark is not an executable ask. Before a manual entry, check the contract station and rules,
          current buy price, spread, fees and available size together. A high city hit rate does not make an expensive share a good trade.
        </div>
        {reference.comparable&&<details className="mt-2 text-[11px]">
          <summary className="cursor-pointer text-accent">Comparable timing record and evidence limits</summary>
          <div className="mt-1 overflow-x-auto"><table className="w-full tabular-nums">
            <thead><tr className="text-left text-muted"><th>Checkpoint</th><th>Correct on matched dates</th><th>Receipt verified before expected peak</th></tr></thead>
            <tbody>{reference.table.map(r=><tr key={r.checkpoint} className="border-t border-border">
              <td className="py-1 pr-2">{momentLabel(r.checkpoint)}</td><td className="pr-2">{r.hits}/{r.n} · {fmtPct(r.rate,0)}</td>
              <td>{r.verifiedBeforePeak}/{r.n}</td>
            </tr>)}</tbody>
          </table></div>
          <p className="mt-1 text-muted">The ranking uses recorded scheduled calls and may include legacy or late-information calls;
            the last column shows the eligible receipt-backed subset on those dates. After-peak calls are excluded.
            The largest observed hit rate is selected from history, not validated on later unseen dates. This recent sample
            can span served model versions and does not establish a seasonal advantage, fair price or profitable return.</p>
        </details>}
        {guide.asOf&&<p className="mt-2 text-[10px] text-muted">History scored {fmtDateTime(guide.asOf,zone)} · {zone}.
          Original frozen calls remain unchanged; live prices and forecasts can move.</p>}
      </>}
  </section>;
}
