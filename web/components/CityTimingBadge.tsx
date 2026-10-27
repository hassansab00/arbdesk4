import React from 'react';
import { momentLabel } from '@/lib/focus';
import { fmtPct } from '@/lib/format';
import { MIN_REFERENCE_DATES, type checkpointReference } from '@/lib/cityManualGuide';

/** Descriptive hit-rate reference, never a live entry signal. */
export default function CityTimingBadge({reference,loading,error}: {
  reference:ReturnType<typeof checkpointReference>;loading:boolean;error:string|null;
}) {
  const leaders=reference.table.filter(r=>reference.leaders.includes(r.checkpoint));
  const limited=leaders.some(r=>r.verifiedBeforePeak<MIN_REFERENCE_DATES);
  return <div aria-label="City timing indicator" className="mt-2 rounded border border-accent/30 bg-panel2 px-2 py-1.5 text-[11px]"
    title={error ?? 'Same matched past dates as the popup guide. Scheduled city-local checkpoints; actual freezes can be later. After-peak calls excluded. Historical accuracy does not establish a profitable entry.'}>
    {loading?<span className="text-muted">Timing · reading history…</span>
      :error?<span className="text-warn">Timing · history unavailable</span>
      :!reference.comparable?<span className="text-muted">Timing · insufficient matched history</span>
      :<>
        <div><span className="text-muted">{leaders.length>1?'Joint best recorded timing':'Best recorded timing'}</span>{' · '}
          <b className="text-accent">{leaders.map(r=>momentLabel(r.checkpoint)).join(' / ')}</b>
        </div>
        <div className="tabular-nums">{leaders[0].hits}/{leaders[0].n} correct · {fmtPct(leaders[0].rate,0)}
          <span className="text-muted"> · same dates across {reference.table.length} checkpoints</span>
        </div>
        <div className={limited?'text-warn':'text-muted'}>
          {limited?'Limited timing proof · ':''}Historical only · entry not validated · open guide
        </div>
      </>}
  </div>;
}
