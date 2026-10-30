"use client";
import { useQuery } from '@/lib/useQuery';
import { paperRead } from '@/lib/paperSupabase';
import { dayMaxCap, DAY_MAX_TIMES, type DayMaxData } from '@/lib/dayMax';

export default function DayMaxPrediction({openLedger}:{openLedger:(id:string)=>void}) {
  const q=useQuery<DayMaxData>(()=>paperRead('day_max'),[],60000);
  const d=q.data;
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
      <p className="text-sm text-muted">Buys YES on the original frozen prediction’s top bucket once per city and date, then holds through settlement. This measures following the forecast, including picks without a positive price edge. It uses each city’s fixed entry checkpoint and rejects entries at or after its saved expected peak. Cash, risk rails and live book depth can reduce or block a fill.</p>
      <p className="text-xs text-muted">Activated: {d.activated_at?new Date(d.activated_at).toLocaleString():'not yet'} · Last worker: {d.last_run?`${new Date(d.last_run.logged_at).toLocaleString()} (${d.last_run.status})`:'waiting for first hourly run'}{d.last_run?.detail.waiting?` · ${d.last_run.detail.waiting}`:''}</p>
      <details><summary className="cursor-pointer text-sm">Selected cities and entry times</summary>
        <p className="text-xs text-muted my-2">Choices stay fixed for prospective measurement. Selected from calls recorded at least 60 minutes before the reviewed high, with at least five settled dates, using the lower confidence bound of hit rate. This small historical sample does not establish profitability. Checkpoint labels use each city’s local timezone.</p>
        <div className="overflow-x-auto"><table className="w-full text-sm"><thead><tr><th className="text-left">City</th><th className="text-left">Entry checkpoint</th><th className="text-left">Local timezone</th><th className="text-right">Selection record</th></tr></thead><tbody>{d.cities.map(c=><tr key={c.city_key}><td>{d.names[c.city_key]?.display_name??c.city_key}</td><td>{DAY_MAX_TIMES[c.checkpoint]??c.checkpoint}</td><td>{d.names[c.city_key]?.timezone??'Unavailable'}</td><td className="text-right">{c.hits??'—'}/{c.calls??'—'} · {c.dates??'—'} dates</td></tr>)}</tbody></table></div>
      </details>
      <details><summary className="cursor-pointer text-sm">Recent daily allocations ({d.entries.length}, last 7 days)</summary>
        {!d.entries.length&&<p className="text-sm my-2">Waiting for the next eligible frozen call after activation. Past calls are not backfilled into trades.</p>}
        <div className="overflow-x-auto"><table className="w-full text-sm"><thead><tr><th className="text-left">City / date</th><th className="text-left">Original pick</th><th className="text-left">Frozen receipt</th><th className="text-left">Execution</th><th>Spent incl. fees</th></tr></thead><tbody>{d.entries.map(e=><tr key={e.checkpoint_id}><td>{d.names[e.city_key]?.display_name??e.city_key} · {e.target_date}</td><td>{e.label??e.pick.top_band_id} ({Math.round(e.pick.top_prob*100)}%)</td><td>{new Date(e.pick.frozen_at).toLocaleString()}</td><td>{e.status}{e.reason?` · ${e.reason}`:''}</td><td>${e.spent.toFixed(2)}</td></tr>)}</tbody></table></div>
      </details>
      {!!Object.keys(d.last_run?.detail.skipped??{}).length&&<details><summary className="cursor-pointer text-sm">Last worker’s skipped entries</summary><ul className="text-xs">{Object.entries(d.last_run!.detail.skipped!).map(([why,n])=><li key={why}>{why} ({n})</li>)}</ul></details>}
    </>}
  </section>;
}
