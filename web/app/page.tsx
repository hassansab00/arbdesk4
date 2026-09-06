"use client";

import Link from "next/link";
import { supabase } from "@/lib/supabase";
import { useQuery } from "@/lib/useQuery";
import SignalsPanel from "@/components/SignalsPanel";
import { DataState, InlineError } from "@/components/DataState";
import PipelineStatus from "@/components/PipelineStatus";
import { fmtCompactUsd, fmtPct, fmtPp, fmtPrice, fmtUsd, pnlColor, regimeColor } from "@/lib/format";
import type { Opportunity, PaperTrade } from "@/lib/types";

export default function OverviewPage() {
  const opps = useQuery<Opportunity[]>(
    () =>
      supabase
        .from("v_opportunities")
        // NOT filtered to tradeable: with every bucket blocked - which is what
        // happens when no book snapshot exists - a filtered query returns
        // nothing and the page shows an empty box that explains nothing. Show
        // what is there and mark what cannot be traded.
        .select("*")
        .order("score", { ascending: false, nullsFirst: false })
        .limit(8),
    [],
    60000
  );

  const positions = useQuery<PaperTrade[]>(
    () => supabase.from("paper_trades").select("*").is("closed_at", null).limit(50),
    [],
    60000
  );

  const cities = useQuery<Array<{ city_key: string }>>(
    () => supabase.from("cities").select("city_key").eq("status", "active"),
    []
  );

  const signals = useQuery<Array<{ signal_id: number }>>(
    () =>
      supabase
        .from("signals")
        .select("signal_id")
        .gte("fired_at", new Date(Date.now() - 24 * 3600 * 1000).toISOString()),
    [],
    60000
  );

  const volume = useQuery<Array<{ city_key: string; volume_usd: number }>>(
    () => supabase.from("v_city_volume").select("city_key,volume_usd"),
    [],
    60000
  );

  const openList = positions.data ?? [];
  const openPnl = openList.reduce((s, p) => s + (p.net_pnl ?? 0), 0);
  const totalVolume = (volume.data ?? []).reduce((s, r) => s + (r.volume_usd ?? 0), 0);
  const oppList = opps.data ?? [];

  return (
    <div className="space-y-6">
      <h1 className="text-lg font-semibold">Overview</h1>

      {/* Every empty container below has one of a handful of causes, and they
          form a chain. Show the chain once, at the top, rather than making the
          reader assemble it from six separate "this table is empty" boxes. */}
      <PipelineStatus />

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
        <Stat label="Cities live" value={cities.loading ? "…" : String((cities.data ?? []).length)} />
        <Stat label="Open positions" value={positions.loading ? "…" : String(openList.length)} />
        <Stat label="Signals (24h)" value={signals.loading ? "…" : String((signals.data ?? []).length)} />
        <Stat label="Open P&L (net)" value={fmtUsd(openPnl, { signed: true })} color={pnlColor(openPnl)} />
        <Stat
          label="Market volume (24h)"
          value={volume.loading ? "…" : fmtCompactUsd(totalVolume)}
          hint="Traded volume across every city, from trades_observed. Distinct from book depth — this is what actually printed."
        />
      </div>
      <InlineError message={cities.error ?? signals.error ?? volume.error} />

      <section>
        <div className="mb-2 flex items-center justify-between">
          <h2 className="text-sm font-semibold text-muted">Top opportunities</h2>
          <Link href="/opportunities" className="text-xs text-accent hover:underline">view all →</Link>
        </div>
        <DataState
          relation="v_opportunities"
          loading={opps.loading}
          error={opps.error}
          isEmpty={oppList.length === 0}
          emptyTitle="Nothing priced yet"
          emptyBody={
            <>
              <code>v_opportunities</code> is empty or nothing is currently tradeable. Run the
              probability and edge engines to populate it: GitHub Actions →{" "}
              <b>Probabilities</b>, then <b>Signals</b>. If you have not run the SQL yet, start with{" "}
              <code>sql/ad4_00_preflight.sql</code> — see <code>docs/GO_LIVE.md</code>.
            </>
          }
          onRetry={opps.refresh}
        >
          <div className="overflow-x-auto rounded border border-border">
            <table className="w-full text-sm">
              <thead className="bg-panel2 text-muted">
                <tr>
                  <th className="p-2 text-left">City</th>
                  <th className="p-2 text-left">Band</th>
                  <th className="p-2 text-left">Side</th>
                  <th className="p-2 text-right">Price</th>
                  <th className="p-2 text-right">Model P</th>
                  <th className="p-2 text-right">Net edge</th>
                  <th className="p-2 text-right" title="24h traded volume on this band. Depth says what the quote can absorb; volume says whether it trades at all.">Vol 24h</th>
                  <th className="p-2 text-left">Regime</th>
                  <th className="p-2 text-left"></th>
                </tr>
              </thead>
              <tbody>
                {oppList.map((o) => (
                  <tr key={o.edge_id} className={`border-t border-border hover:bg-panel2 ${o.tradeable ? "" : "opacity-60"}`}>
                    <td className="p-2">{o.display_name ?? o.city_key}</td>
                    <td className="p-2">{o.band_label ?? `${o.band_lo}-${o.band_hi}`}</td>
                    <td className="p-2">{o.side}</td>
                    <td className="p-2 text-right font-mono">{fmtPrice(o.market_price)}</td>
                    <td className="p-2 text-right font-mono">{fmtPct(o.model_prob)}</td>
                    <td className="p-2 text-right font-mono">{fmtPp(o.edge_net_pp)}</td>
                    <td className={`p-2 text-right font-mono ${o.thin_market ? "text-warn" : ""}`}>
                      {fmtCompactUsd(o.volume_usd)}{o.thin_market ? " ⚠" : ""}
                    </td>
                    <td className={`p-2 ${regimeColor(o.regime_label)}`}>{o.regime_label}</td>
                    <td className="p-2 text-[10px] text-warn" title={o.block_reason ?? ""}>
                      {o.tradeable ? "" : "blocked"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </DataState>
      </section>

      <section>
        <h2 className="mb-2 text-sm font-semibold text-muted">Open positions</h2>
        <DataState
          relation="paper_trades"
          loading={positions.loading}
          error={positions.error}
          isEmpty={openList.length === 0}
          emptyTitle="No open positions"
          emptyBody={
            <>
              All six strategies ship <code>enabled = false</code> and nothing trades until you turn
              one on. Enable one in Supabase (<code>update strategies set enabled = true where
              strategy_id = &apos;…&apos;</code>), then let the Signals workflow run.
            </>
          }
          onRetry={positions.refresh}
        >
          <div className="overflow-x-auto rounded border border-border">
            <table className="w-full text-sm">
              <thead className="bg-panel2 text-muted">
                <tr>
                  <th className="p-2 text-left">Strategy</th>
                  <th className="p-2 text-left">Side</th>
                  <th className="p-2 text-right">Shares</th>
                  <th className="p-2 text-right">Avg fill</th>
                  <th className="p-2 text-right">Net P&amp;L</th>
                </tr>
              </thead>
              <tbody>
                {openList.map((p, i) => (
                  <tr key={p.trade_id ?? i} className="border-t border-border">
                    <td className="p-2">{p.strategy_id}</td>
                    <td className="p-2">{p.side}</td>
                    <td className="p-2 text-right font-mono">{p.shares?.toFixed(0) ?? "—"}</td>
                    <td className="p-2 text-right font-mono">{fmtPrice(p.avg_fill_price)}</td>
                    <td className={`p-2 text-right font-mono ${pnlColor(p.net_pnl)}`}>
                      {fmtUsd(p.net_pnl, { signed: true })}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </DataState>
      </section>

      {/* SIGNALS LIVE HERE NOW, not in the right-hand rail.
          A signal queue is something you sit down and work through - approve,
          dismiss, read the reason - and that is a page, not a glance column.
          Six versions in the rail proved the slot was wrong, not the panel. */}
      <section>
        <h2 className="mb-2 text-sm font-semibold">Signals awaiting you</h2>
        <div className="rounded border border-border bg-panel">
          <SignalsPanel />
        </div>
      </section>
    </div>
  );
}

function Stat({ label, value, color, hint }: { label: string; value: string; color?: string; hint?: string }) {
  return (
    <div className="rounded border border-border bg-panel p-3" title={hint}>
      <div className="text-xs text-muted">{label}</div>
      <div className={`text-xl font-mono ${color ?? ""}`}>{value}</div>
    </div>
  );
}
