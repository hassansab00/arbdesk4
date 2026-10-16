"use client";

import { supabase } from "@/lib/supabase";
import { useQuery } from "@/lib/useQuery";
import { fmtTempDelta, type Unit } from "@/lib/units";

type Learning = {
  as_of: string; training_days: number; pooled: boolean;
  combination_width_c: number | string | null; width_days: number | null;
  calibration: { applies: boolean; sigma_c: number | null; n_validation: number; reason: string } | null;
};

export default function CityLearning({ cityKey, unit }: { cityKey: string; unit: Unit }) {
  const q = useQuery<Learning[]>(() => supabase.from("v_city_learning")
    .select("as_of,training_days,pooled,combination_width_c,width_days,calibration")
    .eq("city_key", cityKey).eq("lead_days", 1).limit(1), [cityKey]);
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
  </section>;
}
