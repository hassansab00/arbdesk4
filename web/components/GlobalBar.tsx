"use client";

import { useEffect, useState } from "react";
import { supabase } from "@/lib/supabase";
import { fmtCompactUsd, fmtUsd, pnlColor } from "@/lib/format";

// Model cycles run 4x/day at 01:30/07:30/13:30/19:30 UTC (30 min after
// P0.2 market discovery, matching .github/workflows/pipeline_intraday.yml).
const MODEL_CYCLE_HOURS_UTC = [1.5, 7.5, 13.5, 19.5];

function nextModelCycle(now: Date): Date {
  const hoursNow = now.getUTCHours() + now.getUTCMinutes() / 60;
  const next = new Date(now);
  const upcoming = MODEL_CYCLE_HOURS_UTC.find((h) => h > hoursNow);
  if (upcoming === undefined) {
    next.setUTCDate(next.getUTCDate() + 1);
    next.setUTCHours(Math.floor(MODEL_CYCLE_HOURS_UTC[0]), (MODEL_CYCLE_HOURS_UTC[0] % 1) * 60, 0, 0);
  } else {
    next.setUTCHours(Math.floor(upcoming), (upcoming % 1) * 60, 0, 0);
  }
  return next;
}

function fmtCountdown(target: Date, now: Date): string {
  const ms = target.getTime() - now.getTime();
  if (ms <= 0) return "now";
  const totalMin = Math.floor(ms / 60000);
  const h = Math.floor(totalMin / 60);
  const m = totalMin % 60;
  return `${h}h ${m}m`;
}

export default function GlobalBar() {
  const [now, setNow] = useState<Date | null>(null);
  const [bankroll, setBankroll] = useState<number | null>(null);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [openExposure, setOpenExposure] = useState<number | null>(null);
  const [dayNetPnl, setDayNetPnl] = useState<number | null>(null);
  const [nextPeakMinutes, setNextPeakMinutes] = useState<number | null>(null);
  const [volume24h, setVolume24h] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    setNow(new Date());
    const t = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(t);
  }, []);

  useEffect(() => {
    async function load() {
      try {
        const { data: settingsRows, error: sErr } = await supabase.from("settings").select("key,value").eq("key", "bankroll");
        if (sErr) throw sErr;
        const amount = (settingsRows?.[0]?.value as { amount?: number } | undefined)?.amount ?? null;
        setBankroll(amount);

        const { data: openTrades, error: oErr } = await supabase.from("paper_trades").select("shares,avg_fill_price").is("closed_at", null);
        if (oErr) throw oErr;
        setOpenExposure((openTrades ?? []).reduce((s, t) => s + (t.shares ?? 0) * (t.avg_fill_price ?? 0), 0));

        const today = new Date().toISOString().slice(0, 10);
        const { data: closedToday, error: cErr } = await supabase.from("paper_trades").select("net_pnl,closed_at").gte("closed_at", today);
        if (cErr) throw cErr;
        setDayNetPnl((closedToday ?? []).reduce((s, t) => s + (t.net_pnl ?? 0), 0));

        const { data: live } = await supabase.from("live_weather").select("minutes_to_peak").not("minutes_to_peak", "is", null).gte("minutes_to_peak", 0).order("minutes_to_peak", { ascending: true }).limit(1);
        setNextPeakMinutes(live?.[0]?.minutes_to_peak ?? null);

        // Market volume belongs on the global bar for the same reason
        // exposure does: it is a standing condition of the whole desk, not
        // a per-page detail. A day with no volume anywhere is a day with
        // nothing to trade, however good the edges look.
        const { data: vol } = await supabase.from("v_city_volume").select("volume_usd");
        setVolume24h((vol ?? []).reduce((s: number, v: { volume_usd: number | null }) => s + (v.volume_usd ?? 0), 0));

        setError(null);
      } catch (e) {
        const msg = e && typeof e === "object" && "message" in e ? String((e as { message: unknown }).message) : String(e);
        setError(msg);
      }
    }
    load();
    const t = setInterval(load, 60000);
    return () => clearInterval(t);
  }, []);

  async function saveBankroll() {
    const amount = parseFloat(draft);
    if (Number.isNaN(amount)) return setEditing(false);
    setSaving(true);
    const { error: rpcError } = await supabase.rpc("update_setting", {
      p_key: "bankroll",
      p_value: { amount, currency: "USD", compounding: false },
    });
    setSaving(false);
    if (rpcError) {
      setError(`Could not save bankroll: ${rpcError.message}`);
    } else {
      setBankroll(amount);
      setError(null);
    }
    setEditing(false);
  }

  return (
    <>
    {error && (
      <div className="border-b border-bad/50 bg-bad/10 px-4 py-1 font-mono text-[11px] text-bad">
        {/secret api key|SECRET Supabase key/i.test(error)
          ? "Blocked: secret key in the browser - see the banner above."
          : error}
      </div>
    )}
    <div className="flex flex-wrap items-center gap-x-5 gap-y-1 border-b border-border bg-panel px-3 py-2 text-xs font-mono sm:px-4 sm:text-sm">
      <div className="flex items-center gap-2">
        <span className="text-muted">Bankroll</span>
        {editing ? (
          <input
            autoFocus
            className="w-24 rounded border border-border bg-panel2 px-1 text-text"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onBlur={saveBankroll}
            onKeyDown={(e) => e.key === "Enter" && saveBankroll()}
          />
        ) : (
          <button className="hover:text-accent" onClick={() => { setDraft(String(bankroll ?? "")); setEditing(true); }}>
            {saving ? "saving…" : bankroll === null ? "set bankroll" : fmtUsd(bankroll)}
          </button>
        )}
      </div>
      <div><span className="text-muted">Open exposure</span> {fmtUsd(openExposure)}</div>
      <div><span className="text-muted">Day P&amp;L (net)</span> <span className={pnlColor(dayNetPnl)}>{fmtUsd(dayNetPnl, { signed: true })}</span></div>
      <div><span className="text-muted">UTC</span> {now ? now.toISOString().slice(11, 19) : "--:--:--"}</div>
      <div><span className="text-muted">Next model cycle</span> {now ? fmtCountdown(nextModelCycle(now), now) : "—"}</div>
      <div title="Traded volume across every city in the last 24h, from trades_observed."><span className="text-muted">Vol 24h</span> {volume24h !== null ? fmtCompactUsd(volume24h) : "—"}</div>
      <div className="hidden sm:block"><span className="text-muted">Next peak window</span> {nextPeakMinutes !== null ? `${nextPeakMinutes}m` : "—"}</div>
    </div>
    </>
  );
}
