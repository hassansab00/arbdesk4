import React from 'react';
import { fmtPct } from '@/lib/format';
import { momentLabel } from '@/lib/focus';
import { fmtDateTime } from '@/lib/time';
import type { FrozenCall } from '@/lib/frozenCalls';

export default function FrozenCallPanel({call,moment,timezone,loading,error}: {
  call:FrozenCall|null;moment:string;timezone:string|null|undefined;loading:boolean;error:string|null;
}) {
  return <div className="mt-2 rounded border border-accent/50 bg-accent/5 px-2 py-1.5 text-xs">
    <div className="text-[10px] uppercase tracking-wide text-muted">
      {moment==='day_ahead'?'Recorded day-ahead pick':'Frozen pick'} · {momentLabel(moment)}
    </div>
    {loading ? <p className="text-muted">Reading original call…</p>
      : error ? <p className="text-bad">Could not read the original call: {error}</p>
      : call ? <>
        <div className="flex flex-wrap items-baseline gap-x-2">
          <b className="text-xl tabular-nums">{call.top_label ?? 'Saved bucket label unavailable'}</b>
          <span>{fmtPct(call.top_prob,0)} likely</span>
        </div>
        <p className="text-[11px] text-muted">
          Recorded {fmtDateTime(call.decided_at,timezone ?? 'UTC')} · {timezone ?? 'UTC'}.
          Server receipt eligibility is checked separately in validation.
        </p>
        <p className="text-[11px] text-muted">This original call is scored separately from later forecasts.</p>
      </> : <p className="text-muted">{moment==='day_ahead'
        ? 'Day-ahead history appears after settlement. It uses the last pricing before city-local midnight; select Evening before for the separate 18:00 checkpoint.'
        : 'No saved call at this moment for this trade date yet. Capture may not be due; a live forecast is not substituted.'}</p>}
  </div>;
}
