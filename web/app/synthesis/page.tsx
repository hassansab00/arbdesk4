"use client";

import { useMemo } from "react";
import { supabase } from "@/lib/supabase";
import { useQuery } from "@/lib/useQuery";
import { DataState } from "@/components/DataState";
import { Freshness, FreshnessRow } from "@/components/Provenance";
import FrozenLearningStatus from "@/components/FrozenLearningStatus";
import { fmtAge, fmtInt } from "@/lib/format";

/**
 * SYNTHESIS — what the archive has actually established, and what the desk is
 * still learning.
 *
 * Every other page shows a slice: this city now, this bucket's price, this
 * model's error at two days out. None of them answered the question the whole
 * archive exists to answer — over the last N days, across these cities, what
 * KEEPS happening, and how sure are we?
 *
 * That answer is a short list of sentences, not a chart, and each sentence has
 * to carry its own evidence: the span, the cities, the count, and whether
 * there is yet enough behind it to act on. A finding with twelve days behind
 * it and one with nine hundred must not look the same on screen.
 *
 * Every sentence here is written by sql/ad4_40_synthesis.sql from rows this
 * database holds. Nothing is a general claim about weather markets, and where
 * the archive cannot support a claim the row says so and names how many more
 * days it needs.
 */

interface Finding {
  key: string;
  direction: "backward" | "forward";
  kind: string;
  span_days: number | null;
  n_cities: number;
  n: number;
  stat: number | null;
  rate_pct: number | null;
  min_n: number;
  status: "established" | "collecting";
  more_needed: number;
  headline: string;
  basis: string;
  threshold_note: string;
}

interface OutcomeHealth {
  sampled_at: string;
  raw_band_facts: number; verified_band_facts: number;
  raw_forecast_facts: number; verified_forecast_facts: number;
}

export default function SynthesisPage() {
  const findingsQ = useQuery<Finding[]>(
    () => supabase.from("v_synthesis_findings").select("*"),
    [],
    180000
  );
  const evidenceQ = useQuery<OutcomeHealth[]>(
    () => supabase.from("v_page_outcome_evidence_health").select("*"),
    [],
    180000
  );

  const findings = findingsQ.data ?? [];
  const evidence = evidenceQ.error ? undefined : evidenceQ.data?.[0];
  const backward = useMemo(
    () => findings.filter((f) => f.direction === "backward")
                  .filter((f) => f.key !== "forecast_lean" || Number(evidence?.verified_forecast_facts ?? 0) > 0)
                  .filter((f) => f.key !== "desk_vs_market" || Number(evidence?.verified_band_facts ?? 0) > 0)
                  .sort((a, b) => (a.status === b.status ? b.n - a.n : a.status === "established" ? -1 : 1)),
    [findings, evidence]
  );
  const forward = useMemo(() => findings.filter((f) => f.direction === "forward"), [findings]);

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-lg font-semibold">Synthesis</h1>
        <p className="mt-1 max-w-3xl text-xs leading-relaxed text-muted">
          What the archive has established, in sentences. Each one carries the span it covers, the
          cities it covers, and how many observations stand behind it — because a finding with a
          fortnight behind it and one with two years are not the same claim and must not read the
          same. Nothing here is a general truth about weather markets; every line is computed from
          rows this database holds, and where there is not enough evidence yet the line says so and
          names what is still needed.
        </p>
        {/* WHAT THIS PAGE STANDS ON. A thin page and an unfed page look
            identical, and only one of them is worth investigating. */}
        <div className="mt-2">
          <FreshnessRow relations={["bands", "derived_city_climate", "derived_city_day_features", "derived_weather_peak", "edges", "fact_band_outcome", "fact_forecast_outcome", "markets"]} />
        </div>
      </div>

      {evidenceQ.error ? <p role="alert" className="text-xs text-warn">Evidence counts unavailable: {evidenceQ.error}</p>
        : evidence?.sampled_at && <p className="text-xs text-muted">Evidence counts sampled {fmtAge(evidence.sampled_at)}; refreshed on the existing page cadence.</p>}

      {evidence && (evidence.raw_band_facts > evidence.verified_band_facts ||
                    evidence.raw_forecast_facts > evidence.verified_forecast_facts) && (
        <div className="rounded border border-warn/40 bg-warn/10 px-3 py-2 text-xs leading-relaxed text-warn">
          <b>Outcome-dependent findings are verification-gated.</b> Historical rows remain preserved,
          but forecast-accuracy claims require final station evidence and model-vs-market claims require
          a complete Gamma+CLOB-confirmed ladder. Unverified rows are not presented as established findings.
        </div>
      )}

      <FrozenLearningStatus />

      {/* ==================================================== WHAT WE KNOW */}
      <section className="space-y-3">
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <h2 className="text-sm font-semibold">What keeps happening</h2>
          <span className="text-[11px] text-muted">measurements from the record, not predictions</span>
        </div>
        <DataState
          relation="v_synthesis_findings"
          loading={findingsQ.loading}
          error={findingsQ.error}
          isEmpty={backward.length === 0}
          emptyTitle="Nothing established yet"
          emptyBody="Run sql/ad4_40_synthesis.sql, then let the collectors build up a few weeks of days."
          onRetry={findingsQ.refresh}
        >
          <ul className="space-y-2">
            {backward.map((f) => (
              <FindingCard key={f.key} f={f} />
            ))}
          </ul>
        </DataState>
      </section>

      {/* ================================================= WHAT IS COMING */}
      <section className="space-y-3">
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <h2 className="text-sm font-semibold">What the board says is coming</h2>
          <span className="text-[11px] text-muted">
            statements about right now — they change every time a forecast lands
          </span>
        </div>
        <DataState
          relation="v_synthesis_findings"
          loading={findingsQ.loading}
          error={findingsQ.error}
          isEmpty={forward.length === 0}
          emptyTitle="Nothing on the board"
          emptyBody="No forward forecasts or live buckets to read from yet."
          onRetry={findingsQ.refresh}
        >
          <ul className="space-y-2">
            {forward.map((f) => (
              <FindingCard key={f.key} f={f} />
            ))}
          </ul>
        </DataState>
      </section>
    </div>
  );
}

/**
 * One finding. The claim on top, the evidence under it, and — when the
 * evidence is thin — a bar showing how far off "enough" is, rather than a
 * badge that only says "not yet".
 */
function FindingCard({ f }: { f: Finding }) {
  const established = f.status === "established";
  const progress = f.min_n > 0 ? Math.min(1, f.n / f.min_n) : 1;
  return (
    <li
      className={`rounded border bg-panel/60 p-3 ${established ? "border-border" : "border-warn/40"}`}
    >
      <div className="flex items-start gap-2">
        <span
          className={`mt-1 h-2 w-2 shrink-0 rounded-full ${established ? "bg-good" : "bg-warn"}`}
          title={established ? "enough evidence to act on" : "still collecting"}
        />
        <div className="min-w-0 flex-1">
          <p className={`text-sm leading-relaxed ${established ? "text-text" : "text-muted"}`}>
            {f.headline}
          </p>
          <p className="mt-1 text-[11px] leading-relaxed text-muted">{f.basis}</p>

          {!established && f.direction === "backward" && (
            <div className="mt-2 flex items-center gap-2">
              <span className="h-1.5 flex-1 overflow-hidden rounded-full bg-panel2">
                <span
                  className="block h-full rounded-full bg-warn/70"
                  style={{ width: `${progress * 100}%` }}
                />
              </span>
              <span className="shrink-0 text-[10px] tabular-nums text-muted">
                {fmtInt(f.n)} / {fmtInt(f.min_n)}
              </span>
            </div>
          )}

          <div className="mt-1.5 flex flex-wrap gap-x-3 gap-y-0.5 text-[10px] text-muted">
            {f.span_days ? <span>last {f.span_days} days</span> : null}
            {f.n_cities > 0 ? (
              <span>
                {f.n_cities} cit{f.n_cities === 1 ? "y" : "ies"}
              </span>
            ) : null}
            <span title={f.threshold_note}>
              bar for this kind of claim: {fmtInt(f.min_n)} observations
            </span>
          </div>
        </div>
      </div>
    </li>
  );
}
