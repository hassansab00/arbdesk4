"use client";
import {createContext,useContext,useEffect,useMemo,useState,type ReactNode} from 'react';
import {operatorFetch} from '../lib/operator';
import {useQuery} from '../lib/useQuery';
import {fmtDateTime} from '../lib/time';
import {predictionLabel,sideEvidenceLabel,sideMoney,SIDE_TIMES,type HistoryIdentity,type OriginalSideCall,
 type SideDesk,type SideHistory,type SideMode,type SideResult,type TradeSide} from '../lib/originalSides';

type Preferences={mode:SideMode;stake:string;account:string;setMode:(v:SideMode)=>void;setStake:(v:string)=>void;setAccount:(v:string)=>void};
const Context=createContext<Preferences>({mode:'recorded',stake:'10',account:'',setMode:()=>{},setStake:()=>{},setAccount:()=>{}});
export function OriginalSidesProvider({children}:{children:ReactNode}){
 const [mode,setMode]=useState<SideMode>('recorded'),[stake,setStake]=useState('10'),[account,setAccount]=useState('');
 return <Context.Provider value={{mode,stake,account,setMode,setStake,setAccount}}>{children}</Context.Provider>;
}
const inFlight=new Map<string,Promise<{data:unknown;error:unknown}>>();
export async function privateSideRead<T>(url:string,body?:unknown):Promise<{data:T|null;error:unknown}>{
 const target=new URL(url,'https://local.invalid');
 if(target.origin!=='https://local.invalid'||target.pathname!=='/api/original-sides')throw new Error('Private session may only reach this site’s original-side API.');
 const key=url+JSON.stringify(body??null);let request=inFlight.get(key);
 if(!request){request=(async()=>{try{
  const response=await operatorFetch(`/api/original-sides${target.search}`,body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{});
  const result=await response.json();return response.ok?result:{data:null,error:result.error??{message:`Private read failed (${response.status}).`}};
 }catch(error){return {data:null,error};}})();inFlight.set(key,request);
 void request.finally(()=>{inFlight.delete(key);});}
 return request as Promise<{data:T|null;error:unknown}>;
}
export function usePrivateSideQuery<T>(url:string,body?:unknown){
 const [session,setSession]=useState(0);const key=JSON.stringify(body??null);
 useEffect(()=>{const refresh=()=>setSession(v=>v+1);window.addEventListener('ad4-private-evidence',refresh);return()=>window.removeEventListener('ad4-private-evidence',refresh);},[]);
 return useQuery<T>(()=>privateSideRead<T>(url,body),[url,key,session],1800000);
}
export function useOriginalSideRows(identities:HistoryIdentity[]){
 const {account,stake}=useContext(Context);
 const bounded=useMemo(()=>identities.slice(0,300),[JSON.stringify(identities)]);
 const query=usePrivateSideQuery<{rows:{row_key:string;call:OriginalSideCall|null;reason:string|null}[]}>('/api/original-sides',
  {op:'resolve',rows:bounded,account:account||null,stake});
 const calls=useMemo(()=>new Map(query.data?.rows.map(x=>[x.row_key,x.call])??[]),[query.data]);
 return {...query,calls,truncated:identities.length>300};
}
export function PrivateSideAccess({error}:{error?:string|null}){
 const [token,setToken]=useState(''),[notice,setNotice]=useState<string|null>(null);
 return <details className="rounded border border-border p-2 text-xs text-muted">
  <summary className="cursor-pointer">{error??'Private paper evidence access'}</summary>
  <p className="my-2">Quotes and paper ledgers require an owner session. The connected operator can install a temporary access receipt; this does not activate a paper policy.</p>
  <form onSubmit={async e=>{e.preventDefault();const result=await privateSideRead<{authorized:boolean}>('/api/original-sides',{op:'session',token});
   if(result.data){setToken('');setNotice('Private evidence unlocked.');window.dispatchEvent(new Event('ad4-private-evidence'));}
   else setNotice((result.error as {message?:string})?.message??'Access receipt was not accepted.');}} className="flex flex-wrap gap-2">
   <input type="password" autoComplete="off" aria-label="Private paper access receipt" className="input" value={token} onChange={e=>setToken(e.target.value)}/>
   <button type="submit" className="rounded border border-border px-2 py-1">Unlock private evidence</button>
  </form>{notice&&<p role="status">{notice}</p>}
 </details>;
}
export function OriginalSideControls(){
 const p=useContext(Context),[draft,setDraft]=useState(p.stake);
 const desk=usePrivateSideQuery<SideDesk>('/api/original-sides?op=desk');
 return <div className="flex flex-wrap items-center gap-2 text-xs">
  <label>Trade result mode <select aria-label="Original bucket trade result mode" className="input" value={p.mode} onChange={e=>p.setMode(e.target.value as SideMode)}>
   <option value="recorded">Recorded paper trades</option><option value="hypothetical">Hypothetical comparison</option></select></label>
  <label>Account <select aria-label="Original bucket paper account" className="input" value={p.account} onChange={e=>p.setAccount(e.target.value)}>
   <option value="">All owned positions · shown separately</option>{desk.data?.policies.filter(x=>x.account).map(x=><option key={x.account!.account_id} value={x.account!.account_id}>{x.account!.account_id.slice(0,8)} · {x.state}</option>)}
  </select></label>
  {p.mode==='hypothetical'&&<form onSubmit={e=>{e.preventDefault();if(/^\d+(\.\d{1,6})?$/.test(draft)&&Number(draft)>0&&Number(draft)<=1000000)p.setStake(draft);}}>
   <label>Independent stake per side $<input aria-label="Hypothetical stake per side" className="input w-20" inputMode="decimal" value={draft} onChange={e=>setDraft(e.target.value)}/></label>
   <button type="submit" className="ml-1 underline">Apply</button>
  </form>}
  {p.mode==='hypothetical'&&<span className="text-muted">Each column uses ${p.stake}; they are not one portfolio allocation.</span>}
 </div>;
}
function SideCard({call,side,mode}:{call:OriginalSideCall;side:TradeSide;mode:SideMode}){
 const result=call.sides[side],e=result.evaluation;
 return <div className="min-w-48 rounded border border-border p-2 text-[11px]">
  <p className="font-medium">Buy {side} · {call.predicted_band_label}</p>
  <p>{result.side_outcome.replaceAll('_',' ')} · {sideEvidenceLabel(result)}</p>
  {!!result.quote_attempts?.length&&<details><summary className="cursor-pointer">{result.quote_attempts.length} retained quote attempts{result.attempts_truncated?' · more retained':''}</summary>
   <p>Comparison uses the first executable direct quote for this stake.</p>{result.quote_attempts.map(a=><p key={a.attempt_id}>{a.received_at} · {a.status} · {a.reason??a.attempt_id}</p>)}</details>}
  {mode==='hypothetical'?<>
   <p className="font-medium">Hypothetical comparison · ${result.comparison_stake} per side</p>
   <p>Own best ask / modeled VWAP: {e?.best_ask??'Not recorded'} / {e?.vwap??'Not recorded'}</p>
   <p>Quote received: {result.quote_received_at?fmtDateTime(result.quote_received_at,call.timezone):'Not recorded'}</p>
   <p>Book observed: {result.book_observed_at?fmtDateTime(result.book_observed_at,call.timezone):'Not recorded'}</p>
   <p>Filled cost: {sideMoney(e?.filled_cost)} · shares {e?.shares??'Not recorded'} · fees {sideMoney(e?.fee)}</p>
   <p>Unspent allocation: {sideMoney(e?.unspent_allocation)}</p>
   <p>Profit if this side wins: {sideMoney(e?.profit_if_win)}</p><p>Loss if this side loses: {sideMoney(e?.loss_if_lose)}</p>
   <p className="font-medium">Hypothetical settled P&amp;L: {result.hypothetical_net_pnl==null?(result.quote_kind==='missing'?'Not recorded':result.reason??'Pending / unverified settlement'):sideMoney(result.hypothetical_net_pnl)}</p>
   {result.reason&&<p className="text-warn">Blocked: {result.reason}</p>}
  </>:result.paper_orders.length?result.paper_orders.map(order=><div key={order.order_id} className="mt-2 border-t border-border pt-1">
   <a className="underline" href={order.source_kind==='recorded_v1_ledger'?`/paper-trades?paperAccount=${order.account_id}`:`/paper-trades?originalAccount=${order.account_id}&call=${call.call_id}#original-side-ledger`}>Paper ledger · {order.account_id.slice(0,8)} / {order.order_id.slice(0,8)}</a>
   <p>{order.status} · {order.paper_outcome.replaceAll('_',' ')}{order.reason?` · ${order.reason}`:''}</p>
   <p>Requested ${order.requested_stake} · cost {order.notional==null?'No fill':sideMoney(order.notional)} · shares {order.shares??'No fill'} · fees {order.fee==null?'No fill':sideMoney(order.fee)}</p>
   <p>Recorded fill: {order.recorded_fill_at?fmtDateTime(order.recorded_fill_at,call.timezone):'No fill'}</p>
   <p>Recorded fill VWAP: {order.fill_vwap??'No fill'}</p>
   {order.evidence_note&&<p className="text-muted">{order.evidence_note}</p>}
   <p className="font-medium">Recorded paper P&amp;L: {order.paper_net_pnl==null?(['filled','partial'].includes(order.status)?'Pending / unverified settlement':'No fill'):sideMoney(order.paper_net_pnl)}</p>
   <details><summary className="cursor-pointer">Receipt links</summary><p>Call {call.call_id}</p><p>Allocation {order.allocation_id}</p><p>Decision {order.decision_id}</p><p>Order {order.order_id}</p><p>Fill book {order.fill_snapshot_id??'Not recorded'}</p><p>Settlement {call.settlement_receipt_id??'Pending'}</p></details>
  </div>):<p className="my-2">No recorded order or fill. {result.quote_kind==='direct'?'A quote exists; switch to Hypothetical comparison to inspect it.':'Side execution evidence: Not recorded.'}</p>}
 </div>;
}
export function OriginalSideCell({call,side,loading,error}:{call:OriginalSideCall|null|undefined;side:TradeSide;loading?:boolean;error?:string|null}){
 const {mode}=useContext(Context);
 if(!call)return <span className="text-muted">{loading?'Loading private evidence…':error?'Private evidence unavailable':'Not recorded · no proven original-call link'}</span>;
 return <details><summary className="cursor-pointer">{side} buy · {call.sides[side].side_outcome.replaceAll('_',' ')} · {mode==='recorded'?`${call.sides[side].paper_orders.length} recorded orders`:sideMoney(call.sides[side].hypothetical_net_pnl)}</summary>
  <SideCard call={call} side={side} mode={mode}/></details>;
}
export function OriginalSideCallCard({call}:{call:OriginalSideCall}){
 const {mode}=useContext(Context);
 return <article id={'side-call-'+call.call_id} className="rounded border border-border bg-panel p-3">
  <p className="text-xs"><b>{call.city_key} · {call.target_date} · {call.source_kind==='day_max_day_ahead_calls'?'Legacy day ahead · last pre-midnight receipt':SIDE_TIMES[call.checkpoint]??call.checkpoint}</b></p>
  <p className="text-xs">Original predicted bucket: <strong>{call.predicted_band_label}</strong> · Prediction: <strong>{predictionLabel(call.prediction_outcome)}</strong></p>
  <p className="text-xs">Verified winning bucket: {call.actual_winning_band_label??'Pending / unavailable verified ladder'}</p>
  <p className="text-[11px] text-muted">Frozen {fmtDateTime(call.frozen_at,call.timezone)} · {call.station} · {call.engine_version}</p>
  <p className="text-[11px] text-muted">Trade result on the original predicted bucket. A winning NO position never changes the prediction hit count.</p>
  <div className="mt-2 grid gap-2 sm:grid-cols-2"><SideCard call={call} side="YES" mode={mode}/><SideCard call={call} side="NO" mode={mode}/></div>
  <details className="mt-1 text-[10px] text-muted"><summary className="cursor-pointer">Original call and settlement provenance</summary>
   <p>Call {call.call_id} · {call.source_kind} · entry eligibility {call.eligibility}</p><p>Input as of {call.input_as_of??'Not recorded'} · hash {call.input_sha256??'Not recorded'}</p>
   <p>Rule receipt {call.rule_receipt_id??'Saved dated market receipt'} · hash {call.rules_sha256??'Unverified'}</p><p>Ladder {call.ladder_sha256}</p>
   <p>Settlement {call.settlement_receipt_id??'Pending'} · {call.settlement_state}</p>
  </details>
 </article>;
}
export function OriginalSideHistory({cities,checkpoint,account:accountOverride}:{cities?:string[];checkpoint?:string;account?:string}){
 const preferences=useContext(Context),[offset,setOffset]=useState(0),[days,setDays]=useState(7);
 const [to,setTo]=useState(()=>new Date(Date.now()+2*86400000).toISOString().slice(0,10));
 const [from,setFrom]=useState(()=>new Date(Date.now()-6*86400000).toISOString().slice(0,10));
 const params=new URLSearchParams({from,to,stake:preferences.stake,offset:String(offset)});
 const account=accountOverride??preferences.account;
 if(account)params.set('account',account);if(cities?.length)params.set('cities',cities.join(','));if(checkpoint)params.set('checkpoints',checkpoint);
 const query=usePrivateSideQuery<SideHistory>('/api/original-sides?'+params);
 useEffect(()=>setOffset(0),[cities?.join(','),checkpoint,account,days,from,to]);
 return <div className="space-y-2">
  <p className="text-xs font-medium">Evidence-linked original checkpoint calls · pending and settled</p>
  <p className="text-[11px] text-muted">Native Day ahead is D−1 09:00 local. The legacy last-pre-midnight history above remains a separate cohort. These trade rows do not alter its accuracy totals.</p>
  <OriginalSideControls/>
  {query.data?.capacity?.capture_allowed===false&&<p className="text-xs text-warn">New quote capture is held at the private evidence storage limit. Retained evidence and settlement recovery remain available.</p>}
  <div className="flex flex-wrap gap-3 text-xs"><label>Past dates + upcoming local targets <select className="input" value={days} onChange={e=>{const n=Number(e.target.value);setDays(n);setFrom(new Date(Date.now()-(n-1)*86400000).toISOString().slice(0,10));setTo(new Date(Date.now()+2*86400000).toISOString().slice(0,10));}}><option value={7}>7 past days</option><option value={14}>14 past days</option><option value={30}>30 past days</option></select></label>
   <label>Target from <input aria-label="Side evidence target from" className="input" type="date" value={from} onChange={e=>setFrom(e.target.value)}/></label>
   <label>to <input aria-label="Side evidence target to" className="input" type="date" value={to} onChange={e=>setTo(e.target.value)}/></label>
   <button className="underline" onClick={query.refresh}>Refresh side evidence</button>
   <button className="underline" onClick={async()=>{const response=await operatorFetch('/api/original-sides?'+params.toString()+'&op=export');if(!response.ok)return;
    const url=URL.createObjectURL(await response.blob());const a=document.createElement('a');a.href=url;a.download='original-bucket-sides.json';a.click();URL.revokeObjectURL(url);}}>Export this page</button>
  </div>
  {query.error?<PrivateSideAccess error={query.error}/>:query.loading?<p className="text-xs text-muted">Loading private side evidence…</p>:<>
   <p className="text-xs text-muted">{query.data?.total_calls??0} original calls in this scope. Separate YES/NO evidence; each prediction counts once. Refreshes every 30 minutes while visible.</p>
   {query.data?.page_summary&&<p className="text-xs text-muted">This page: {query.data.page_summary.quote_covered_calls}/{query.data.page_summary.calls} calls with executable quotes · {query.data.page_summary.both_side_quote_calls} with both sides · {query.data.page_summary.attempted_orders} recorded orders · {query.data.page_summary.filled_positions} filled positions · {query.data.page_summary.resolved_positions} resolved · {query.data.page_summary.pending_positions} pending. {account?`Selected-account recorded settled P&L on this page: ${sideMoney(query.data.page_summary.recorded_net_pnl)}.`:'Select an account for compatible recorded P&L totals.'}</p>}
   {query.data?.rows.map(call=><OriginalSideCallCard key={call.call_id} call={call}/>)}
   {!query.data?.rows.length&&<p className="text-xs text-muted">No retained side receipts in this window. Earlier calls are not reconstructed as trades.</p>}
   <div className="flex gap-3 text-xs"><button disabled={!offset} className="underline disabled:opacity-40" onClick={()=>setOffset(v=>Math.max(0,v-100))}>Previous page</button>
    {query.data?.truncated&&<><span className="text-warn">More rows exist; this page is incomplete.</span><button className="underline" onClick={()=>setOffset(v=>v+100)}>Next page</button></>}
   </div>
  </>}
 </div>;
}

export function ExactOriginalSideCall({callId,account}:{callId:string;account?:string}){
 const p=useContext(Context),params=new URLSearchParams({op:'call',call:callId,stake:p.stake});
 if(account)params.set('account',account);
 const query=usePrivateSideQuery<OriginalSideCall>('/api/original-sides?'+params);
 return query.error?<PrivateSideAccess error={query.error}/>:query.data?<OriginalSideCallCard call={query.data}/>:<p className="text-xs">{query.loading?'Reading exact source call…':'Original-call link unavailable.'}</p>;
}
