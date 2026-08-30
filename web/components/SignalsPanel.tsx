"use client";

import { useEffect, useState } from "react";
import { supabase } from "@/lib/supabase";
import { ErrorBox, Loading } from "@/components/DataState";
import { fmtAge, severityColor } from "@/lib/format";
import type { SignalRow } from "@/lib/types";

export default function SignalsPanel() {
  const [signals, setSignals] = useState<SignalRow[]>([]);
  const [collapsed, setCollapsed] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    try {
      const { data, error: e } = await supabase
        .from("signals")
        .select("*")
        .order("fired_at", { ascending: false })
        .limit(50);
      if (e) throw e;
      setSignals((data as SignalRow[]) ?? []);
      setError(null);
    } catch (e) {
      const msg = e && typeof e === "object" && "message" in e ? String((e as { message: unknown }).message) : String(e);
      setError(msg);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
    // Live feed via Supabase Realtime rather than polling - dedupe-aware
    // because signals.dedupe_key already prevents the same condition from
    // being logged twice within its ttl (scripts/signals.py).
    //
    // Guarded: this panel lives in the root layout, so an unconfigured
    // Supabase client (missing NEXT_PUBLIC_SUPABASE_* env vars) throwing
    // here would take down EVERY page with a blank "Application error"
    // screen instead of showing the one message the user needs.
    let channel: ReturnType<typeof supabase.channel> | null = null;
    try {
      channel = supabase
        .channel("signals-feed")
        .on("postgres_changes", { event: "INSERT", schema: "public", table: "signals" }, () => load())
        .subscribe();
    } catch (e) {
      const msg = e && typeof e === "object" && "message" in e ? String((e as { message: unknown }).message) : String(e);
      setError(msg);
      setLoading(false);
    }
    return () => {
      try {
        if (channel) supabase.removeChannel(channel);
      } catch {
        /* client was never constructed - nothing to remove */
      }
    };
  }, []);

  async function approve(id?: number) {
    if (!id) return;
    const { error: e } = await supabase.rpc("approve_signal", { p_signal_id: id });
    if (e) setError(e.message);
    load();
  }

  async function dismiss(id?: number) {
    if (!id) return;
    const { error: e } = await supabase.rpc("dismiss_signal", { p_signal_id: id });
    if (e) setError(e.message);
    load();
  }

  if (collapsed) {
    return (
      <button
        onClick={() => setCollapsed(false)}
        className="fixed right-0 top-24 rounded-l bg-panel border border-border border-r-0 px-2 py-3 text-xs text-muted hover:text-text"
      >
        Signals ({signals.filter((s) => s.status === "pending_approval").length})
      </button>
    );
  }

  return (
    <aside className="hidden lg:flex w-80 shrink-0 flex-col border-l border-border bg-panel">
      <div className="flex items-center justify-between border-b border-border px-3 py-2">
        <span className="text-sm font-semibold">Signals</span>
        <button className="text-muted hover:text-text text-xs" onClick={() => setCollapsed(true)}>hide</button>
      </div>
      <div className="flex-1 space-y-2 overflow-y-auto p-2">
        {loading && <Loading compact />}
        {error && <ErrorBox message={error} onRetry={load} compact />}
        {!loading && !error && signals.length === 0 && (
          <div className="rounded border border-dashed border-border p-3 text-xs leading-relaxed text-muted">
            <div className="font-semibold text-text">No signals yet</div>
            Every fired signal is logged here, approved or not. Nothing fires until a strategy is
            enabled — all six ship <code>enabled = false</code> — and the Signals workflow runs
            (GitHub Actions → <b>Signals</b>, 4× a day).
          </div>
        )}
        {signals.map((s) => (
          <div key={s.signal_id ?? s.fired_at} className={`rounded border-l-4 bg-panel2 p-2 text-xs ${severityColor(s.severity)}`}>
            <div className="flex justify-between">
              <span className="font-semibold">{s.strategy_id}</span>
              <span className="uppercase text-[10px]">{s.severity}</span>
            </div>
            <div className="text-text">{s.reason}</div>
            <div className="text-muted">{s.side ?? ""} {s.action} · {fmtAge(s.fired_at)}</div>
            {s.status === "pending_approval" && (
              <div className="mt-1 flex gap-2">
                <button onClick={() => approve(s.signal_id)} className="rounded bg-good/20 px-2 py-0.5 text-good hover:bg-good/30">Approve</button>
                <button onClick={() => dismiss(s.signal_id)} className="rounded bg-bad/20 px-2 py-0.5 text-bad hover:bg-bad/30">Dismiss</button>
              </div>
            )}
            {s.status !== "pending_approval" && <div className="mt-1 text-[10px] text-muted">{s.status}</div>}
          </div>
        ))}
      </div>
    </aside>
  );
}
