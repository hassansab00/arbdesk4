"use client";

import { useEffect, useState } from 'react';
import Link from 'next/link';
import PaperAutomation from '@/components/PaperAutomation';
import Hint, { PAPER_HINTS as H } from '@/components/Hint';
import PaperExit from '@/components/PaperExit';
import PaperDeskControl from '@/components/PaperDeskControl';
import PaperPipelineStatus from '@/components/PaperPipelineStatus';
import PaperTradeHistory from '@/components/PaperTradeHistory';
import { paperAction, paperRead, runPaperWorker, describeWorker } from '@/lib/paperSupabase';
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

type NewDesk = {
  name:string; cash:string; mode:string;
  max_plan_usd:string; max_exposure_usd:string; min_edge:string;
  strategies:string[]; cities:string[];
};

// Deliberately empty rather than opinionated: an unticked strategy list means
// "nothing may fire here yet", which is the safe reading for a desk that is
// about to be created paused anyway.
const BLANK_DESK:NewDesk = {
  name:'', cash:'', mode:'manual',
  max_plan_usd:'25', max_exposure_usd:'250', min_edge:'0.03',
  strategies:[], cities:[],
};

export default function PaperTradesPage() {
  const [error,setError] = useState<string|null>(null);
  const [notice,setNotice] = useState<string|null>(null);
  const [noticeWarning,setNoticeWarning] = useState(false);
  const [busy,setBusy] = useState(false);
  const [account,setAccount] = useState('');
  const [tab,setTab] = useState('Trades');
  const [starting,setStarting] = useState('');
  const [showNewDesk,setShowNewDesk] = useState(false);
  // A NEW DESK IS A NEW SET OF SETTINGS, not just a new name. The form used
  // to send name and cash and hard-code mode:'manual' with no policy, so
  // every desk arrived identical and had to be configured afterwards under
  // Settings - which is the opposite of "try a setting without disturbing the
  // one already running". paper_desk_create has always taken a mode and a
  // policy; this just stops throwing them away.
  const [newDesk,setNewDesk] = useState<NewDesk>(BLANK_DESK);
  const [showArchived,setShowArchived] = useState(false);
  // The same two lists the Settings tab uses, so a desk can be given its
  // scope at creation instead of afterwards. Cities are ACTIVE ONLY - a
  // retired city must not be offerable as a new desk's scope.
  const strategyList=useQuery<{strategy_id:string;name:string;enabled:boolean}[]>(
    ()=>supabase.from('strategies').select('strategy_id,name,enabled').order('strategy_id'),[]);
  const cityList=useQuery<{city_key:string}[]>(
    ()=>supabase.from('cities').select('city_key').eq('status','active').order('city_key'),[]);
  // Archived desks are excluded from the switcher by the route, so without
  // this they would be unreachable - "remove" that cannot be undone from the
  // UI is a delete however it is spelled in the database.
  // Retired desks (plan v2 P0.3) are archived too, so they land here, hidden
  // behind the same toggle. Unlike an archived desk they cannot be restored:
  // the database refuses, so the button is not offered.
  const archived=useQuery<Array<{account_id:string;name:string;mode:string;archived_at:string|null;status:string|null}>>(
    ()=>supabase.from('v_paper_desks').select('account_id,name,mode,archived_at:created_at,status').eq('archived',true).order('name'),
    [],undefined);
  // DO THIS DESK'S BOOKS BALANCE?
  //
  // v_paper_desk_integrity checks cash against the activity ledger, reserved
  // cash against what live orders claimed, and cash against starting cash less
  // open basis plus realised P&L - four tables written by four code paths. n8n
  // P2.2 runs the same check hourly and records an anomaly, but the alarm a
  // person actually sees is this line, on the page about the desk. `note` is
  // the view's own sentence; it is not re-worded here, or the page and the
  // database would be able to say different things about the same number.
  const books=useQuery<Array<{ok:boolean;breaches:string[];unverifiable:string[];note:string}>>(
    async()=>{
      if(!account) return {data:[],error:null};
      return supabase.from('v_paper_desk_integrity')
        .select('ok,breaches,unverifiable,note').eq('account_id',account);
    },[account],60000);
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
    // mode and entries_paused only - both on paper_accounts since day one.
    // The trade and position counts came from a view that is no longer in
    // this request path, and a desk that CAN act is the distinction that
    // actually mattered: it separates "Wide edge, all US" (automatic,
    // running, 10 trades) from "Main paper account" (manual, paused, empty),
    // which is the pair that made this page look broken.
    const live=(a:Account)=>(a.mode==='automatic'||a.mode==='assisted')&&!a.entries_paused;
    const best=[...accounts.data].sort((a,b)=>
      Number(live(b))-Number(live(a))
      || (b.open_positions??0)-(a.open_positions??0)
      || (b.trade_count??0)-(a.trade_count??0));
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
        // A ticket queued here expires in FIVE MINUTES and reserves the cash
        // until it does. Until this route was pointed at GitHub Actions the
        // wake-up always failed, so every manual ticket ever written expired
        // unfilled - which is why this now says what is actually happening
        // and when to look, instead of "refresh shortly".
        setNotice(result.orders_completed
          ? 'Order processed against a fresh verified book. See Orders for the fill result.'
          : describeWorker(result));
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
        onClick={()=>{setShowNewDesk(v=>!v);setNewDesk(BLANK_DESK);}}>
        {showNewDesk?'Cancel':'New desk'}</button>}
      {/* ARCHIVE BELONGS BESIDE THE SWITCHER, NOT HALFWAY DOWN THE PAGE.
          It was rendered between the pipeline status block and the manual
          ticket form - after the desk controls, before the tabs, in a row of
          grey helper text. It works; nobody could find it, which for a
          control is the same thing as it not being there. The pair a person
          is looking for is "make a desk / put this one away", so they sit
          together. */}
      {selected&&<button type="button" disabled={busy} className={button}
        title={`Archive "${selected.name}" — off the list, stopped, nothing deleted`}
        onClick={async()=>{
          const open=positions.data?.filter(x=>Number(x.shares)>0).length??0;
          const warn=open?`\n\n${open} position${open===1?' is':'s are'} still open. Archiving does not close ${open===1?'it':'them'} — ${open===1?'it':'they'} will settle as normal and stay on the record.`:'';
          if(!confirm(`Archive "${selected.name}"?\n\nIt comes off the desk list and stops trading. Nothing is deleted: every trade, order and activity row stays, and you can restore it below.${warn}`))return;
          const done=await act(()=>paperAction('archive_desk',{p_account_id:selected.account_id,p_archived:true}));
          if(done){setAccount('');archived.refresh();}
        }}>Archive this desk</button>}
      {accounts.data&&accounts.data.length>1&&<span className="text-xs text-muted">
        {accounts.data.length} desks · each runs independently</span>}</div>

      {books.data?.[0]&&(books.data[0].ok
        ? <p className={`text-xs ${books.data[0].unverifiable?.length?'text-warn':'text-muted'}`}>
            Books balance{books.data[0].unverifiable?.length
              ? ` · ${books.data[0].unverifiable.join(', ')} cannot be checked on this desk`
              : ''}</p>
        : <div role="alert" className="rounded border border-bad p-3 text-sm text-bad">
            <strong>This desk&apos;s books do not balance</strong> ({books.data[0].breaches.join(', ')}).{' '}
            {books.data[0].note}</div>)}

      {!accounts.loading&&!accounts.data?.length&&<form className={`${card} max-w-lg space-y-3`} onSubmit={e=>{e.preventDefault();act(()=>paperAction('create_account',{p_name:'Main paper account',p_starting_cash:Number(starting)}));}}>
        <h2 className="font-semibold">Create paper account</h2><label className="block text-sm">Starting paper cash (USD)<input required type="number" min="0.01" step="0.01" className="input mt-1" value={starting} onChange={e=>setStarting(e.target.value)}/></label>
        <button disabled={busy} className={button}>Create account</button></form>}

      {showNewDesk&&<form className={`${card} max-w-2xl space-y-3`} onSubmit={async e=>{
        e.preventDefault();
        // Mode and policy go in AT CREATION. paper_desk_create validates all
        // of it server-side and always creates the desk paused, so a wrong
        // number here costs an edit, never a trade.
        const made=await act(()=>paperAction('create_desk',{
          p_name:newDesk.name.trim(),
          p_starting_cash:Number(newDesk.cash),
          p_mode:newDesk.mode,
          p_policy:{
            strategies:newDesk.strategies,
            cities:newDesk.cities,
            max_plan_usd:Number(newDesk.max_plan_usd),
            max_exposure_usd:Number(newDesk.max_exposure_usd),
            min_edge:Number(newDesk.min_edge),
          },
        }));
        if(made){setShowNewDesk(false);setNewDesk(BLANK_DESK);}
      }}>
        <h2 className="font-semibold">New paper desk</h2>
        <p className="text-xs text-muted">Its own cash, strategies, cities and limits — nothing is shared with the desk already running. It is created <strong>paused</strong> whatever you choose below, so it will not act until you press Start.</p>

        <div className="grid gap-3 sm:grid-cols-2">
          <label className="block text-sm">Name<Hint text={H.name}/>
            <input required className="input mt-1" maxLength={100} placeholder="e.g. Austin only, tight edge" value={newDesk.name} onChange={e=>setNewDesk({...newDesk,name:e.target.value})}/></label>
          <label className="block text-sm">Starting paper cash (USD)<Hint text={H.starting_cash}/>
            <input required type="number" min="1" step="0.01" className="input mt-1" value={newDesk.cash} onChange={e=>setNewDesk({...newDesk,cash:e.target.value})}/></label>
        </div>

        <label className="block text-sm">Mode<Hint text={H.mode}/>
          <select className="input mt-1" value={newDesk.mode} onChange={e=>setNewDesk({...newDesk,mode:e.target.value})}>
            <option value="manual">Manual — only your own tickets</option>
            <option value="assisted">Assisted — propose, you approve each one</option>
            <option value="automatic">Automatic — place them within the limits below</option>
          </select></label>

        <div className="grid gap-3 sm:grid-cols-3">
          {([['max_plan_usd','Max per trade (USD)',H.max_plan_usd],
             ['max_exposure_usd','Max at risk at once (USD)',H.max_exposure_usd],
             ['min_edge','Min edge per share (USD)',H.min_edge]] as const).map(([f,label,hint])=>
            <label key={f} className="text-sm">{label}<Hint text={hint}/>
              <input required type="number" className="input mt-1"
                min={f==='min_edge'?0:1} max={f==='min_edge'?1:1000000} step="0.01"
                value={newDesk[f]} onChange={e=>setNewDesk({...newDesk,[f]:e.target.value})}/></label>)}
        </div>

        <div><p className="mb-1 text-sm">Allowed strategies<Hint text={H.strategies}/></p>
          <div className="grid gap-2 md:grid-cols-2">{strategyList.data?.map(st=>
            <label className="flex gap-2 text-sm" key={st.strategy_id}>
              <input type="checkbox" checked={newDesk.strategies.includes(st.strategy_id)}
                onChange={()=>setNewDesk({...newDesk,strategies:newDesk.strategies.includes(st.strategy_id)
                  ?newDesk.strategies.filter(x=>x!==st.strategy_id)
                  :[...newDesk.strategies,st.strategy_id]})}/>
              {st.name}{!st.enabled&&<span className="text-muted">(disabled globally)</span>}</label>)}</div>
          {!newDesk.strategies.length&&<p className="mt-1 text-xs text-muted">None ticked — this desk will not open anything automatically until one is.</p>}</div>

        <div><p className="mb-1 text-sm">Allowed cities<Hint text={H.cities}/></p>
          <div className="flex max-h-40 flex-wrap gap-3 overflow-auto">{['ALL',...(cityList.data||[]).map(c=>c.city_key)].map(c=>
            <label className="flex gap-2 text-sm" key={c}>
              <input type="checkbox" checked={newDesk.cities.includes(c)}
                onChange={()=>setNewDesk({...newDesk,cities:newDesk.cities.includes(c)
                  ?newDesk.cities.filter(x=>x!==c):[...newDesk.cities,c]})}/>
              {c==='ALL'?'All cities':c}</label>)}</div></div>

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
          <div className="grid gap-3 md:grid-cols-3"><label className="text-sm md:col-span-2">Market / band<Hint text={H.band}/><select required className="input mt-1" value={ticket.band} onChange={e=>{setCommand(null);setTicket({...ticket,band:e.target.value});}}><option value="">Select a current contract</option>{bands.data?.map(b=><option key={b.band_id} value={b.band_id}>{name(b.band_id)}</option>)}</select></label>
            <label className="text-sm">Side<Hint text={H.side}/><select className="input mt-1" value={ticket.side} onChange={e=>{setCommand(null);setTicket({...ticket,side:e.target.value});}}><option>YES</option><option>NO</option></select></label>
            {(['shares','limit','ceiling'] as const).map(field=><label key={field} className="text-sm">{{shares:'Shares',limit:'Maximum price (USD/share)',ceiling:'Maximum total including fees (USD)'}[field]}<Hint text={H[field]}/><input required type="number" min="0.01" max={field==='limit'?'0.99':undefined} step="0.01" className="input mt-1" value={ticket[field]} onChange={e=>{setCommand(null);setTicket({...ticket,[field]:e.target.value});}}/></label>)}
          </div><label className="block text-sm">Reason / thesis<Hint text={H.reason}/><input className="input mt-1" value={ticket.reason} onChange={e=>{setCommand(null);setTicket({...ticket,reason:e.target.value});}}/></label>
          <p className="text-xs text-muted">Immediate-or-cancel simulation: only available depth within your limit can fill. Market metadata, fees and book freshness are verified by the worker.</p>
          <button disabled={busy||!ticket.band} className={button}>Queue paper order</button></fieldset>
        </form>
        </details>
        <nav aria-label="Paper trade views" className="flex flex-wrap gap-2">{['Trades','Orders','Positions','Activity','Settings'].map(t=><button key={t} className={`${button} ${tab===t?'text-accent border-accent':''}`} onClick={()=>setTab(t)}>{t}</button>)}</nav>
        {tab==='Trades'&&<PaperTradeHistory account={account} deskName={selected.name} bandName={name}/>}
        {tab==='Orders'&&<div className={`${card} overflow-x-auto`}><table className="w-full text-left text-sm"><thead className="text-muted"><tr>{['Contract','Order','Requested','Limit','Status',''].map((x,i)=><th className="p-2" key={i}>{x}</th>)}</tr></thead><tbody>{orders.data?.map(o=><tr key={o.order_id} className="border-t border-border"><td className="p-2">{name(o.band_id)}</td><td className="p-2">{o.origin} · {o.action} {o.side}</td><td className="p-2">{Number(o.shares).toLocaleString(undefined,{maximumFractionDigits:2})}</td><td className="p-2">{fmtPrice(Number(o.limit_price))}</td><td className="p-2"><div>{o.status}</div><div className="text-xs text-muted">{o.reason}</div></td><td className="p-2">{o.status==='queued'&&<button disabled={busy} className={button} onClick={()=>act(()=>paperAction('cancel_order',{p_order:o.order_id}))}>Cancel</button>}<details><summary className="cursor-pointer text-muted">Evidence</summary><pre className="max-w-md overflow-auto text-xs">{JSON.stringify(o.result,null,2)}</pre></details></td></tr>)}</tbody></table>{!orders.data?.length&&<p className="py-4 text-sm text-muted">No paper orders yet. System alerts are not trades.</p>}{orders.truncated&&<p className="text-xs text-warn">Showing the latest 100 orders; older history is retained.</p>}</div>}
        {tab==='Positions'&&<div className={`${card} space-y-3`}>{positions.data?.map(p=><div key={p.band_id+p.side} className="border-b border-border pb-2 text-sm"><div>{name(p.band_id)} · {p.side}</div><div className="font-mono">{Number(p.shares).toLocaleString(undefined,{maximumFractionDigits:2})} shares · Cost basis {fmtUsd(Number(p.cost_basis))} · Realized {fmtUsd(Number(p.realized_pnl))}</div>{Number(p.shares)>0&&<PaperExit key={account+p.band_id+p.side} account={account} band={p.band_id} side={p.side} available={Number(p.shares)} refresh={refresh}/>}</div>)}{!positions.data?.length&&<p className="text-sm text-muted">Positions appear after a recorded fill. Unrealized profit requires a fresh executable exit quote.</p>}</div>}
        {tab==='Activity'&&<div className={`${card} space-y-3`}>{events.data?.map(e=><details key={e.event_id} className="border-b border-border pb-2"><summary className="cursor-pointer text-sm">{new Date(e.occurred_at).toLocaleString()} · {e.event_type.replaceAll('_',' ')} · {fmtUsd(Number(e.cash_delta))}</summary><pre className="overflow-auto text-xs text-muted">{JSON.stringify(e.payload,null,2)}</pre></details>)}{events.truncated&&<p className="text-xs text-warn">Showing the latest 100 events; older history is retained.</p>}</div>}
        {tab==='Settings'&&<PaperAutomation key={selected.account_id+selected.policy_version} account={selected} refresh={refresh}/>}
      </>}

    {/* WHERE AN ARCHIVED DESK GOES, so archiving is a move and not a
        disappearance. Without this the route's archived_at filter would make
        "Archive" irreversible from the UI, which is a delete wearing a softer
        word - and this desk's rule is that nothing is ever deleted. */}
    {!!archived.data?.length&&<div className={`${card} space-y-2`}>
      <button type="button" className="flex w-full items-center justify-between text-left text-sm font-semibold"
        onClick={()=>setShowArchived(v=>!v)}>
        <span>Archived desks ({archived.data.length})</span>
        <span className="text-xs font-normal text-muted">{showArchived?'Hide':'Show'}</span>
      </button>
      {showArchived&&<>
        <p className="text-xs text-muted">Off the switcher and stopped. Every trade, order and activity row is still there — restoring an archived desk brings all of it back, still paused. A retired desk cannot be restored.</p>
        {archived.data.map(d=><div key={d.account_id} className="flex flex-wrap items-center justify-between gap-2 border-t border-border pt-2 text-sm">
          <span>{d.name} <span className="text-muted">— {d.status==='retired'?'retired':d.mode}</span></span>
          {d.status==='retired'?<span className="text-xs text-muted">Kept as history; never trades again.</span>:
          <span className="flex items-center gap-1">
            <button type="button" disabled={busy} className={button}
              onClick={async()=>{
                const done=await act(()=>paperAction('archive_desk',{p_account_id:d.account_id,p_archived:false}));
                if(done){archived.refresh();}
              }}>Restore</button>
            <Hint text={H.restore}/>
          </span>}
        </div>)}
      </>}
    </div>}

    <div className="flex flex-wrap gap-4 text-sm text-accent"><Link href="/board">Board</Link><Link href="/predictive">Predictive</Link><Link href="/databank">Data Bank</Link><Link href="/synthesis">Synthesis</Link></div>
  </div>;
}
