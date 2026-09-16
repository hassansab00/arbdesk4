"use client";

import { useEffect, useState } from 'react';
import Link from 'next/link';
import PaperAutomation from '@/components/PaperAutomation';
import PaperExit from '@/components/PaperExit';
import PaperDeskControl from '@/components/PaperDeskControl';
import PaperPipelineStatus from '@/components/PaperPipelineStatus';
import PaperTradeHistory from '@/components/PaperTradeHistory';
import { paperAction, paperRead, runPaperWorker } from '@/lib/paperSupabase';
import { supabase } from '@/lib/supabase';
import { useQuery } from '@/lib/useQuery';
import { fmtUsd, fmtPrice } from '@/lib/format';

type Account = { account_id:string; name:string; cash:number; reserved_cash:number; mode:string; entries_paused:boolean; policy:{strategies?:string[];cities?:string[];max_plan_usd?:number;max_exposure_usd?:number;min_edge?:number}; policy_version:number;
  // From v_paper_desk_activity. Absent when the desk list comes back through
  // the edge gateway rather than the service key, so every use tolerates it.
  trade_count?:number; open_positions?:number; live?:boolean };
type Order = { order_id:string; band_id:string; side:string; action:string; origin:string; shares:number; limit_price:number; status:string; reason:string|null; requested_at:string; result:Record<string,unknown>|null };
type Position = { band_id:string; side:string; shares:number; cost_basis:number; realized_pnl:number };
type Event = { event_id:number; event_type:string; occurred_at:string; cash_delta:number; payload:Record<string,unknown> };
type Band = { band_id:string; band_label:string; markets:{ city_key:string; resolution_date:string; unit:string } };
const button = 'rounded border border-border px-3 py-1 text-sm hover:bg-panel2 disabled:opacity-40';
const card = 'rounded border border-border bg-panel p-4';

export default function PaperTradesPage() {
  const [error,setError] = useState<string|null>(null);
  const [notice,setNotice] = useState<string|null>(null);
  const [noticeWarning,setNoticeWarning] = useState(false);
  const [busy,setBusy] = useState(false);
  const [account,setAccount] = useState('');
  const [tab,setTab] = useState('Trades');
  const [starting,setStarting] = useState('');
  const [showNewDesk,setShowNewDesk] = useState(false);
  const [newDesk,setNewDesk] = useState({name:'',cash:''});
  const [ticket,setTicket] = useState({band:'',side:'YES',shares:'',limit:'',ceiling:'',reason:''});
  // Stable across retry after an uncertain network response; reset only after success.
  const [command,setCommand] = useState<string|null>(null);
  useEffect(()=>{if(window.location.hash==='#automation')setTab('Settings');},[]);
  const accounts=useQuery<Account[]>(async()=>{
    return paperRead<Account[]>('accounts');
  },[],15000);
  // OPEN ON THE DESK THAT IS TRADING, not the one created first.
  //
  // This was `accounts.data[0]` over a list ordered by created_at. On 16 Sep
  // that was "Main paper account" - manual, paused, no trades, no positions -
  // while "Wide edge, all US" held 10 trades and 9 open positions. So the page
  // opened on an empty desk every time, with nothing saying a live one existed.
  //
  // Most open positions first, then most trades, then a desk that CAN act, and
  // creation order last. Every term degrades to the old behaviour when the
  // counts are absent, which they are on the edge-gateway path.
  useEffect(()=>{
    if(account || !accounts.data?.length) return;
    const best=[...accounts.data].sort((a,b)=>
      (b.open_positions??0)-(a.open_positions??0)
      || (b.trade_count??0)-(a.trade_count??0)
      || Number(b.live??false)-Number(a.live??false));
    setAccount(best[0].account_id);
  },[accounts.data,account]);
  const orders=useQuery<Order[]>(async()=>{
    if(!account) return {data:[],error:null};
    return paperRead<Order[]>('orders',account);
  },[account],15000,100);
  const positions=useQuery<Position[]>(async()=>{
    if(!account) return {data:[],error:null};
    return paperRead<Position[]>('positions',account);
  },[account],15000,500);
  const events=useQuery<Event[]>(async()=>{
    if(!account) return {data:[],error:null};
    return paperRead<Event[]>('activity',account);
  },[account],15000,100);
  const bands=useQuery<Band[]>(async()=>{
    const result=await supabase.from('bands').select('band_id,band_label,markets!inner(city_key,resolution_date,unit)')
      .eq('markets.closed',false).gte('markets.resolution_date',new Date().toISOString().slice(0,10))
      .order('band_id').limit(1000);
    return {data:result.data as unknown as Band[],error:result.error};
  },[],60000,1000);
  const selected=accounts.data?.find(a=>a.account_id===account);
  const name=(id:string)=>{const b=bands.data?.find(x=>x.band_id===id);return b?`${b.markets.city_key} · ${b.band_label} · ${b.markets.resolution_date}`:id;};
  const refresh=()=>{accounts.refresh();orders.refresh();positions.refresh();events.refresh();};
  async function act(fn:()=>PromiseLike<{error:unknown}>) {
    setBusy(true);setError(null);setNotice(null);setNoticeWarning(false);
    try {const r=await fn();if(r.error) throw r.error;refresh();return true;}
    catch(e){setError(e&&typeof e==='object'&&'message' in e?String(e.message):String(e));return false;}
    finally{setBusy(false);}
  }
  async function submit(){
    const id=command??crypto.randomUUID();setCommand(id);
    const ok=await act(()=>paperAction('submit_order',{p_account:account,p_command:id,p_band:ticket.band,
      p_side:ticket.side,p_shares:Number(ticket.shares),p_limit:Number(ticket.limit),p_cash_ceiling:Number(ticket.ceiling),p_reason:ticket.reason}));
    if(ok){
      setCommand(null);setTicket({...ticket,shares:'',ceiling:''});
      setBusy(true);
      try {
        const result=await runPaperWorker();
        refresh();
        setNoticeWarning(false);
        setNotice(result.orders_completed
          ? 'Order processed against a fresh verified book. See Orders for the fill result.'
          : 'Order queued; the worker found no claimable order yet. Refresh shortly.');
      } catch(e) {
        setNoticeWarning(true);
        setNotice(e&&typeof e==='object'&&'message' in e?String(e.message):'Order queued; worker wake-up failed.');
      } finally {
        setBusy(false);
      }
    }
  }
  const issue=error||accounts.error||orders.error||positions.error||events.error||bands.error;
  return <div className="space-y-4 p-4">
    <div className="flex flex-wrap items-center justify-between gap-3"><div><h1 className="text-lg font-semibold">Paper Trades</h1>
      <p className="text-sm text-muted">Real market data · Simulated orders · Traceable decisions</p></div>
      <button className={button} onClick={refresh}>Refresh</button></div>
    {issue&&<div role="alert" className="rounded border border-bad p-3 text-sm text-bad">{issue}</div>}
    {notice&&<div role="status" className={`text-sm ${noticeWarning?'text-warn':'text-good'}`}>{notice}</div>}
    <div className="flex flex-wrap items-center gap-3"><select disabled={busy} aria-label="Paper account" className="input max-w-sm" value={account} onChange={e=>{setAccount(e.target.value);setCommand(null);}}>
      <option value="">Paper desk</option>{accounts.data?.map(a=><option key={a.account_id} value={a.account_id}>{a.name} — {a.mode}{a.entries_paused?' · paused':''}{a.open_positions?` · ${a.open_positions} open`:''}{a.trade_count?` · ${a.trade_count} trades`:''}</option>)}</select>
      {/* Several desks, so a setting can be tried without disturbing the one
          already running. Each carries its own cash, strategies and cities -
          the Settings tab edits them per desk. */}
      {!!accounts.data?.length&&<button type="button" disabled={busy} className={button}
        onClick={()=>{setShowNewDesk(v=>!v);setNewDesk({name:'',cash:''});}}>
        {showNewDesk?'Cancel':'New desk'}</button>}
      {accounts.data&&accounts.data.length>1&&<span className="text-xs text-muted">
        {accounts.data.length} desks · each runs independently</span>}</div>

      {!accounts.loading&&!accounts.data?.length&&<form className={`${card} max-w-lg space-y-3`} onSubmit={e=>{e.preventDefault();act(()=>paperAction('create_account',{p_name:'Main paper account',p_starting_cash:Number(starting)}));}}>
        <h2 className="font-semibold">Create paper account</h2><label className="block text-sm">Starting paper cash (USD)<input required type="number" min="0.01" step="0.01" className="input mt-1" value={starting} onChange={e=>setStarting(e.target.value)}/></label>
        <button disabled={busy} className={button}>Create account</button></form>}

      {showNewDesk&&<form className={`${card} max-w-lg space-y-3`} onSubmit={async e=>{
        e.preventDefault();
        const made=await act(()=>paperAction('create_desk',{p_name:newDesk.name.trim(),p_starting_cash:Number(newDesk.cash),p_mode:'manual'}));
        if(made){setShowNewDesk(false);setNewDesk({name:'',cash:''});}
      }}>
        <h2 className="font-semibold">New paper desk</h2>
        <p className="text-xs text-muted">Its own cash, strategies and cities. It starts <strong>paused</strong> and manual — set it up under Settings, then press Start when you want it to act.</p>
        <label className="block text-sm">Name<input required className="input mt-1" maxLength={100} placeholder="e.g. Austin only, tight edge" value={newDesk.name} onChange={e=>setNewDesk({...newDesk,name:e.target.value})}/></label>
        <label className="block text-sm">Starting paper cash (USD)<input required type="number" min="1" step="0.01" className="input mt-1" value={newDesk.cash} onChange={e=>setNewDesk({...newDesk,cash:e.target.value})}/></label>
        <button disabled={busy} className={button}>Create desk</button></form>}
      {selected&&<>
        {/* STATUS, THE SWITCH AND THE NUMBERS, before anything else.
            This replaces three separate warning banners that each explained
            one way a desk can be idle. They said the right things in the
            wrong place: below the desk picker, above nothing, and only for
            the two cases somebody had thought of. PaperDeskControl states
            the case, names the remedy and carries the button that applies
            it. */}
        <PaperDeskControl
          desk={selected}
          exposure={positions.data?.reduce((t,p)=>t+Number(p.cost_basis||0),0) ?? 0}
          openPositions={positions.data?.filter(p=>Number(p.shares)>0).length ?? 0}
          lastFill={orders.data?.find(o=>o.status==='filled'||o.status==='partial')?.requested_at ?? null}
          refresh={refresh}
          openSettings={()=>setTab('Settings')}/>
        <PaperPipelineStatus refresh={refresh}/>
        <details className={card} open={selected.mode==='manual'}>
          <summary className="cursor-pointer text-sm font-semibold">Manual paper ticket
            <span className="ml-2 font-normal text-muted">— place one order by hand, bypassing strategies</span></summary>
        <form className="mt-3 space-y-3" onSubmit={e=>{e.preventDefault();submit();}}><fieldset disabled={busy} className="space-y-3">
          <div className="grid gap-3 md:grid-cols-3"><label className="text-sm md:col-span-2">Market / band<select required className="input mt-1" value={ticket.band} onChange={e=>{setCommand(null);setTicket({...ticket,band:e.target.value});}}><option value="">Select a current contract</option>{bands.data?.map(b=><option key={b.band_id} value={b.band_id}>{name(b.band_id)}</option>)}</select></label>
            <label className="text-sm">Side<select className="input mt-1" value={ticket.side} onChange={e=>{setCommand(null);setTicket({...ticket,side:e.target.value});}}><option>YES</option><option>NO</option></select></label>
            {(['shares','limit','ceiling'] as const).map(field=><label key={field} className="text-sm">{{shares:'Shares',limit:'Maximum price (USD/share)',ceiling:'Maximum total including fees (USD)'}[field]}<input required type="number" min="0.01" max={field==='limit'?'0.99':undefined} step="0.01" className="input mt-1" value={ticket[field]} onChange={e=>{setCommand(null);setTicket({...ticket,[field]:e.target.value});}}/></label>)}
          </div><label className="block text-sm">Reason / thesis<input className="input mt-1" value={ticket.reason} onChange={e=>{setCommand(null);setTicket({...ticket,reason:e.target.value});}}/></label>
          <p className="text-xs text-muted">Immediate-or-cancel simulation: only available depth within your limit can fill. Market metadata, fees and book freshness are verified by the worker.</p>
          <button disabled={busy||!ticket.band} className={button}>Queue paper order</button></fieldset>
        </form>
        </details>
        <nav aria-label="Paper trade views" className="flex flex-wrap gap-2">{['Trades','Orders','Positions','Activity','Settings'].map(t=><button key={t} className={`${button} ${tab===t?'text-accent border-accent':''}`} onClick={()=>setTab(t)}>{t}</button>)}</nav>
        {tab==='Trades'&&<PaperTradeHistory account={account} bandName={name}/>}
        {tab==='Orders'&&<div className={`${card} overflow-x-auto`}><table className="w-full text-left text-sm"><thead className="text-muted"><tr>{['Contract','Order','Requested','Limit','Status',''].map((x,i)=><th className="p-2" key={i}>{x}</th>)}</tr></thead><tbody>{orders.data?.map(o=><tr key={o.order_id} className="border-t border-border"><td className="p-2">{name(o.band_id)}</td><td className="p-2">{o.origin} · {o.action} {o.side}</td><td className="p-2">{Number(o.shares).toLocaleString(undefined,{maximumFractionDigits:2})}</td><td className="p-2">{fmtPrice(Number(o.limit_price))}</td><td className="p-2"><div>{o.status}</div><div className="text-xs text-muted">{o.reason}</div></td><td className="p-2">{o.status==='queued'&&<button disabled={busy} className={button} onClick={()=>act(()=>paperAction('cancel_order',{p_order:o.order_id}))}>Cancel</button>}<details><summary className="cursor-pointer text-muted">Evidence</summary><pre className="max-w-md overflow-auto text-xs">{JSON.stringify(o.result,null,2)}</pre></details></td></tr>)}</tbody></table>{!orders.data?.length&&<p className="py-4 text-sm text-muted">No paper orders yet. System alerts are not trades.</p>}{orders.truncated&&<p className="text-xs text-warn">Showing the latest 100 orders; older history is retained.</p>}</div>}
        {tab==='Positions'&&<div className={`${card} space-y-3`}>{positions.data?.map(p=><div key={p.band_id+p.side} className="border-b border-border pb-2 text-sm"><div>{name(p.band_id)} · {p.side}</div><div className="font-mono">{Number(p.shares).toLocaleString(undefined,{maximumFractionDigits:2})} shares · Cost basis {fmtUsd(Number(p.cost_basis))} · Realized {fmtUsd(Number(p.realized_pnl))}</div>{Number(p.shares)>0&&<PaperExit key={account+p.band_id+p.side} account={account} band={p.band_id} side={p.side} available={Number(p.shares)} refresh={refresh}/>}</div>)}{!positions.data?.length&&<p className="text-sm text-muted">Positions appear after a recorded fill. Unrealized profit requires a fresh executable exit quote.</p>}</div>}
        {tab==='Activity'&&<div className={`${card} space-y-3`}>{events.data?.map(e=><details key={e.event_id} className="border-b border-border pb-2"><summary className="cursor-pointer text-sm">{new Date(e.occurred_at).toLocaleString()} · {e.event_type.replaceAll('_',' ')} · {fmtUsd(Number(e.cash_delta))}</summary><pre className="overflow-auto text-xs text-muted">{JSON.stringify(e.payload,null,2)}</pre></details>)}{events.truncated&&<p className="text-xs text-warn">Showing the latest 100 events; older history is retained.</p>}</div>}
        {tab==='Settings'&&<PaperAutomation key={selected.account_id+selected.policy_version} account={selected} refresh={refresh}/>}
      </>}
    <div className="flex flex-wrap gap-4 text-sm text-accent"><Link href="/board">Board</Link><Link href="/predictive">Predictive</Link><Link href="/databank">Data Bank</Link><Link href="/synthesis">Synthesis</Link></div>
  </div>;
}
