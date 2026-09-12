"use client";

import { useState } from 'react';
import { paperClient, runPaperWorker } from '@/lib/paperSupabase';
import { supabase } from '@/lib/supabase';
import { useQuery } from '@/lib/useQuery';
import PaperExitPolicy from '@/components/PaperExitPolicy';

type Policy = {strategies?:string[];cities?:string[];max_plan_usd?:number;max_exposure_usd?:number;min_edge?:number;auto_exit_enabled?:boolean;take_profit_fraction?:number;stop_loss_fraction?:number};
type Plan = {plan_id:string;strategy_id:string;status:string;reason:string;expires_at:string;legs:unknown;evidence:unknown};
const button='rounded border border-border px-3 py-1 text-sm hover:bg-panel2 disabled:opacity-40';

export default function PaperAutomation({account,refresh}:{account:{account_id:string;mode:string;entries_paused:boolean;policy:Policy};refresh:()=>void}) {
  const [mode,setMode]=useState(account.mode);
  const [paused,setPaused]=useState(account.entries_paused);
  const [policy,setPolicy]=useState<Policy>({strategies:[],cities:[],max_plan_usd:0,max_exposure_usd:0,min_edge:0.03,...account.policy});
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState<string|null>(null);
  const [saved,setSaved]=useState(false);
  const strategies=useQuery<{strategy_id:string;name:string;enabled:boolean}[]>(()=>supabase.from('strategies').select('strategy_id,name,enabled').order('strategy_id'),[]);
  const cities=useQuery<{city_key:string}[]>(()=>supabase.from('cities').select('city_key').order('city_key'),[]);
  const plans=useQuery<Plan[]>(()=>paperClient().from('paper_trade_plans').select('*').eq('account_id',account.account_id).order('created_at',{ascending:false}).limit(100),[account.account_id],15000,100);
  async function act(fn:()=>PromiseLike<{error:unknown}>, wakeWorker=false) {
    setBusy(true);setError(null);setSaved(false);
    try {const r=await fn();if(r.error)throw r.error;if(wakeWorker)await runPaperWorker();plans.refresh();refresh();setSaved(true);}
    catch(e){setError(e&&typeof e==='object'&&'message' in e?String(e.message):String(e));}
    finally{setBusy(false);}
  }
  function toggle(field:'strategies'|'cities',id:string){setSaved(false);setPolicy({...policy,[field]:policy[field]?.includes(id)?policy[field]?.filter(x=>x!==id):[...(policy[field]||[]),id]});}
  return <div className="space-y-4 rounded border border-border bg-panel p-4">
    <h2 className="font-semibold">Paper trading policy</h2>
    <p className="text-sm text-muted">Manual orders use your ticket. Assisted mode proposes trades for approval. Automatic mode queues eligible proposals within the limits below. Every proposal records its evidence and any reason it was blocked.</p>
    {(error||plans.error||strategies.error||cities.error)&&<p role="alert" className="text-sm text-bad">{error||plans.error||strategies.error||cities.error}</p>}
    {saved&&<p role="status" className="text-sm text-good">Saved.</p>}
    <form onSubmit={e=>{e.preventDefault();act(()=>paperClient().rpc('set_paper_policy',{p_account:account.account_id,p_mode:mode,p_paused:paused,p_policy:policy}));}}>
      <fieldset disabled={busy} className="space-y-3">
        <label className="block text-sm">Mode<select className="input mt-1" value={mode} onChange={e=>setMode(e.target.value)}><option value="manual">Manual</option><option value="assisted">Assisted — approve each plan</option><option value="automatic">Automatic — within policy limits</option></select></label>
        <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={paused} onChange={e=>setPaused(e.target.checked)}/>Pause new automatic entries</label>
        <p className="text-xs text-muted">Pausing prevents new automatic plans. Already queued orders can be canceled in Orders; exits remain available.</p>
        <div className="grid gap-3 sm:grid-cols-3">{(['max_plan_usd','max_exposure_usd','min_edge'] as const).map(field=><label key={field} className="text-sm">{{max_plan_usd:'Maximum plan cost (USD)',max_exposure_usd:'Maximum open cost + reservations (USD)',min_edge:'Minimum net edge per share (USD)'}[field]}<input required className="input mt-1" type="number" min={field==='min_edge'?0:1} max={field==='min_edge'?1:1000000} step="0.01" value={policy[field]??''} onChange={e=>setPolicy({...policy,[field]:Number(e.target.value)})}/></label>)}</div>
        <div><p className="mb-2 text-sm">Allowed strategies</p><div className="grid gap-2 md:grid-cols-2">{strategies.data?.map(s=><label className="flex gap-2 text-sm" key={s.strategy_id}><input type="checkbox" checked={policy.strategies?.includes(s.strategy_id)||false} onChange={()=>toggle('strategies',s.strategy_id)}/>{s.name}{!s.enabled&&<span className="text-muted">(disabled globally)</span>}</label>)}</div></div>
        <div><p className="mb-2 text-sm">Allowed cities</p><div className="flex max-h-40 flex-wrap gap-3 overflow-auto">{['ALL',...(cities.data||[]).map(c=>c.city_key)].map(c=><label className="flex gap-2 text-sm" key={c}><input type="checkbox" checked={policy.cities?.includes(c)||false} onChange={()=>toggle('cities',c)}/>{c==='ALL'?'All cities':c}</label>)}</div></div>
        <button className={button}>Save policy</button>
      </fieldset>
    </form>
    <PaperExitPolicy account={account.account_id} policy={account.policy} refresh={refresh}/>
    <h3 className="border-t border-border pt-4 font-semibold">Strategy proposals</h3>
    {!plans.data?.length&&<p className="text-sm text-muted">No proposals yet. Proposals appear when an enabled strategy fires and your account is in assisted or automatic mode.</p>}
    {plans.data?.map(p=><div key={p.plan_id} className="space-y-2 border-b border-border pb-3 text-sm"><div>{p.strategy_id} · {p.status}</div><p className="text-muted">{p.reason}</p><p className="text-xs text-muted">Expires {new Date(p.expires_at).toLocaleString()}</p>
      {p.status==='pending_approval'&&<div className="flex gap-2"><button disabled={busy||Date.parse(p.expires_at)<=Date.now()} className={button} onClick={()=>act(()=>paperClient().rpc('approve_paper_plan',{p_plan:p.plan_id,p_approve:true}),true)}>Approve paper plan</button><button disabled={busy} className={button} onClick={()=>act(()=>paperClient().rpc('approve_paper_plan',{p_plan:p.plan_id,p_approve:false}))}>Reject</button></div>}
      <details><summary className="cursor-pointer text-muted">Legs and evidence</summary><pre className="overflow-auto text-xs">{JSON.stringify({legs:p.legs,evidence:p.evidence},null,2)}</pre></details></div>)}
    {plans.truncated&&<p className="text-xs text-warn">Showing the latest 100 proposals; older history is retained.</p>}
  </div>;
}
