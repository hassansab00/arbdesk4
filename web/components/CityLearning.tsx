"use client";

import { supabase } from "@/lib/supabase";
import { useQuery } from "@/lib/useQuery";
import { fmtTempDelta, type Unit } from "@/lib/units";

type Learning = {
  as_of: string; training_days: number; pooled: boolean;
  combination_width_c: number | string | null; width_days: number | null;
  calibration: { applies: boolean; sigma_c: number | null; n_validation: number; reason: string } | null;
};

type Forward = { checkpoint: string; width_version: string; settled_calls: number;
  settled_dates: number; pending_calls: number; hit_rate: number | null;
  brier: number | null; log_loss: number | null };

export default function CityLearning({ cityKey, unit }: { cityKey: string; unit: Unit }) {
  const q = useQuery<Learning[]>(() => supabase.from("v_city_learning")
    .select("as_of,training_days,pooled,combination_width_c,width_days,calibration")
    .eq("city_key", cityKey).eq("lead_days", 1).limit(1), [cityKey]);
  const forward = useQuery<Forward[]>(() => supabase.from("v_city_calibration_forward")
    .select("checkpoint,width_version,settled_calls,settled_dates,pending_calls,hit_rate,brier,log_loss")
    .eq("city_key", cityKey).order("last_called_at", { ascending: false }).limit(8), [cityKey], 120000);
  const row = q.data?.[0];
  const cal = row?.calibration;
  return <section className="mt-3 rounded border border-border bg-panel2 p-2">
    <h3 className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-muted">This city’s learning</h3>
    {q.error ? <div className="text-muted">Learning status could not be loaded: {q.error}
      {" "}<button type="button" className="underline" onClick={q.refresh}>Reload</button></div> : row ? <>
      <p>Last fit {row.as_of} · {row.training_days} completed training days
        {row.pooled ? " · uses the pooled prior" : " · city-specific correction"}.</p>
      <p className="mt-1 text-muted">Learns temperature errors using model disagreement, cloud, rain, humidity,
        wind and season. Updates nightly from completed days.</p>
      {cal ? <p className="mt-1">
        Blend uncertainty: {cal.sigma_c != null ? fmtTempDelta(cal.sigma_c, unit).replace("+", "") : "pending"}.
        {" "}{cal.applies ? "Passed the later-date calibration check" : `Held: ${cal.reason}`}.
        {" "}{cal.n_validation} validation days.
      </p> : <p className="mt-1 text-muted">Blend calibration is awaiting its first completed fit.</p>}
      <p className="mt-1 text-[10px] text-muted">New widths must improve on later dates before use. The weather
        differences in the day reviews describe each hit or miss; they do not prove its cause.</p>
    </> : <p className="text-muted">{q.loading ? "Loading learning status…" : "No completed city fit yet."}</p>}
    <div className="mt-2 border-t border-border pt-2">
      <p className="font-semibold">Forward results of served widths</p>
      {forward.error ? <p className="text-muted">Results could not be loaded: {forward.error}
        {" "}<button type="button" className="underline" onClick={forward.refresh}>Reload</button></p>
        : forward.loading ? <p className="text-muted">Loading forward record…</p>
        : !forward.data?.length ? <p className="text-muted">No first checkpoint call has used a new blend width here yet.</p>
        : forward.data.map((f) => <div key={`${f.width_version}:${f.checkpoint}`} className="mt-1">
          <p>{f.checkpoint} · {f.settled_calls} settled calls on {f.settled_dates} dates · {f.pending_calls} awaiting settlement</p>
          {f.settled_calls > 0 && <p>Hit rate {((f.hit_rate ?? 0) * 100).toFixed(1)}% · Brier {Number(f.brier).toFixed(3)}
            {" · "}log loss {Number(f.log_loss).toFixed(3)}</p>}
          <p className="break-all text-[10px] text-muted">{f.width_version}</p>
        </div>)}
      <p className="mt-1 text-[10px] text-muted">Original checkpoint calls only, graded after the local day ends
        and the venue confirms the winner. These scores do not establish improvement over the earlier width.</p>
    </div>
  </section>;
}
