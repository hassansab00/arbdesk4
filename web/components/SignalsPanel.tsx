"use client";

import { useEffect, useState } from "react";
import { supabase } from "@/lib/supabase";
import { ErrorBox, Loading } from "@/components/DataState";
import { fmtAge, fmtPrice, fmtPct, fmtPp, severityColor } from "@/lib/format";
import { fmtBandRange } from "@/lib/units";
import { fmtDaysAhead, fmtResolutionDate } from "@/lib/time";
import { signalMeaning } from "@/lib/signalText";
import type { SignalRow, Opportunity } from "@/lib/types";

/**
 * Every signal, with enough context to act on it.
 *
 * A signal row carries band_id, price, probability and edge. This panel used
 * to show none of them - only strategy_id, severity and the raw reason code -
 * so the single most common signal read "system / critical /
 * implausible_edge_anomaly" and named neither a city nor a trade.
 *
 * band_id resolves against v_opportunities, which already carries the city,
 * the band range, the unit and the resolution date. One extra query for the
 * whole panel.
 */

type BandInfo = Pick<
  Opportunity,
  "band_id" | "city_key" | "band_lo" | "band_hi" | "open_low" | "open_high" | "unit" | "resolution_date" | "band_label"
>;

export default function SignalsPanel({ onHide }: { onHide: () => void }) {
  const [signals, setSignals] = useState<SignalRow[]>([]);
  const [bands, setBands] = useState<Record<string, BandInfo>>({});
  const [expanded, setExpanded] = useState<number | null>(null);
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
      const rows = (data as SignalRow[]) ?? [];
      setSignals(rows);
      setError(null);

      // Resolve the band a signal points at, so it can show the range and the
      // settlement date. v_opportunities is the richer source but it only
      // holds bands that currently have an EDGE - so a signal about a band
      // whose market has moved on, or a database where the edge engine has not
      // run, resolved to nothing and every card read "Desk". `bands` always
      // has the row. Try the rich view first, then fill the gaps from the table.
      const ids = Array.from(new Set(rows.map((s) => s.band_id).filter(Boolean))) as string[];
      if (ids.length) {
        const map: Record<string, BandInfo> = {};
        const { data: rich } = await supabase
          .from("v_opportunities")
          .select("band_id,city_key,band_lo,band_hi,open_low,open_high,unit,resolution_date,band_label")
          .in("band_id", ids);
        for (const b of (rich as BandInfo[]) ?? []) map[b.band_id] = b;

        const missing = ids.filter((id) => !map[id]);
        if (missing.length) {
          const { data: raw } = await supabase
            .from("bands")
            .select("band_id,band_lo,band_hi,open_low,open_high,band_label")
            .in("band_id", missing);
          for (const b of (raw as Array<Omit<BandInfo, "city_key" | "unit" | "resolution_date">> ?? [])) {
            map[b.band_id] = { ...b, city_key: "", unit: "C", resolution_date: "" } as BandInfo;
          }
        }
        setBands(map);
      }
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
    // Supabase client throwing here would take down EVERY page.
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

  const pending = signals.filter((s) => s.status === "pending_approval").length;

  return (
    <div className="flex min-h-0 flex-col">
      <div className="flex items-center justify-between border-b border-border px-3 py-2">
        <div>
          <span className="text-sm font-semibold">Signals</span>
          <span className="ml-2 text-xs text-muted">
            {pending > 0 ? `${pending} awaiting you` : "nothing awaiting you"}
          </span>
        </div>
        <button className="text-xs text-muted hover:text-text" onClick={onHide}>hide</button>
      </div>

      <div className="max-h-[52vh] space-y-2 overflow-y-auto p-2">
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

        {signals.map((s) => {
          const m = signalMeaning(s.reason, s.action);
          const b = s.band_id ? bands[s.band_id] : undefined;
          const city = s.city_key || b?.city_key || "";
          const open = expanded === (s.signal_id ?? -1);
          return (
            <div
              key={s.signal_id ?? s.fired_at}
              className={`rounded border-l-4 bg-panel2 p-2.5 text-xs ${severityColor(s.severity)}`}
            >
              {/* WHERE: the city and band, or the desk itself */}
              <div className="flex items-baseline justify-between gap-2">
                <span className="font-semibold text-text">
                  {/* The signal knows its own city. The band lookup only adds
                      the RANGE - it is not what tells us where this is. */}
                  {city || (b?.city_key ?? (s.band_id ? "Unknown city" : "Desk"))}
                  {b && (
                    <span className="ml-1.5 font-mono text-accent">
                      {b.band_label ?? fmtBandRange(b.band_lo, b.band_hi, b.unit, b.open_low, b.open_high)}
                    </span>
                  )}
                </span>
                <span className="shrink-0 font-mono text-[10px] uppercase tracking-wide">{s.severity}</span>
              </div>
              {b?.resolution_date && (
                <div className="text-[10px] text-muted">
                  settles {fmtResolutionDate(b.resolution_date)} · {fmtDaysAhead(b.resolution_date)}
                </div>
              )}
              {!city && !b && s.band_id && (
                <div className="text-[10px] text-warn">
                  This signal names a band that is no longer in the book. It fired before the market
                  moved on — most likely stale rather than wrong.
                </div>
              )}

              {/* WHAT: in words */}
              <div className="mt-1.5 font-medium text-text">{m.headline}</div>

              {/* THE NUMBERS, when there is a trade behind it */}
              {(s.price_at_fire !== null || s.prob_at_fire !== null || s.edge_at_fire !== null) && (
                <div className="mt-1.5 grid grid-cols-3 gap-1 rounded bg-panel px-2 py-1.5 font-mono text-[11px]">
                  <div>
                    <div className="text-[9px] uppercase tracking-wide text-muted">Market</div>
                    {fmtPrice(s.price_at_fire)}
                  </div>
                  <div>
                    <div className="text-[9px] uppercase tracking-wide text-muted">Model</div>
                    {fmtPct(s.prob_at_fire)}
                  </div>
                  <div>
                    <div className="text-[9px] uppercase tracking-wide text-muted">Edge</div>
                    <span className={(s.edge_at_fire ?? 0) > 0 ? "text-good" : "text-bad"}>
                      {fmtPp(s.edge_at_fire)}
                    </span>
                  </div>
                </div>
              )}

              {/* WHY, on demand */}
              <button
                className="mt-1.5 text-[11px] text-muted underline decoration-dotted hover:text-text"
                onClick={() => setExpanded(open ? null : (s.signal_id ?? -1))}
              >
                {open ? "less" : "why did this fire?"}
              </button>
              {open && (
                <div className="mt-1 space-y-1.5 border-l border-border pl-2 text-[11px] leading-relaxed text-muted">
                  <p>{m.detail}</p>
                  <p className="text-text">{m.action}</p>
                  {/* Whatever raised it recorded its own context. For an
                      anomaly that is the rule that tripped and the value that
                      tripped it - the part that says whether to believe it. */}
                  {s.payload && Object.keys(s.payload).length > 0 && (
                    <pre className="overflow-x-auto rounded bg-panel px-1.5 py-1 font-mono text-[9.5px] leading-snug text-muted">
                      {JSON.stringify(s.payload, null, 1)}
                    </pre>
                  )}
                  <p className="font-mono text-[10px]">
                    {s.strategy_id} · {s.action}
                    {s.side ? ` ${s.side}` : ""} · {s.reason}
                    {s.regime_label ? ` · regime ${s.regime_label}` : ""}
                    {s.confidence !== null ? ` · confidence ${fmtPct(s.confidence, 0)}` : ""}
                  </p>
                </div>
              )}

              <div className="mt-1.5 flex items-center justify-between">
                <span className="text-[10px] text-muted">{fmtAge(s.fired_at)}</span>
                {s.status === "pending_approval" && m.tradeable ? (
                  <span className="flex gap-1.5">
                    <button onClick={() => approve(s.signal_id)} className="rounded bg-good/20 px-2 py-0.5 text-good hover:bg-good/30">Approve</button>
                    <button onClick={() => dismiss(s.signal_id)} className="rounded bg-bad/20 px-2 py-0.5 text-bad hover:bg-bad/30">Dismiss</button>
                  </span>
                ) : s.status === "pending_approval" ? (
                  // An alert about the desk has nothing to approve - offering a
                  // trade button here would be an invitation to a trade that
                  // does not exist.
                  <button onClick={() => dismiss(s.signal_id)} className="rounded border border-border px-2 py-0.5 text-muted hover:text-text">Acknowledge</button>
                ) : (
                  <span className="text-[10px] uppercase tracking-wide text-muted">{s.status}</span>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
