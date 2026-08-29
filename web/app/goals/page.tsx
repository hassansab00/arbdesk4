"use client";

import { useEffect, useState } from "react";
import { supabase } from "@/lib/supabase";
import { fmtPct, fmtUsd, pnlColor } from "@/lib/format";

interface Goal { target_usd: number; period_days: number; started_at: string; }

export default function GoalsPage() {
  const [goal, setGoal] = useState<Goal | null>(null);
  const [targetUsd, setTargetUsd] = useState("1000");
  const [periodDays, setPeriodDays] = useState("30");
  const [progress, setProgress] = useState(0);
  const [capacity5c, setCapacity5c] = useState<number | null>(null);

  async function load() {
    const { data } = await supabase.from("settings").select("value").eq("key", "goal");
    const g = data?.[0]?.value as Goal | undefined;
    if (g) { setGoal(g); setTargetUsd(String(g.target_usd)); setPeriodDays(String(g.period_days)); }

    if (g) {
      const { data: trades } = await supabase.from("paper_trades").select("net_pnl,closed_at").gte("closed_at", g.started_at);
      setProgress((trades ?? []).reduce((s: number, t: any) => s + (t.net_pnl ?? 0), 0));
    }

    const { data: cap } = await supabase.from("derived_capacity").select("usd_at_5c");
    setCapacity5c((cap ?? []).reduce((s: number, c: any) => s + (c.usd_at_5c ?? 0), 0));
  }

  useEffect(() => { load(); }, []);

  async function saveGoal() {
    const g: Goal = { target_usd: parseFloat(targetUsd) || 0, period_days: parseInt(periodDays) || 30, started_at: new Date().toISOString() };
    await supabase.rpc("update_setting", { p_key: "goal", p_value: g });
    load();
  }

  const daysElapsed = goal ? Math.max(1, (Date.now() - new Date(goal.started_at).getTime()) / (24 * 3600 * 1000)) : 0;
  const daysRemaining = goal ? Math.max(0, goal.period_days - daysElapsed) : 0;
  const remainingUsd = goal ? goal.target_usd - progress : 0;
  const requiredDailyEv = goal && daysRemaining > 0 ? remainingUsd / daysRemaining : null;

  // Reality-check: is required daily EV achievable given today's total
  // 5c-slippage capacity across all cities? A rough, honestly-labelled
  // sanity check, not a guaranteed forecast.
  const achievable = requiredDailyEv !== null && capacity5c !== null
    ? requiredDailyEv <= capacity5c * 0.05 // assume at most ~5% of available depth converts to daily EV - provisional
    : null;

  return (
    <div className="max-w-xl space-y-6">
      <h1 className="text-lg font-semibold">Goals</h1>

      <div className="grid gap-2 rounded border border-border bg-panel p-4 sm:grid-cols-2">
        <label className="block">
          <div className="text-[10px] text-muted">Target profit (USD)</div>
          <input value={targetUsd} onChange={(e) => setTargetUsd(e.target.value)} className="input" />
        </label>
        <label className="block">
          <div className="text-[10px] text-muted">Period (days)</div>
          <input value={periodDays} onChange={(e) => setPeriodDays(e.target.value)} className="input" />
        </label>
        <button onClick={saveGoal} className="rounded bg-accent py-1.5 text-white hover:opacity-90 sm:col-span-2">Set goal</button>
      </div>

      {goal && (
        <div className="space-y-3 rounded border border-border bg-panel p-4 text-sm">
          <div className="flex justify-between"><span className="text-muted">Progress</span><span className={pnlColor(progress)}>{fmtUsd(progress, { signed: true })} / {fmtUsd(goal.target_usd)}</span></div>
          <div className="h-2 rounded bg-panel2">
            <div className="h-2 rounded bg-accent" style={{ width: `${Math.max(0, Math.min(100, (progress / goal.target_usd) * 100))}%` }} />
          </div>
          <div className="flex justify-between text-muted"><span>Days remaining</span><span className="font-mono">{daysRemaining.toFixed(0)}</span></div>
          <div className="flex justify-between text-muted"><span>Required daily EV</span><span className="font-mono">{requiredDailyEv !== null ? fmtUsd(requiredDailyEv) : "goal met"}</span></div>

          {achievable !== null && (
            <div className={`rounded p-2 text-xs ${achievable ? "bg-good/10 text-good" : "bg-bad/10 text-bad"}`}>
              {achievable
                ? "Required daily EV looks achievable relative to today's aggregate 5c-slippage capacity - not a guarantee, a rough sanity check."
                : "Not achievable at current capacity: required daily EV exceeds what today's book depth across all cities can plausibly support. State this plainly rather than implying the target is on track."}
            </div>
          )}
        </div>
      )}
      {!goal && <div className="text-muted text-sm">No goal set yet.</div>}
    </div>
  );
}
