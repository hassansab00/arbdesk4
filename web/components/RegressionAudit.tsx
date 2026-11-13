"use client";
import {useEffect,useState} from 'react';
import {useQuery} from '../lib/useQuery';
import {PrivateSideAccess,privateSideRead} from './OriginalSideResults';
import {readinessWords} from '../lib/cityValidation';
import {AUDIT_HORIZONS,auditHorizon,auditNumber as n,auditPercent as pct,auditWords as words,
 type AuditCohort,type AuditCursor,type AuditPage,type AuditRow} from '../lib/regressionAudit';

function Cohort({label,value}:{label:string;value:AuditCohort}){
 const market=value.market,correction=value.correction;
 return <div className="min-w-0 space-y-1 rounded border border-border p-3">
  <h4 className="font-medium">{label} · {value.start??'Unknown start'} to {value.end_exclusive??'unknown end'} (end excluded)</h4>
  <p>Original served hits {n(value.hits)}/{n(value.n)} settled · {pct(value.hit_rate)} · log loss {n(value.log_loss,3)} (lower is better)</p>
  <p>{n(value.dates)} settled dates · {n(value.calls)} calls · {n(value.pending)} pending · {n(value.verified)} verified server receipts · mean stated confidence {pct(value.claimed)}</p>
  <p>{n(value.version_count)} saved version IDs; daily versions are not distinct model recipes.</p>
  <p>Matched saved market: {n(market?.n)} pairs / {n(market?.dates)} dates · model {pct(market?.model_hit_rate)} ({n(market?.model_hits)} hits) · market {pct(market?.market_hit_rate)} ({n(market?.market_hits)} hits).</p>
  <p>Saved raw centre → baseline correction: {n(correction?.n)} pairs · helped {n(correction?.helped)} · hurt {n(correction?.hurt)} · mean log-loss gain {n(correction?.log_loss_gain,3)} (positive favours correction).</p>
 </div>;
}

export function RegressionAuditCard({row}:{row:AuditRow}){
 return <article className="space-y-3 rounded border border-border p-3 text-xs">
  <h3 className="text-sm font-semibold">{words(row.city_key)} · {auditHorizon(row.checkpoint)}</h3>
  <p className="text-muted">Source scored at {row.source_as_of} · report fingerprint <span className="break-all">{row.source_report_fingerprint??'Not recorded'}</span></p>
  <p className={row.flags.length?'text-warn':'text-muted'}>Alerts: {row.flags.length?row.flags.map(words).join(' · '):'No threshold triggered'}.
   {' '}Comparable dates: {row.comparable_dates_ready===true?'ready':row.comparable_dates_ready===false?'insufficient':'not measured'}.
   {' '}Hit-rate change: {row.hit_rate_change==null?'Not measured':`${(100*row.hit_rate_change).toFixed(1)} percentage points`}.</p>
  {!!row.findings.length&&<ul className="list-inside list-disc">{row.findings.map(f=><li key={f}>{words(f)}</li>)}</ul>}
  <div className="grid gap-2 xl:grid-cols-2"><Cohort label="Recent local dates" value={row.recent}/><Cohort label="Prior local dates" value={row.prior}/></div>
  <p className="text-muted">Market comparisons use saved probabilities, not executable returns. Correction pairs compare the saved raw centre and baseline corrected centre; served adaptations are scored separately. These findings do not establish a cause.</p>
  <details><summary className="cursor-pointer text-accent">Learning gates · {row.learning.length} retained records</summary>
   {!row.learning.length&&<p>Learning readiness not recorded for this cell.</p>}
   {row.learning.map((s,i)=><div key={i} className="mt-2 space-y-1 border-t border-border pt-2">
    <p>{s.policy??'Policy not recorded'} · {s.phase??'Phase not recorded'} · {s.active===true?'Active':s.active===false?'Held / inactive':'Activation not recorded'}</p>
    <p>{s.decision?words(s.decision):'Decision not recorded'} · {s.reason??'Reason not recorded'}</p>
    <p>Older training dates {n(s.train_dates)} · later validation dates {n(s.validation_dates)}. {readinessWords(s.readiness)}</p>
    <p className="break-all text-muted">Version {s.model_version??'Not recorded'} · as of {s.as_of??'Not recorded'}</p>
   </div>)}
  </details>
  <details><summary className="cursor-pointer text-accent">Retained daily details · {row.daily_details_complete?'complete for the reported recent cohort':'incomplete for the reported recent cohort'}</summary>
   {!row.daily_details_complete&&<p className="my-2 text-warn">Evidence gap: retained daily details do not cover all reported recent settled or pending calls. Missing dates are not reconstructed.</p>}
   <div className="overflow-x-auto"><table className="w-full text-left text-xs"><thead><tr>{['City-local target date','Hits / settled','Hit rate','Pending','Saved raw → corrected hits','Helped / hurt','Paired log-loss gain'].map(h=><th className="p-2 font-normal text-muted" key={h} scope="col">{h}</th>)}</tr></thead>
    <tbody>{row.retained_daily_details.map(d=><tr className="border-t border-border" key={d.date}>
     <td className="whitespace-nowrap p-2">{d.date}</td><td className="p-2">{n(d.hits)}/{n(d.n)}</td><td className="p-2">{pct(d.hit_rate)}</td><td className="p-2">{n(d.pending)}</td>
     <td className="p-2">{n(d.early_comparison?.raw_centre_hits)} → {n(d.early_comparison?.corrected_hits)} ({n(d.early_comparison?.calls)} pairs)</td>
     <td className="p-2">{n(d.early_comparison?.correction_helped)} / {n(d.early_comparison?.correction_hurt)}</td><td className="p-2">{n(d.early_comparison?.mean_log_loss_gain,3)}</td>
    </tr>)}</tbody></table></div>
   {!row.retained_daily_details.length&&<p>No retained daily details in this recent cohort.</p>}
  </details>
 </article>;
}

function AuditBrowser({city,checkpoint,flagged,limit}:{city:string;checkpoint:string;flagged:boolean;limit:string}){
 const [cursors,setCursors]=useState<Array<AuditCursor|null>>([null]),[session,setSession]=useState(0);
 const cursor=cursors[cursors.length-1];
 const params=new URLSearchParams({op:'regression-audit',city,checkpoint,flagged:String(flagged),limit});
 if(cursor){params.set('after_city',cursor.city_key);params.set('after_checkpoint',cursor.checkpoint);}
 const url='/api/original-sides?'+params;
 useEffect(()=>{const refresh=()=>{setCursors([null]);setSession(v=>v+1);};window.addEventListener('ad4-private-evidence',refresh);return()=>window.removeEventListener('ad4-private-evidence',refresh);},[]);
 const query=useQuery<{url:string;page:AuditPage}>(async()=>{
  const response=await privateSideRead<AuditPage>(url);
  return {data:response.data?{url,page:response.data}:null,error:response.error};
 },[url,session]);
 // Never render an old page under a new scope, or while rechecking access.
 const page=!query.loading&&!query.error&&query.data?.url===url?query.data.page:null;
 return <div className="space-y-3">
  <button className="text-xs text-accent underline" onClick={()=>{setCursors([null]);query.refresh();}}>Refresh audit from first page</button>
  {query.loading&&<p role="status" className="text-xs text-muted">Reading private audit evidence…</p>}
  {query.error&&<div role="alert" className="space-y-2"><p className="text-xs text-warn">Audit unavailable: {query.error} No empty result or completion was assumed. For a size-limit error, choose a smaller page.</p><PrivateSideAccess error="Owner access for private audit evidence" purpose="audit"/></div>}
  {page&&<>
   <p className="text-xs text-muted">Page {cursors.length} · {page.rows.length} cells · {page.has_more?'more cells remain; this is not the complete scope':'end of this scope'}. Pages read the current source reports; a scoring refresh between pages can change this scope. Refresh from the first page after scoring changes.</p>
   <p className="text-xs text-muted">{page.interpretation}</p>
   {page.rows.map(row=><RegressionAuditCard key={row.city_key+'/'+row.checkpoint} row={row}/>)}
   {!page.rows.length&&<p className="text-xs">No report cells match these filters. This does not establish complete coverage, successful learning or improved accuracy.</p>}
  </>}
  <div className="flex flex-wrap gap-4 text-xs">
   <button className="text-accent underline disabled:opacity-40" disabled={query.loading||cursors.length===1} onClick={()=>setCursors(v=>v.slice(0,-1))}>Previous audit page</button>
   <button className="text-accent underline disabled:opacity-40" disabled={!page?.has_more||!page.next_cursor} onClick={()=>{if(page?.next_cursor)setCursors(v=>[...v,page.next_cursor]);}}>Next audit page</button>
  </div>
 </div>;
}

export default function RegressionAudit({cities}:{cities:string[]}){
 const [open,setOpen]=useState(false),[city,setCity]=useState(''),[checkpoint,setCheckpoint]=useState(''),[flagged,setFlagged]=useState(true),[limit,setLimit]=useState('20');
 return <section className="space-y-3 rounded border border-border bg-panel p-4">
  <h2 className="text-sm font-semibold">Private regression audit</h2>
  <p className="text-xs text-muted">Inspect original served calls, matched market evidence, saved correction pairs and learning gates. Owner access required. Reading this audit does not fit models, change validation gates or alter paper trades.</p>
  <details onToggle={e=>setOpen(e.currentTarget.open)}><summary className="cursor-pointer text-xs text-accent">Open private audit</summary>
   {open&&<div className="mt-3 space-y-3">
    <div className="flex flex-wrap gap-3 text-xs">
     <label>Audit city <select className="input" value={city} onChange={e=>setCity(e.target.value)}><option value="">All cities</option>{[...new Set(cities)].sort().map(c=><option key={c} value={c}>{words(c)}</option>)}</select></label>
     <label>Audit checkpoint <select className="input" value={checkpoint} onChange={e=>setCheckpoint(e.target.value)}><option value="">All checkpoints</option>{AUDIT_HORIZONS.map(([key,label])=><option key={key} value={key}>{label}</option>)}</select></label>
     <label>Audit cells <select className="input" value={String(flagged)} onChange={e=>setFlagged(e.target.value==='true')}><option value="true">Flagged only</option><option value="false">All report cells</option></select></label>
     <label>Cells per page <select className="input" value={limit} onChange={e=>setLimit(e.target.value)}>{['5','10','20','25'].map(x=><option key={x} value={x}>{x}</option>)}</select></label>
    </div>
    <p className="text-xs text-muted">Audit filters are independent of the forecast cards. No background polling; pages load only on opening, filter changes, navigation, access changes or refresh.</p>
    <AuditBrowser key={[city,checkpoint,flagged,limit].join('/')} city={city} checkpoint={checkpoint} flagged={flagged} limit={limit}/>
   </div>}
  </details>
 </section>;
}
