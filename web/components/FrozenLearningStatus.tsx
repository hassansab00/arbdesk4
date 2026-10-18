"use client";

import Link from "next/link";
import {supabase} from "@/lib/supabase";
import {useQuery} from "@/lib/useQuery";
import {DataState} from "@/components/DataState";
import {fmtAge} from "@/lib/format";

interface Selection {city_key:string; checkpoint:string; as_of:string; fit_at:string|null;
  decision:string|null; active:string|null}
interface Phase {phase:string; active:boolean; decision:string; reason:string; model_version:string}
interface Learning {city_key:string; as_of:string; learning:Phase[]|null}

/** Current receipt-based learning, independent of the retired weather research chain. */
export default function FrozenLearningStatus() {
  const q=useQuery<{selection:Selection[]; learning:Learning[]}>(async()=>{
    const [s,l]=await Promise.all([
      supabase.from("v_city_prediction_validation")
        .select("city_key,checkpoint,as_of,fit_at:report->model_selection->>fit_at,decision:report->model_selection->>decision,active:report->model_selection->effective->>active")
        .eq("scope","served").neq("checkpoint","overall").order("city_key").order("checkpoint"),
      supabase.from("v_city_prediction_validation").select("city_key,as_of,learning:report->learning")
        .eq("scope","served").eq("checkpoint","overall").order("city_key")
    ]);
    return {data:{selection:(s.data??[]) as Selection[],learning:(l.data??[]) as Learning[]},error:s.error??l.error};
  },[],120000);
  const rows=q.data?.selection??[], learning=q.data?.learning??[];
  const supported=rows.filter(r=>["noon","prepeak_2h","prepeak_1h","postpeak_1h"].includes(r.checkpoint));
  const phases=learning.flatMap(r=>r.learning??[]);
  const scores=rows.map(r=>r.as_of).sort(), fits=rows.flatMap(r=>r.fit_at?[r.fit_at]:[]).sort();
  const oldestScore=scores[0]??null, fit=fits.at(-1)??null;
  const stale=oldestScore && Date.now()-Date.parse(oldestScore)>36*3600000;
  return <section className="space-y-3">
    <h2 className="text-sm font-semibold">Frozen prediction learning and validation</h2>
    <p className="max-w-3xl text-xs text-muted">Original calls stay frozen. Venue-confirmed results are scored,
      then city and peak-phase adjustments and horizon choices are fitted on older dates and checked on later dates.
      Insufficient evidence keeps the declared prior.</p>
    <DataState relation="v_city_prediction_validation" loading={q.loading} error={q.error}
      isEmpty={!rows.length} emptyTitle="Awaiting frozen-call validation reports"
      emptyBody="The existing daily learning job and intraday score refresh populate this record." onRetry={q.refresh}>
      <div className={`rounded border p-3 text-xs ${stale?"border-warn text-warn":"border-border"}`}>
        <p>{new Set(rows.map(r=>r.city_key)).size} cities · oldest city score refresh {fmtAge(oldestScore)} · latest fit {fmtAge(fit)}</p>
        <p className="mt-2">Predictor selection: {supported.filter(r=>r.active==="true").length} validated overrides
          across {supported.length} supported city/horizon cells. Earlier horizons retain their authorized baseline.</p>
        <p className="mt-1">Calibration: {phases.filter(p=>p.active).length} validated adjustments across {phases.length} city/peak-phase cells.</p>
        <p className="mt-2 text-muted">A nightly fit can complete with every adjustment held. A change needs at least
          20 older training dates and 10 later validation dates, plus the declared improvement checks. Time elapsed alone does not approve it.</p>
        {stale && <p className="mt-2">Score refresh is overdue. Check the daily and intraday workflows.</p>}
        <Link href="/predictive" className="mt-2 inline-block text-accent hover:underline">City rankings, raw/baseline comparisons and individual learning evidence →</Link>
      </div>
    </DataState>
  </section>;
}
