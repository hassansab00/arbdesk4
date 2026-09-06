"use client";

import { useMemo } from "react";
import Link from "next/link";
import { supabase } from "@/lib/supabase";
import { useQuery } from "@/lib/useQuery";
import { DataState } from "@/components/DataState";
import { Freshness } from "@/components/Provenance";
import RefreshButton from "@/components/RefreshButton";
import { fmtInt } from "@/lib/format";

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

interface LearningStage {
  stage: string;
  step: number;
  rows: number;
  last_run: string | null;
  detail: string;
  job: string;
  state: "not started" | "collecting" | "ready";
  why: string;
}

function since(ts: string | null): string {
  if (!ts) return "never";
  const h = (Date.now() - new Date(ts).getTime()) / 3600000;
  if (h < 1) return "just now";
  if (h < 48) return `${Math.round(h)} h ago`;
  return `${Math.round(h / 24)} days ago`;
}

export default function SynthesisPage() {
  const findingsQ = useQuery<Finding[]>(
    () => supabase.from("v_synthesis_findings").select("*"),
    [],
    180000
  );
  const learningQ = useQuery<LearningStage[]>(
    () => supabase.from("v_learning_state").select("*").order("step"),
    [],
    180000
  );

  const findings = findingsQ.data ?? [];
  const backward = useMemo(
    () => findings.filter((f) => f.direction === "backward")
                  .sort((a, b) => (a.status === b.status ? b.n - a.n : a.status === "established" ? -1 : 1)),
    [findings]
  );
  const forward = useMemo(() => findings.filter((f) => f.direction === "forward"), [findings]);
  const stages = learningQ.data ?? [];

  // Where the chain stops. Learning is sequential: nothing can be measured
  // from a day that has not settled, nothing fitted from accuracy that has
  // not been measured, nothing predicted from a fit that does not exist. The
  // first stage that is not ready is the only one worth acting on.
  const blocked = stages.find((s) => s.state !== "ready") ?? null;

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
      </div>

      {/* ===================================================== LEARNING STATE */}
      <section className="space-y-3">
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <h2 className="text-sm font-semibold">Is the desk still learning?</h2>
          <Freshness relation="derived_weather_model" />
        </div>
        <p className="max-w-3xl text-xs leading-relaxed text-muted">
          Learning is not a mood, it is four dated artefacts, each written by a named job and each
          needing the one before it. A day has to settle before its accuracy can be measured;
          accuracy has to be measured before a correction can be fitted; a correction has to exist
          before a corrected forecast can be produced. The first stage below that is not ready is
          the only one worth doing anything about.
        </p>

        <DataState
          relation="v_learning_state"
          loading={learningQ.loading}
          error={learningQ.error}
          isEmpty={stages.length === 0}
          emptyTitle="Learning state unavailable"
          emptyBody="Run sql/ad4_40_synthesis.sql to publish it."
          onRetry={learningQ.refresh}
        >
          <div className="space-y-2">
            <ol className="grid gap-2 md:grid-cols-2 xl:grid-cols-4">
              {stages.map((s) => {
                const tone =
                  s.state === "ready" ? "border-good/50" : s.state === "collecting" ? "border-warn/50" : "border-border";
                const dot =
                  s.state === "ready" ? "bg-good" : s.state === "collecting" ? "bg-warn" : "bg-muted";
                const isBlocker = blocked?.stage === s.stage;
                return (
                  <li
                    key={s.stage}
                    className={`rounded border ${tone} bg-panel/60 p-3 ${isBlocker ? "ring-1 ring-warn/40" : ""}`}
                  >
                    <div className="flex items-center gap-2">
                      <span className={`h-2 w-2 rounded-full ${dot}`} />
                      <span className="text-xs font-semibold capitalize">
                        {s.step}. {s.stage}
                      </span>
                      <span className="ml-auto text-[10px] text-muted">{since(s.last_run)}</span>
                    </div>
                    <div className="mt-1.5 text-xs tabular-nums text-text">{s.detail}</div>
                    <p className="mt-1 text-[10px] leading-snug text-muted">{s.why}</p>
                    <div className="mt-1.5 text-[10px] text-muted">
                      written by <span className="text-text">{s.job}</span>
                    </div>
                  </li>
                );
              })}
            </ol>

            {/* ---- the one thing to do about it ------------------------- */}
            <div className="rounded border border-border bg-panel2/40 p-3">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div className="max-w-2xl">
                  <div className="text-xs font-semibold">
                    {blocked
                      ? `Blocked at “${blocked.stage}”`
                      : "Every stage is current — the desk is learning from everything that has settled"}
                  </div>
                  <p className="mt-0.5 text-[11px] leading-relaxed text-muted">
                    {blocked ? (
                      <>
                        {blocked.why} Run <span className="text-text">{blocked.job}</span> — nothing
                        after it can move until it does.
                      </>
                    ) : (
                      <>
                        Re-running teaches it from days that have settled since the last fit. It is
                        safe at any time; each stage only re-reads what it needs.
                      </>
                    )}
                  </p>
                </div>
                <RefreshButton
                  job="P2.1_relearn"
                  label="Learn from what has settled"
                  onDone={() => {
                    learningQ.refresh();
                    findingsQ.refresh();
                  }}
                />
              </div>
              <p className="mt-2 text-[10px] leading-relaxed text-muted">
                The button fires the <code className="rounded bg-panel2 px-1">P2.1_relearn</code>{" "}
                workflow, which asks GitHub to run the four learning Actions in order. The token
                that lets it do that lives in n8n, never in this page — a browser that could start
                a workflow is a browser holding a credential.{" "}
                <Link href="/workflows" className="text-accent hover:underline">
                  Workflows
                </Link>{" "}
                is where its URL is set, and where each Action&apos;s last run is shown.
              </p>
            </div>
          </div>
        </DataState>
      </section>

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
