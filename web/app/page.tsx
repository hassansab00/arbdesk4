"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { supabase } from "@/lib/supabase";
import { fmtPct, fmtPp, fmtPrice, fmtUsd, pnlColor, regimeColor } from "@/lib/format";
import type { Opportunity, PaperTrade } from "@/lib/types";

export default function OverviewPage() {
  const [opportunities, setOpportunities] = useState<Opportunity[]>([]);
  const [openPositions, setOpenPositions] = useState<PaperTrade[]>([]);
  const [cityCount, setCityCount] = useState<number>(0);
  const [signalCount24h, setSignalCount24h] = useState<number>(0);

  useEffect(() => {
    async function load() {
      const { data: opps } = await supabase
        .from("v_opportunities")
        .select("*")
        .eq("tradeable", true)
        .order("score", { ascending: false, nullsFirst: false })
        .limit(8);
      setOpportunities((opps as Opportunity[]) ?? []);

      const { data: positions } = await supabase.from("paper_trades").select("*").is("closed_at", null).limit(10);
      setOpenPositions((positions as PaperTrade[]) ?? []);

      const { count } = await supabase.from("cities").select("*", { count: "exact", head: true }).eq("status", "active");
      setCityCount(count ?? 0);

      const since = new Date(Date.now() - 24 * 3600 * 1000).toISOString();
      const { count: sigCount } = await supabase.from("signals").select("*", { count: "exact", head: true }).gte("fired_at", since);
      setSignalCount24h(sigCount ?? 0);
    }
    load();
  }, []);

  const dayNet = openPositions.reduce((s, p) => s + (p.net_pnl ?? 0), 0);

  return (
    <div className="space-y-6">
      <h1 className="text-lg font-semibold">Overview</h1>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Stat label="Cities live" value={String(cityCount)} />
        <Stat label="Open positions" value={String(openPositions.length)} />
        <Stat label="Signals (24h)" value={String(signalCount24h)} />
        <Stat label="Open P&L (net)" value={fmtUsd(dayNet, { signed: true })} color={pnlColor(dayNet)} />
      </div>

      <section>
        <div className="mb-2 flex items-center justify-between">
          <h2 className="text-sm font-semibold text-muted">Top opportunities</h2>
          <Link href="/opportunities" className="text-xs text-accent hover:underline">view all →</Link>
        </div>
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
                <th className="p-2 text-left">Regime</th>
              </tr>
            </thead>
            <tbody>
              {opportunities.map((o) => (
                <tr key={o.edge_id} className="border-t border-border hover:bg-panel2">
                  <td className="p-2">{o.display_name ?? o.city_key}</td>
                  <td className="p-2">{o.band_label ?? `${o.band_lo}-${o.band_hi}`}</td>
                  <td className="p-2">{o.side}</td>
                  <td className="p-2 text-right font-mono">{fmtPrice(o.market_price)}</td>
                  <td className="p-2 text-right font-mono">{fmtPct(o.model_prob)}</td>
                  <td className="p-2 text-right font-mono">{fmtPp(o.edge_net_pp)}</td>
                  <td className={`p-2 ${regimeColor(o.regime_label)}`}>{o.regime_label}</td>
                </tr>
              ))}
              {opportunities.length === 0 && (
                <tr><td colSpan={7} className="p-4 text-center text-muted">No tradeable opportunities right now.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </section>

      <section>
        <h2 className="mb-2 text-sm font-semibold text-muted">Open positions</h2>
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
              {openPositions.map((p, i) => (
                <tr key={i} className="border-t border-border">
                  <td className="p-2">{p.strategy_id}</td>
                  <td className="p-2">{p.side}</td>
                  <td className="p-2 text-right font-mono">{p.shares.toFixed(0)}</td>
                  <td className="p-2 text-right font-mono">{fmtPrice(p.avg_fill_price)}</td>
                  <td className={`p-2 text-right font-mono ${pnlColor(p.net_pnl)}`}>{fmtUsd(p.net_pnl, { signed: true })}</td>
                </tr>
              ))}
              {openPositions.length === 0 && (
                <tr><td colSpan={5} className="p-4 text-center text-muted">No open positions. All six strategies ship disabled until enabled.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}

function Stat({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div className="rounded border border-border bg-panel p-3">
      <div className="text-xs text-muted">{label}</div>
      <div className={`text-xl font-mono ${color ?? ""}`}>{value}</div>
    </div>
  );
}
