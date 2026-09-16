"use client";

import { useEffect, useState } from "react";
import { paperAction, runPaperWorker } from "@/lib/paperSupabase";
import { fmtUsd, fmtAge } from "@/lib/format";
import { nextCycle } from "@/components/PaperPipelineStatus";

/**
 * IS THE DESK ON, AND CAN I TURN IT OFF - answered at the top, in one word,
 * with the buttons next to it.
 *
 * The switch existed. It was a checkbox reading "Pause new automatic entries",
 * inside a policy form, on a tab, behind a Save button - so turning the desk
 * off meant: pick the desk, click Automation, scroll, tick, save. Four steps
 * and a guess about which of them actually stopped it. Meanwhile nothing on
 * the page said whether it was running at all.
 *
 * FOUR STATES, and the fourth is the one worth having.
 *
 *   ACTIVE    automation on, correctly configured, will trade
 *   PAUSED    deliberately stopped; nothing new will open
 *   MANUAL    no automation at all - it only acts on tickets you write
 *   STALLED   switched ON and cannot possibly trade
 *
 * STALLED is the state the old page could not express, and it is the one that
 * wastes days. A desk with no strategies chosen, or no cities, or no spare
 * cash, looks exactly like a working desk that has not found a trade yet. It
 * will sit there for ever. So it gets its own colour and says which of the
 * three it is.
 */

type Policy = {
  strategies?: string[]; cities?: string[];
  max_plan_usd?: number; max_exposure_usd?: number; min_edge?: number;
};
export type Desk = {
  account_id: string; name: string; mode: string; entries_paused: boolean;
  cash: number | string; reserved_cash: number | string; policy: Policy;
};

const num = (v: unknown) => {
  const n = typeof v === "number" ? v : parseFloat(String(v ?? ""));
  return Number.isFinite(n) ? n : 0;
};

export type DeskState = {
  key: "ACTIVE" | "PAUSED" | "MANUAL" | "STALLED";
  dot: string; text: string; line: string;
};

/** The one sentence that explains what this desk is doing and why. */
export function deskState(desk: Desk, available: number): DeskState {
  const automated = desk.mode === "automatic" || desk.mode === "assisted";
  if (!automated) return {
    key: "MANUAL", dot: "bg-muted", text: "text-muted",
    line: "No automation. It acts only on tickets you write by hand — switch it to automatic in Settings to have strategies trade it.",
  };
  if (desk.entries_paused) return {
    key: "PAUSED", dot: "bg-warn", text: "text-warn",
    line: "Stopped. Nothing new will open. Exits and settlement still run, and queued orders can still be cancelled.",
  };
  // Switched on but unable to act. Each of these leaves the desk looking
  // healthy and idle for ever, so each is named rather than left to deduce.
  if (!desk.policy?.strategies?.length) return {
    key: "STALLED", dot: "bg-bad", text: "text-bad",
    line: "No strategies chosen. It can only act on what a strategy proposes, so it will never trade until you pick one in Settings.",
  };
  if (!desk.policy?.cities?.length) return {
    key: "STALLED", dot: "bg-bad", text: "text-bad",
    line: "No cities chosen. Every proposal will be rejected as out of policy. Pick cities, or ALL, in Settings.",
  };
  if (available < 1) return {
    key: "STALLED", dot: "bg-bad", text: "text-bad",
    line: "No available cash. Everything is either spent or reserved against queued orders, so no new plan can be funded.",
  };
  return {
    key: "ACTIVE", dot: "bg-good", text: "text-good",
    line: desk.mode === "assisted"
      ? "Running. Strategies propose; each plan waits for your approval in Settings before it is queued."
      : "Running on its own. Eligible proposals are queued and filled within the limits below.",
  };
}

const button = "rounded border border-border px-3 py-1.5 text-sm hover:bg-panel2 disabled:opacity-40";
const primary = "rounded border border-accent px-3 py-1.5 text-sm text-accent hover:bg-panel2 disabled:opacity-40";

function Stat({ label, value, tone, hint }: {
  label: string; value: string; tone?: string; hint?: string;
}) {
  return <div title={hint}>
    <div className="text-xs text-muted">{label}</div>
    <div className={`font-mono text-lg ${tone ?? ""}`}>{value}</div>
  </div>;
}

export default function PaperDeskControl({ desk, exposure, openPositions, lastFill, refresh, openSettings }: {
  desk: Desk;
  exposure: number;
  openPositions: number;
  lastFill: string | null;
  refresh: () => void;
  openSettings: () => void;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  const [canRun, setCanRun] = useState<{ configured: boolean; missing: string[] } | null>(null);

  useEffect(() => {
    fetch("/api/paper-run").then(r => r.json())
      .then(setCanRun).catch(() => setCanRun({ configured: false, missing: [] }));
  }, []);

  const available = num(desk.cash) - num(desk.reserved_cash);
  const state = deskState(desk, available);
  const cap = num(desk.policy?.max_exposure_usd);

  async function say(fn: () => Promise<string>, tag: string) {
    setBusy(tag); setNotice(null); setFailed(false);
    try { setNotice(await fn()); refresh(); }
    catch (e) { setFailed(true); setNotice(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(null); }
  }

  // One click, no form, no tab. The policy and mode are sent back exactly as
  // loaded so that flipping the switch cannot quietly rewrite anything else.
  const setPaused = (paused: boolean) => say(async () => {
    const r = await paperAction("set_policy", {
      p_account: desk.account_id, p_mode: desk.mode,
      p_paused: paused, p_policy: desk.policy,
    });
    if (r.error) throw new Error(String((r.error as { message?: string })?.message ?? r.error));
    return paused
      ? "Stopped. No new positions will open."
      : "Started. It will act on the next cycle — or press Run cycle now.";
  }, paused ? "stop" : "start");

  const runCycle = () => say(async () => {
    const r = await fetch("/api/paper-run", { method: "POST" });
    const body = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(body.error ?? "The cycle could not be started.");
    return body.note ?? "Cycle requested.";
  }, "cycle");

  const fillNow = () => say(async () => {
    const r = await runPaperWorker();
    return r.orders_completed
      ? `Filled ${r.orders_completed} queued order(s).`
      : "Nothing was claimable — no order is queued, or what was has expired.";
  }, "fill");

  const running = state.key === "ACTIVE" || state.key === "STALLED";

  return <div className="rounded border border-border bg-panel">
    <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border p-4">
      <div className="flex items-center gap-3">
        <span className={`inline-block h-3 w-3 shrink-0 rounded-full ${state.dot}`} aria-hidden />
        <div>
          <div className={`text-lg font-semibold tracking-wide ${state.text}`}>{state.key}</div>
          <div className="text-xs text-muted">{desk.name} · {desk.mode}</div>
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        {running
          ? <button className={button} disabled={!!busy} onClick={() => setPaused(true)}>
              {busy === "stop" ? "Stopping…" : "Stop"}
            </button>
          : state.key === "PAUSED"
            ? <button className={primary} disabled={!!busy} onClick={() => setPaused(false)}>
                {busy === "start" ? "Starting…" : "Start"}
              </button>
            : null}
        <button className={button} disabled={!!busy || !canRun?.configured}
                title={canRun?.configured
                  ? "Starts the full chain in GitHub Actions: signals, proposals, fills, settlement, exits."
                  : "GITHUB_DISPATCH_TOKEN is not set on this deployment. Add it in the site environment, then REDEPLOY - Vercel bakes environment variables in at build time, so one added after the last deploy is not in the running build. Or start the cycle from the repository's Actions tab."}
                onClick={runCycle}>
          {busy === "cycle" ? "Starting…" : "Run cycle now"}
        </button>
        <button className={button} disabled={!!busy} onClick={fillNow}
                title="Fills orders that are already queued. Does not look for new trades.">
          {busy === "fill" ? "Filling…" : "Fill queued"}
        </button>
        <button className={button} onClick={openSettings}>Settings</button>
      </div>
    </div>

    <p className={`px-4 pt-3 text-sm ${state.key === "ACTIVE" ? "text-muted" : state.text}`}>{state.line}</p>

    <div className="grid gap-4 p-4 sm:grid-cols-3 lg:grid-cols-5">
      <Stat label="Available cash" value={fmtUsd(available)}
            tone={available < 1 ? "text-bad" : undefined}
            hint="Cash minus what is reserved against queued orders. New plans are funded from this." />
      <Stat label="Exposure"
            value={cap ? `${fmtUsd(exposure)} / ${fmtUsd(cap)}` : fmtUsd(exposure)}
            tone={cap && exposure >= cap ? "text-warn" : undefined}
            hint={cap && exposure >= cap
              ? "At the limit — no new position can open until something closes."
              : "Cost of open positions against this desk's ceiling."} />
      <Stat label="Open positions" value={String(openPositions)}
            tone={openPositions ? "text-accent" : "text-muted"} />
      <Stat label="Last fill" value={fmtAge(lastFill)}
            tone={lastFill ? undefined : "text-muted"}
            hint={lastFill ?? "This desk has not filled an order yet."} />
      <Stat label="Next cycle"
            value={running ? `${nextCycle(new Date()).toISOString().slice(11, 16)}Z` : "—"}
            tone="text-muted"
            hint="Scheduled, not promised: GitHub delays cron by thirty minutes to three hours under load." />
    </div>

    {notice && <p role="status" className={`px-4 pb-4 text-sm ${failed ? "text-bad" : "text-good"}`}>{notice}</p>}
  </div>;
}
