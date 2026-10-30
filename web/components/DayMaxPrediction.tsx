"use client";
import {useState} from 'react';
import { useQuery } from '@/lib/useQuery';
import { paperRead } from '@/lib/paperSupabase';
import { dayMaxCap, dayMaxStatus, DAY_MAX_TIMES, type DayMaxData } from '@/lib/dayMax';

export default function DayMaxPrediction({openLedger}:{openLedger:(id:string)=>void}) {
  const q=useQuery<DayMaxData>(()=>paperRead('day_max'),[],60000);
  const d=q.data;
  const [date,setDate]=useState(()=>new Date(Date.now()+86400000).toISOString().slice(0,10));
  let cap:number|null=null,configError:string|null=null;
  if(d) try {cap=dayMaxCap(d.cities);} catch(e) {configError=String(e);}
  const active=!!(d?.enabled&&d.account?.mode==='automatic'&&!d.account.entries_paused);
  return <section className="rounded border border-border bg-panel p-4 space-y-3">
    <div className="flex flex-wrap justify-between gap-2"><h2 className="font-semibold">Day Max Prediction <span className="text-xs text-muted">Paper · {active?'Automatic':'Waiting / paused'}</span></h2>
      <div className="flex gap-3 text-sm"><button onClick={q.refresh}>Refresh strategy</button>{d?.account&&<button onClick={()=>openLedger(d.account!.account_id)}>Open its ledger</button>}</div></div>
    {q.error&&<p role="alert">Could not read Day Max: {q.error}</p>}
    {configError&&<p role="alert">{configError}</p>}
    {!d&&q.loading&&<p>Loading strategy…</p>}
    {d&&<>
      <p className="text-sm">{d.cities.length} selected cities · $10 per city daily, including fees · maximum ${cap??'—'} per settlement date.</p>
      <p className="text-sm text-muted">Buys YES on the original frozen prediction’s top bucket once per city and date, then holds through settlement. This measures following the forecast, including picks without a positive price edge. Each city uses one fixed early checkpoint. Evening-before is 18:00 local; day-ahead takes the last complete forecast before local midnight and receives its immutable receipt in the next hourly run. Entries at or after the saved expected peak are refused. Cash, risk rails and live book depth can reduce or block a fill.</p>
      <p className="text-xs text-muted">Activated: {d.activated_at?new Date(d.activated_at).toLocaleString():'not yet'} · Last worker: {d.last_run?`${new Date(d.last_run.logged_at).toLocaleString()} (${d.last_run.status})`:'waiting for first hourly run'}{d.last_run?.detail.waiting?` · ${d.last_run.detail.waiting}`:''}</p>
      <details><summary className="cursor-pointer text-sm">Selected cities and entry times</summary>
        <p className="text-xs text-muted my-2">One choice per city, selected by hit rate on the same settled dates for day-ahead and evening-before; ties use evening-before. Historical day-ahead calls lack server freeze receipts and are diagnostic only. This paper experiment tests the choices prospectively; historical differences do not establish an edge or profitability. Times use each city’s timezone. A complete cycle offers one opportunity per city; missing forecasts, books or cash can prevent a trade.</p>
        <div className="overflow-x-auto"><table className="w-full text-sm"><thead><tr><th className="text-left">City</th><th className="text-left">Entry checkpoint</th><th className="text-left">Local timezone</th><th className="text-right">Selection record</th></tr></thead><tbody>{d.cities.map(c=><tr key={c.city_key}><td>{d.names[c.city_key]?.display_name??c.city_key}</td><td>{DAY_MAX_TIMES[c.checkpoint]??c.checkpoint}</td><td>{d.names[c.city_key]?.timezone??'Unavailable'}</td><td className="text-right">{c.hits??'—'}/{c.calls??'—'} · {c.dates??'—'} dates</td></tr>)}</tbody></table></div>
      </details>
      <div className="space-y-2"><label className="text-sm">Daily city status · settlement date <input type="date" value={date} onChange={e=>setDate(e.target.value)} className="bg-panel border border-border rounded px-2"/></label>
        <div className="overflow-x-auto"><table className="w-full text-sm"><thead><tr><th className="text-left">City</th><th className="text-left">Entry window / receipt (your time)</th><th className="text-left">Status</th></tr></thead><tbody>{d.cities.map(c=>{const s=dayMaxStatus(d,c,date);return <tr key={c.city_key}><td>{d.names[c.city_key]?.display_name??c.city_key}</td><td>{s.scheduled?new Date(s.scheduled).toLocaleString():'Unavailable'}</td><td>{s.status}</td></tr>})}</tbody></table></div>
      </div>
      <details><summary className="cursor-pointer text-sm">Recent daily allocations ({d.entries.length}, last 7 days)</summary>
        {!d.entries.length&&<p className="text-sm my-2">Waiting for the next eligible frozen call after activation. Past calls are not backfilled into trades.</p>}
        <div className="overflow-x-auto"><table className="w-full text-sm"><thead><tr><th className="text-left">City / date</th><th className="text-left">Original pick</th><th className="text-left">Frozen receipt</th><th className="text-left">Execution</th><th>Spent incl. fees</th></tr></thead><tbody>{d.entries.map(e=><tr key={e.checkpoint_id}><td>{d.names[e.city_key]?.display_name??e.city_key} · {e.target_date}</td><td>{e.label??e.pick.top_band_id} ({Math.round(e.pick.top_prob*100)}%)</td><td>{new Date(e.pick.frozen_at).toLocaleString()}</td><td>{e.status}{e.reason?` · ${e.reason}`:''}</td><td>${e.spent.toFixed(2)}</td></tr>)}</tbody></table></div>
      </details>
      {!!Object.keys(d.last_run?.detail.skipped??{}).length&&<details><summary className="cursor-pointer text-sm">Last worker’s skipped entries</summary><ul className="text-xs">{Object.entries(d.last_run!.detail.skipped!).map(([why,n])=><li key={why}>{why} ({n})</li>)}</ul></details>}
    </>}
  </section>;
}
