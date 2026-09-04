"use client";

import { useEffect, useMemo, useState } from "react";
import { supabase } from "@/lib/supabase";
import { useQuery } from "@/lib/useQuery";

/**
 * Own the n8n execution budget from here.
 *
 * n8n bills per execution and AD4's cadences were hard-coded inside each
 * workflow's own JSON, so changing one meant opening n8n, finding the Schedule
 * Trigger and editing it - per workflow. Nobody does that, and the plan gets
 * spent on jobs nobody needed that often.
 *
 * The honest limit of what can be done from outside n8n: a Schedule Trigger
 * still fires. What this controls is whether the workflow DOES anything when
 * it does. A job set to manual exits at its first node - one trivial execution
 * instead of a run that fetches fifty-four cities. Turning the trigger off
 * entirely is still a toggle inside n8n, and the panel says so rather than
 * implying otherwise.
 */

type Mode = "auto" | "manual" | "off";
interface Entry { mode: Mode; every_minutes: number }
interface BudgetRow { job: string; mode: string; every_minutes: number; runs_per_month: number | null }

const PRESETS: Array<{ label: string; minutes: number }> = [
  { label: "15 min", minutes: 15 },
  { label: "hourly", minutes: 60 },
  { label: "2 h", minutes: 120 },
  { label: "6 h", minutes: 360 },
  { label: "12 h", minutes: 720 },
  { label: "daily", minutes: 1440 },
];

export default function ScheduleControl({ jobs }: { jobs: Array<{ job: string; label: string }> }) {
  const [draft, setDraft] = useState<Record<string, Entry> | null>(null);
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  const q = useQuery<Array<{ key: string; value: Record<string, unknown> }>>(
    () => supabase.from("settings").select("key,value").eq("key", "workflow_schedules"),
    []
  );
  const budgetQ = useQuery<BudgetRow[]>(() => supabase.from("v_execution_budget").select("*"), []);

  const stored = useMemo(() => {
    const v = (q.data ?? [])[0]?.value ?? {};
    const out: Record<string, Entry> = {};
    for (const [k, e] of Object.entries(v)) {
      if (e && typeof e === "object" && "mode" in (e as object)) {
        const o = e as { mode?: string; every_minutes?: number };
        out[k] = { mode: (o.mode as Mode) ?? "auto", every_minutes: Number(o.every_minutes ?? 0) };
      }
    }
    return out;
  }, [q.data]);

  useEffect(() => { if (!draft && Object.keys(stored).length) setDraft(stored); }, [stored, draft]);

  const cur = draft ?? stored;
  const dirty = JSON.stringify(cur) !== JSON.stringify(stored);

  // What the CURRENT draft would cost, so the consequence is visible before
  // saving rather than at the end of the month.
  const projected = useMemo(
    () =>
      Object.values(cur).reduce(
        (s, e) => s + (e.mode === "auto" && e.every_minutes > 0 ? Math.round(43200 / e.every_minutes) : 0),
        0
      ),
    [cur]
  );
  const current = (budgetQ.data ?? []).reduce((s, r) => s + (r.runs_per_month ?? 0), 0);

  async function save() {
    setSaving(true); setMsg(null);
    const note = ((q.data ?? [])[0]?.value as { note?: string } | undefined)?.note;
    const { error } = await supabase.rpc("update_setting", {
      p_key: "workflow_schedules",
      p_value: { ...(note ? { note } : {}), ...cur },
    });
    setSaving(false);
    if (error) setMsg(error.message);
    else { setMsg("Saved. Each workflow checks this on its next run."); q.refresh(); budgetQ.refresh(); }
  }

  if (q.error && /does not exist|could not find|schema cache/i.test(q.error)) return null;
  if (Object.keys(cur).length === 0) {
    return (
      <div className="rounded border border-warn/40 bg-warn/10 px-3 py-2 text-xs leading-relaxed text-warn">
        <b>Schedule control is not installed.</b> Run <code>sql/ad4_20_schedules.sql</code> to set
        each workflow&apos;s cadence from here instead of editing nine Schedule Triggers by hand.
      </div>
    );
  }

  return (
    <div className="rounded border border-border bg-panel p-3">
      <div className="mb-1 flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-sm font-semibold">Schedules &amp; execution budget</h2>
        <span className="font-mono text-[11px] text-muted">
          {dirty ? (
            <>
              <span className="line-through opacity-60">{current}</span>{" "}
              <span className={projected > current ? "text-warn" : "text-good"}>{projected}</span> runs/month
            </>
          ) : (
            <>{current} runs/month</>
          )}
        </span>
      </div>
      <p className="mb-2 max-w-3xl text-[11px] leading-relaxed text-muted">
        n8n charges per execution. <b>Auto</b> runs on its own but never more often than the
        interval; <b>Manual</b> means it only runs when you press Run, and a scheduled firing exits
        at the first node instead of doing the work. The trigger itself still fires either way —
        switching it off completely is a toggle inside n8n — but the difference between a full run
        and an immediate exit is most of the cost.
      </p>

      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead className="bg-panel2 text-[10px] uppercase tracking-wide text-muted">
            <tr>
              <th className="p-1.5 text-left">Workflow</th>
              <th className="p-1.5 text-left">Mode</th>
              <th className="p-1.5 text-left">No more often than</th>
              <th className="p-1.5 text-right">Runs/month</th>
            </tr>
          </thead>
          <tbody>
            {jobs.filter((j) => cur[j.job]).map((j) => {
              const e = cur[j.job];
              const runs = e.mode === "auto" && e.every_minutes > 0 ? Math.round(43200 / e.every_minutes) : 0;
              const set = (patch: Partial<Entry>) =>
                setDraft({ ...cur, [j.job]: { ...e, ...patch } });
              return (
                <tr key={j.job} className="border-t border-border">
                  <td className="p-1.5">{j.label}</td>
                  <td className="p-1.5">
                    <div className="flex gap-1">
                      {(["auto", "manual", "off"] as Mode[]).map((m) => (
                        <button
                          key={m}
                          onClick={() => set({ mode: m })}
                          className={`rounded border px-1.5 py-0.5 text-[10px] ${
                            e.mode === m
                              ? m === "auto" ? "border-good text-good"
                                : m === "manual" ? "border-accent text-accent"
                                : "border-bad text-bad"
                              : "border-border text-muted hover:text-text"
                          }`}
                        >
                          {m}
                        </button>
                      ))}
                    </div>
                  </td>
                  <td className="p-1.5">
                    {e.mode === "auto" ? (
                      e.every_minutes === 0 ? (
                        <span className="text-[10px] text-muted">event-driven — no cadence</span>
                      ) : (
                        <div className="flex flex-wrap gap-1">
                          {PRESETS.map((p) => (
                            <button
                              key={p.minutes}
                              onClick={() => set({ every_minutes: p.minutes })}
                              className={`rounded border px-1.5 py-0.5 text-[10px] ${
                                e.every_minutes === p.minutes
                                  ? "border-accent text-accent"
                                  : "border-border text-muted hover:text-text"
                              }`}
                            >
                              {p.label}
                            </button>
                          ))}
                        </div>
                      )
                    ) : (
                      <span className="text-[10px] text-muted">—</span>
                    )}
                  </td>
                  <td className={`p-1.5 text-right font-mono ${runs > 500 ? "text-warn" : "text-muted"}`}>
                    {e.mode === "auto" ? (e.every_minutes > 0 ? runs : "—") : 0}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <div className="mt-2 flex items-center gap-3">
        <button
          onClick={save}
          disabled={!dirty || saving}
          className={`rounded border px-3 py-1 text-xs ${
            dirty ? "border-accent text-accent hover:bg-accent/10" : "cursor-not-allowed border-border text-muted"
          }`}
        >
          {saving ? "saving…" : dirty ? "Save schedules" : "No changes"}
        </button>
        {dirty && (
          <button onClick={() => setDraft(stored)} className="text-[11px] text-muted underline hover:text-text">
            discard
          </button>
        )}
        {msg && <span className="text-[11px] text-muted">{msg}</span>}
      </div>
    </div>
  );
}
