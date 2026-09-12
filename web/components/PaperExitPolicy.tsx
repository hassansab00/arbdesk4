"use client";
import { useState } from 'react';
import { paperAction } from '@/lib/paperSupabase';

export default function PaperExitPolicy({account,policy,refresh}:{account:string;policy:{auto_exit_enabled?:boolean;take_profit_fraction?:number;stop_loss_fraction?:number};refresh:()=>void}) {
  const [enabled,setEnabled]=useState(policy.auto_exit_enabled??false);
  const [profit,setProfit]=useState(String((policy.take_profit_fraction??.25)*100));
  const [loss,setLoss]=useState(String((policy.stop_loss_fraction??.15)*100));
  const [busy,setBusy]=useState(false);const [error,setError]=useState('');
  return <form className="space-y-3 border-t border-border pt-4" onSubmit={async e=>{
    e.preventDefault();setBusy(true);setError('');
    try {const r=await paperAction('set_exit_policy',{p_account:account,p_enabled:enabled,p_take_profit:Number(profit)/100,p_stop_loss:Number(loss)/100});if(r.error)throw r.error;refresh();}
    catch(e){setError(e&&typeof e==='object'&&'message' in e?String(e.message):String(e));}finally{setBusy(false);}
  }}><fieldset disabled={busy} className="space-y-3"><h3 className="font-semibold">Automatic exits</h3>
    <p className="text-sm text-muted">In automatic mode, evaluate profit and loss against a fresh sell quote after fees. This applies to every open position in this account, including individual basket legs. It can reduce a basket’s coverage. Entry pause does not pause these exits.</p>
    <label className="flex gap-2 text-sm"><input type="checkbox" checked={enabled} onChange={e=>setEnabled(e.target.checked)}/>Enable account exit thresholds</label>
    <div className="grid gap-3 sm:grid-cols-2"><label className="text-sm">Take profit (%)<input required type="number" className="input mt-1" min="0.01" max="1000" step="0.01" value={profit} onChange={e=>setProfit(e.target.value)}/></label><label className="text-sm">Stop loss (%)<input required type="number" className="input mt-1" min="0.01" max="99.99" step="0.01" value={loss} onChange={e=>setLoss(e.target.value)}/></label></div>
    <p className="text-xs text-muted">Thresholds trigger an order, not a guaranteed price. Actual fills depend on the available bid depth.</p>
    <button className="rounded border border-border px-3 py-1 text-sm hover:bg-panel2 disabled:opacity-40">Save exit policy</button>
    {error&&<p role="alert" className="text-sm text-bad">{error}</p>}
  </fieldset></form>;
}
