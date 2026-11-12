"use client";
import {useEffect,useState} from 'react';
import {MIXED_SIDE_PRESET,SIDE_TIMES,sideMoney,type SideDesk,type SidePolicyConfig,type TradeSide} from '../lib/originalSides';
import {ExactOriginalSideCall,OriginalSideHistory,OriginalSidesProvider,PrivateSideAccess,privateSideRead,usePrivateSideQuery} from './OriginalSideResults';

type Ledger={entries:{entry_id:string;event_type:string;call_id:string|null;order_id:string|null;settlement_id:string|null;
 cash_delta:string;reserved_delta:string;recorded_at:string}[];
 allocation_plans:{plan_id:string;target_date:string;frozen_at:string;available_settled_cash:string;allocation_per_slot:string;rounding_remainder:string;
  slots:{allocation_id:string;city_key:string;checkpoint:string;side:TradeSide;reserved_amount:string;order_id:string|null;call_id:string|null;status:string}[]}[];
 stage_events:{stage:string;reason:string;call_id:string|null;recorded_at:string}[];next_before:string|null;truncated:boolean};
function PrivateLedger({account}:{account:string}){
 const [before,setBefore]=useState('');useEffect(()=>setBefore(''),[account]);
 const query=usePrivateSideQuery<Ledger>(`/api/original-sides?op=ledger&account=${account}${before?'&before='+before:''}`);
 return <div id="original-side-ledger" className="scroll-mt-20 space-y-2">
  <h3 className="text-sm font-medium">Original bucket paper ledger</h3>
  {query.error?<p className="text-xs text-bad">{query.error}</p>:<>
   {query.data?.allocation_plans.map(plan=><details key={plan.plan_id} className="rounded border border-border p-2 text-xs">
    <summary className="cursor-pointer">{plan.target_date} · frozen allocation ${plan.allocation_per_slot} per slot · available settled cash ${plan.available_settled_cash}</summary>
    <p>Plan {plan.plan_id} · frozen {plan.frozen_at} · rounding cash remainder ${plan.rounding_remainder}</p>
    <div className="overflow-x-auto"><table className="w-full text-[11px]"><thead><tr><th>City / checkpoint</th><th>Side on original bucket</th><th>Reserved</th><th>Execution</th></tr></thead>
     <tbody>{plan.slots.map(slot=><tr key={slot.allocation_id}><td>{slot.city_key} · {SIDE_TIMES[slot.checkpoint]}</td><td>{slot.side}</td><td>${slot.reserved_amount}</td><td>{slot.status} · {slot.order_id??'No recorded order'}</td></tr>)}</tbody></table></div>
   </details>)}
   <div className="overflow-x-auto rounded border border-border"><table className="w-full text-[11px]"><thead><tr><th>Recorded UTC</th><th>Ledger event</th><th>Cash change</th><th>Reservation change</th><th>Source / fill / settlement</th></tr></thead>
    <tbody>{query.data?.entries.map(entry=><tr key={entry.entry_id} className="border-t border-border"><td>{entry.recorded_at}</td><td>{entry.event_type}</td><td>{sideMoney(entry.cash_delta)}</td><td>{sideMoney(entry.reserved_delta)}</td>
     <td><details><summary className="cursor-pointer">Evidence links</summary><p>Call {entry.call_id??'Not applicable'}</p><p>Order {entry.order_id??'Not applicable'}</p><p>Settlement {entry.settlement_id??'Not recorded'}</p></details></td></tr>)}</tbody></table></div>
   {query.data?.truncated&&<div className="flex gap-2 text-xs text-warn">Older ledger entries remain.<button className="underline" onClick={()=>setBefore(query.data?.next_before??'')}>Read older page</button></div>}
   {before&&<button className="text-xs underline" onClick={()=>setBefore('')}>Latest ledger page</button>}
   {!!query.data?.stage_events.length&&<details className="text-xs"><summary className="cursor-pointer">Retained blocked / deferred execution stages</summary>{query.data.stage_events.map((e,i)=><p key={i}>{e.recorded_at} · {e.stage} · {e.reason} · call {e.call_id??'Not recorded'}</p>)}</details>}
  </>}
 </div>;
}
export default function OriginalSideDesk(){
 const query=usePrivateSideQuery<SideDesk&{truncated?:boolean}>('/api/original-sides?op=desk');
 const [draft,setDraft]=useState<SidePolicyConfig>(()=>JSON.parse(JSON.stringify(MIXED_SIDE_PRESET)));
 const [account,setAccount]=useState(''),[callId,setCallId]=useState(''),[notice,setNotice]=useState(''),[busy,setBusy]=useState(false);
 useEffect(()=>{const p=new URLSearchParams(window.location.search),id=p.get('originalAccount');if(id)setAccount(id);setCallId(p.get('call')??'');},[]);
 const changeSlot=(i:number,field:'city_key'|'side'|'checkpoint',value:string)=>setDraft(p=>({...p,slots:p.slots.map((x,n)=>n===i?{...x,[field]:value}:x)}));
 const command=async(body:unknown)=>{setBusy(true);try{const result=await privateSideRead('/api/original-sides',body);
  setNotice(result.error?(result.error as {message?:string})?.message??'Paper command failed.':'Paper policy receipt recorded. Saving remains inactive; activation is a separate prospective action.');
  if(result.data){query.refresh();window.dispatchEvent(new Event('ad4-private-evidence'));}
 }finally{setBusy(false);}};
 const selected=query.data?.policies.find(p=>p.account?.account_id===account);
 return <OriginalSidesProvider><section className="space-y-3 rounded border border-border bg-panel p-4">
  <h2 className="text-sm font-semibold">Day Max · original bucket YES / NO · paper v2</h2>
  <p className="text-xs text-muted">Both quote sides are observed on the unchanged frozen pick. Only an explicitly activated side policy executes. Existing fixed-$10 v1 choices above keep their separate settings.</p>
  {query.error&&<PrivateSideAccess error={query.error}/>}
  {query.data?.capacity?.capture_allowed===false&&<p className="text-xs text-warn">New side capture is held at its private evidence storage limit. Existing receipts and independent settlement recovery remain available.</p>}
  <details className="rounded border border-border p-3 text-xs"><summary className="cursor-pointer">Editable mixed research preset · initially inactive · one $100 paper bankroll</summary>
   <p className="my-2 text-muted">Fixed city/side selection, D−1 18:00 local by default. Eight equal all-in slots initially reserve $12.50 each. Reinvestment uses available settled cash after reservations; missing or disputed payouts remain unavailable. No external top-ups.</p>
   <label>Funding <select className="input" value={draft.funding} onChange={e=>setDraft(p=>({...p,funding:e.target.value as SidePolicyConfig['funding']}))}>
    <option value="settled_equal_compound">Equal split / compound settled cash</option><option value="fixed">Fixed stake per configured slot</option></select></label>
   {draft.funding==='fixed'&&<label> Fixed stake $<input aria-label="v2 fixed stake" className="input w-20" value={draft.fixed_stake} onChange={e=>setDraft(p=>({...p,fixed_stake:e.target.value}))}/></label>}
   <div className="my-2 overflow-x-auto"><table className="w-full"><thead><tr><th>Fixed city key</th><th>Buy side</th><th>Original checkpoint</th></tr></thead><tbody>{draft.slots.map((slot,i)=><tr key={i}>
    <td><input aria-label={`City slot ${i+1}`} className="input" value={slot.city_key} onChange={e=>changeSlot(i,'city_key',e.target.value)}/></td>
    <td><select aria-label={`Side slot ${i+1}`} className="input" value={slot.side} onChange={e=>changeSlot(i,'side',e.target.value)}><option>YES</option><option>NO</option></select></td>
    <td><select aria-label={`Checkpoint slot ${i+1}`} className="input" value={slot.checkpoint} onChange={e=>changeSlot(i,'checkpoint',e.target.value)}>{Object.entries(SIDE_TIMES).map(([key,label])=><option key={key} value={key}>{label}</option>)}</select></td>
   </tr>)}</tbody></table></div>
   <button disabled={busy} className="rounded border border-border px-3 py-1" onClick={()=>command({op:'save',config:draft})}>Save inactive research policy</button>
  </details>
  {notice&&<p role="status" className="text-xs">{notice}</p>}
  {query.data?.truncated&&<p className="text-xs text-warn">Only the latest 20 owned policies are listed; older policies remain retained.</p>}
  {query.data?.policies.map(policy=><div key={policy.policy_id} className="rounded border border-border p-2 text-xs">
   <p><b>{policy.state}</b> · policy {policy.policy_id} · {policy.config.funding} · immutable city/side selection</p>
   <p>{policy.config.slots.map(slot=>`${slot.city_key} ${slot.side} (${SIDE_TIMES[slot.checkpoint]})`).join(' · ')}</p>
   {policy.account?<>
    <p>Activated {policy.account.activated_at} · settled cash ${policy.account.cash} · reserved ${policy.account.reserved_cash}</p>
    <p>Recorded resolved paper P&amp;L {sideMoney(policy.account.recorded_pnl)} · {policy.account.pending_positions} pending positions{policy.account.pending_positions?' · portfolio result incomplete':''}</p>
    <div className="flex flex-wrap gap-3"><button disabled={busy} className="underline" onClick={()=>command({op:'state',policy_id:policy.policy_id,action:policy.state==='pause'?'resume':'pause'})}>{policy.state==='pause'?'Resume prospective paper entries':'Pause new paper entries'}</button>
     <button className="underline" onClick={()=>setAccount(policy.account!.account_id)}>Open private ledger and exact calls</button></div>
   </>:<p className="mt-2">No funded account or historical trades. <button disabled={busy} className="underline" onClick={()=>command({op:'state',policy_id:policy.policy_id,action:'activate'})}>Activate this prospective $100 paper policy from now</button></p>}
  </div>)}
  {!query.error&&!query.loading&&!query.data?.policies.length&&<p className="text-xs text-muted">No v2 policy has been saved or activated. The preset is a research configuration, not a validated city selector.</p>}
  {account&&<><p className="text-xs">Selected private account {account}{selected?'':' · ownership checked on every read'}</p>{callId&&<ExactOriginalSideCall callId={callId} account={account}/>}<PrivateLedger account={account}/><OriginalSideHistory account={account}/></>}
 </section></OriginalSidesProvider>;
}
