"use client";

import { useState } from "react";
import { supabase } from "@/lib/supabase";
import { useQuery } from "@/lib/useQuery";
import { runPaperWorker, describeWorker } from "@/lib/paperSupabase";
import { fmtAge, fmtInt } from "@/lib/format";

/**
 * WHY IS THE DESK DOING NOTHING? - answered on the page instead of in a log.
 *
 * The automatic desk is a chain of five scripts and a failure anywhere in it
 * looks identical from the outside: no orders. On 16 Sep the chain had been
 * running for hours, firing 90-111 signals a cycle, and producing zero
 * proposals - and nothing in the platform said so. The diagnosis needed a
 * Supabase query against ingest_log, which is not a thing a desk owner should
 * have to do to find out whether their desk is alive.
 *
 * Each step shows the same three facts: when it last ran, whether it
 * succeeded, and the ONE number that says whether it did anything. A step that
 * succeeds and produces nothing is the interesting case - it is how this chain
 * fails - so a zero is coloured, not hidden.
 */

const STEPS = [
  { job: "signal_engine", label: "Signals",
    count: (d: Detail) => d.fired,
    unit: "fired",
    blank: "No strategy fired. Check that strategies are enabled and the board has priced edges." },
  { job: "paper_plans", label: "Proposals",
    count: (d: Detail) => d.proposals,
    unit: "proposed",
    blank: "Signals fired but none became a plan. The Plans list below names the reason for each." },
  { job: "paper_worker", label: "Fills",
    count: (d: Detail) => d.orders_completed,
    unit: "filled",
    blank: "No order was claimable. Orders expire five minutes after their plan, so a late worker finds nothing." },
  { job: "paper_settlement", label: "Settlement",
    count: (d: Detail) => d.evidence_captured ?? d.bands_checked,
    unit: "proofs",
    blank: "No resolution proof captured this run. Markets settle when the venue closes them, not when the day ends." },
  { job: "paper_exits", label: "Exits",
    count: (d: Detail) => d.exits_queued,
    unit: "queued",
    blank: "No exit queued. Exits only act when a desk has auto_exit_enabled and a position clears its threshold." },
];

type Detail = Record<string, number | undefined>;
type Run = { job: string; started_at: string; status: string; detail: Detail | null };

// pipeline_intraday.yml: '15 0,4,8,12,16,20 * * *'. Stated here so the page can
// say when the next cycle is due rather than leaving the owner to guess - but
// stated as SCHEDULED, because GitHub delays cron under load by anything from
// thirty minutes to three hours and this page must not imply a promise the
// runner does not make.
const CYCLE_HOURS = [0, 4, 8, 12, 16, 20];

export function nextCycle(now: Date): Date {
  const next = new Date(now);
  next.setUTCMinutes(15, 0, 0);
  for (const h of CYCLE_HOURS) {
    next.setUTCHours(h);
    if (next > now) return next;
  }
  next.setUTCDate(next.getUTCDate() + 1);
  next.setUTCHours(CYCLE_HOURS[0]);
  return next;
}

const card = "rounded border border-border bg-panel p-4";
const button = "rounded border border-border px-3 py-1 text-sm hover:bg-panel2 disabled:opacity-40";

export default function PaperPipelineStatus({ refresh }: { refresh: () => void }) {
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  // One row per job: the newest run of each. Ordered newest-first and folded
  // in the browser, because DISTINCT ON is not expressible through PostgREST
  // and five jobs over two days is a small enough window to fold honestly.
  const runs = useQuery<Run[]>(async () => {
    const since = new Date(Date.now() - 3 * 86400_000).toISOString();
    const r = await supabase.from("ingest_log")
      .select("job,started_at,status,detail")
      .in("job", STEPS.map(s => s.job))
      .gte("started_at", since)
      .order("started_at", { ascending: false })
      .limit(500);
    return { data: r.data as Run[] | null, error: r.error };
  }, [], 30000, 500);

  const latest = new Map<string, Run>();
  for (const run of runs.data ?? []) {
    if (!latest.has(run.job)) latest.set(run.job, run);
  }

  const due = nextCycle(new Date());

  async function fillNow() {
    setBusy(true); setNotice(null); setFailed(false);
    try {
      setNotice(describeWorker(await runPaperWorker()));
      runs.refresh(); refresh();
    } catch (e) {
      setFailed(true);
      setNotice(e instanceof Error ? e.message : "The worker could not be reached.");
    } finally {
      setBusy(false);
    }
  }

  return <div className={`${card} space-y-3`}>
    <div className="flex flex-wrap items-baseline justify-between gap-2">
      <h2 className="font-semibold">Automation</h2>
      <div className="text-xs text-muted">
        Runs in GitHub Actions every four hours · next scheduled{" "}
        <span className="text-text">{due.toISOString().slice(11, 16)}Z</span>
        <span title="GitHub delays scheduled workflows under load, by anything from thirty minutes to three hours. This is when it is due, not a promise.">
          {" "}(often late)
        </span>
      </div>
    </div>

    {runs.error && <div role="alert" className="text-sm text-bad">{runs.error}</div>}

    <div className="grid gap-2 sm:grid-cols-3 lg:grid-cols-5">
      {STEPS.map(step => {
        const run = latest.get(step.job);
        const n = run?.detail ? step.count(run.detail) : undefined;
        const ran = !!run;
        const ok = run?.status === "ok";
        const idle = ran && ok && !n;
        return <div key={step.job} className="rounded border border-border p-3"
                    title={idle ? step.blank : undefined}>
          <div className="text-xs text-muted">{step.label}</div>
          <div className={`font-mono text-xl ${!ran ? "text-muted" : !ok ? "text-bad" : idle ? "text-warn" : "text-good"}`}>
            {ran ? fmtInt(n ?? 0) : "—"}
          </div>
          <div className="text-xs text-muted">
            {ran ? `${step.unit} · ${fmtAge(run.started_at)}` : "never run"}
          </div>
          {ran && !ok && <div className="text-xs text-bad">{run.status}</div>}
        </div>;
      })}
    </div>

    {/* A zero that succeeded is the failure mode of this chain, so the first
        step that produced nothing explains itself in words rather than leaving
        five amber tiles to be interpreted. */}
    {(() => {
      const stuck = STEPS.find(s => {
        const run = latest.get(s.job);
        return run && run.status === "ok" && !s.count(run.detail ?? {});
      });
      const never = STEPS.find(s => !latest.get(s.job));
      if (never) return <p className="text-sm text-warn">
        <strong>{never.label}</strong> has not run in the last three days. The chain stops there.
      </p>;
      if (stuck) return <p className="text-sm text-warn">
        <strong>{stuck.label}</strong> ran and produced nothing. {stuck.blank}
      </p>;
      return <p className="text-sm text-good">Every step ran and produced something on its last cycle.</p>;
    })()}

    <div className="flex flex-wrap items-center gap-3">
      <button className={button} disabled={busy} onClick={fillNow}>
        {busy ? "Filling…" : "Fill queued orders now"}
      </button>
      {/* It was "the only step this page can trigger", which stopped being
          true when Run cycle now went in - and the fill itself only started
          working when it was pointed at GitHub Actions instead of a worker
          service that was never deployed. */}
      <span className="text-xs text-muted">
        Runs the fill step on its own, in GitHub Actions, about a minute after you press it. Use{" "}
        <strong>Run cycle now</strong> above for the whole chain — signals, proposals, fills,
        settlement, exits.
      </span>
    </div>
    {notice && <div role="status" className={`text-sm ${failed ? "text-bad" : "text-good"}`}>{notice}</div>}
  </div>;
}
