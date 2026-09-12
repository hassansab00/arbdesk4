"use client";
import { useState } from 'react';
import { paperAction, runPaperWorker } from '@/lib/paperSupabase';

export default function PaperExit({account,band,side,available,refresh}:{account:string;band:string;side:string;available:number;refresh:()=>void}) {
  const [shares,setShares]=useState('');const [limit,setLimit]=useState('');
  const [command,setCommand]=useState<string|null>(null);const [busy,setBusy]=useState(false);
  const [message,setMessage]=useState('');const [error,setError]=useState(false);
  return <form className="mt-2" onSubmit={async e=>{
    e.preventDefault();setBusy(true);setMessage('');setError(false);
    const id=command??crypto.randomUUID();setCommand(id);
    try {const r=await paperAction('submit_exit',{p_account:account,p_command:id,p_band:band,p_side:side,p_shares:Number(shares),p_limit:Number(limit)});
      if(r.error)throw r.error;setCommand(null);setShares('');
      try {await runPaperWorker();setMessage('Exit processed. See Orders for the verified fill result.');}
      catch(wake){setError(true);setMessage(wake&&typeof wake==='object'&&'message' in wake?String(wake.message):'Exit queued; worker wake-up failed.');}
      refresh();
    } catch(e){setError(true);setMessage(e&&typeof e==='object'&&'message' in e?String(e.message):String(e));}
    finally{setBusy(false);}
  }}><fieldset disabled={busy} className="flex flex-wrap items-end gap-2">
    <label className="text-xs">Shares to sell<input className="input mt-1" required type="number" min="0.01" max={available} step="0.01" value={shares} onChange={e=>{setShares(e.target.value);setCommand(null);}}/></label>
    <label className="text-xs">Minimum price (USD/share)<input className="input mt-1" required type="number" min="0.001" max="0.999" step="0.001" value={limit} onChange={e=>{setLimit(e.target.value);setCommand(null);}}/></label>
    <button className="rounded border border-border px-3 py-1 text-sm hover:bg-panel2 disabled:opacity-40">Queue exit</button>
  </fieldset>{message&&<p role={error?'alert':'status'} className={`mt-2 text-xs ${error?'text-bad':'text-good'}`}>{message}</p>}</form>;
}
