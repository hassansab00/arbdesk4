"use client";

import { supabase } from "@/lib/supabase";
import { useQuery } from "@/lib/useQuery";
import { fmtAge } from "@/lib/format";

/**
 * IS CALIBRATION PENDING, RUNNING, FAILED, OR IN FORCE?
 *
 * The page showed calibrated numbers and never said whether a calibration was
 * actually applied. It is not: calibration.py has fitted a map every day since
 * 16 Sep and withheld every one of them, because the gate wants 30 distinct
 * settlement dates and the desk has 8.
 *
 * TWO FACTS, AND SHOWING ONLY ONE WOULD MISLEAD. The countdown is real - 22
 * more days of settlements, roughly three weeks. But on the evidence that
 * exists the fit currently makes the out-of-sample Brier WORSE (0.727 ->
 * 0.741), so reaching 30 dates is necessary and not sufficient. A progress
 * bar alone would promise a switch-on that the measurement does not support.
 */
type Status = {
  last_run_at: string | null;
  run_status: string;
  applies: boolean;
  method: string | null;
  settlement_dates: number | null;
  complete_ladders: number | null;
  gate_unmet: string[] | null;
  brier_before: number | null;
  brier_after: number | null;
  note: string | null;
  validation_improves: boolean | null;
  state: string;
};

const LOOK: Record<string, { label: string; tone: string; means: string }> = {
  applied: {
    label: "In force",
    tone: "text-good border-good/40",
    means: "Prices on this page carry the fitted map.",
  },
  pending_evidence: {
    label: "Pending",
    tone: "text-warn border-warn/40",
    means: "Under 7 settlement dates: a map is fitted every night and none can apply yet. From 7 dates it updates weekly, and at 30 it runs a full recalibration. Raw probabilities are pricing.",
  },
  fitted_not_applied: {
    label: "Fitted, not applied",
    tone: "text-warn border-warn/40",
    means: "The last weekly update did not beat the uncalibrated numbers on days it never saw, so raw probabilities are pricing until the next one.",
  },
  failed: {
    label: "Failed",
    tone: "text-bad border-bad/40",
    means: "The last run raised. Nothing has been refitted since.",
  },
  stale: {
    label: "Stale",
    tone: "text-bad border-bad/40",
    means: "It has not run since yesterday — look at the daily pipeline, not the map.",
  },
  stopped: {
    label: "Stopped",
    tone: "text-muted border-border",
    means: "The nightly refit was switched off by the WXPredict build (wave A.3), and no fitted map is in force, so raw probabilities price. WXPredict calibrates itself.",
  },
  never_run: {
    label: "Never run",
    tone: "text-muted border-border",
    means: "No calibration run is on record.",
  },
};

export default function CalibrationStatus() {
  const q = useQuery<Status[]>(
    () => supabase.from("v_calibration_status").select("*"),
    [],
    60000
  );
  const s = q.data?.[0];
  if (q.error || !s) return null;
  const look = LOOK[s.state] ?? LOOK.never_run;
  const gates = Array.isArray(s.gate_unmet) ? s.gate_unmet : [];

  return (
    <div className={`rounded border bg-panel p-3 text-xs ${look.tone.split(" ")[1] ?? "border-border"}`}>
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <span className="font-semibold">Calibration</span>
        <span className={`font-semibold ${look.tone.split(" ")[0]}`}>{look.label}</span>
        {s.method && <code className="text-[10px] text-muted">{s.method}</code>}
        {s.last_run_at && (
          <span className="text-muted">last run {fmtAge(s.last_run_at)}</span>
        )}
      </div>

      <p className="mt-1 text-muted">{look.means}</p>

      {/* WHAT IT IS WAITING ON, counted. "Pending" without a number is the
          same non-answer as "nothing has met its conditions". */}
      {gates.length > 0 && (
        <p className="mt-1">
          <span className="text-muted">Waiting on: </span>
          <span className="font-mono">{gates.join(" · ")}</span>
        </p>
      )}

      {s.settlement_dates != null && (
        <p className="mt-0.5 font-mono text-muted">
          {s.settlement_dates} settlement date{s.settlement_dates === 1 ? "" : "s"}
          {s.complete_ladders != null && ` · ${s.complete_ladders.toLocaleString()} complete ladders`}
        </p>
      )}

      {/* AND WHETHER WAITING WOULD EVEN HELP. This is the part a countdown
          hides: on today's evidence the fit is worse out of sample, so the
          date gate is necessary and not sufficient. */}
      {s.brier_before != null && s.brier_after != null && (
        <p className={`mt-0.5 ${s.validation_improves ? "text-good" : "text-warn"}`}>
          Out-of-sample Brier {Number(s.brier_before).toFixed(4)} →{" "}
          {Number(s.brier_after).toFixed(4)}
          {s.validation_improves
            ? " — the fit improves on the raw numbers"
            : " — the fit is worse than the raw numbers, so more dates alone will not switch it on"}
        </p>
      )}

      {s.note && <p className="mt-1 text-muted">{s.note}</p>}
    </div>
  );
}
