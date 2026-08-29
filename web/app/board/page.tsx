"use client";

import { useEffect, useMemo, useState } from "react";
import { supabase } from "@/lib/supabase";
import { fmtPct, fmtPp, fmtPrice, fmtUsd, regimeColor } from "@/lib/format";
import type { Opportunity } from "@/lib/types";

type SortKey = "edge_net_pp" | "model_prob" | "market_price" | "fillable_usd_5c" | "city_key";

export default function BoardPage() {
  const [rows, setRows] = useState<Opportunity[]>([]);
  const [cityFilter, setCityFilter] = useState("");
  const [sideFilter, setSideFilter] = useState<"ALL" | "YES" | "NO">("ALL");
  const [sortKey, setSortKey] = useState<SortKey>("edge_net_pp");
  const [sortDesc, setSortDesc] = useState(true);
  const [showUntradeable, setShowUntradeable] = useState(false);

  useEffect(() => {
    async function load() {
      let query = supabase.from("v_opportunities").select("*").limit(2000);
      if (!showUntradeable) query = query.eq("tradeable", true);
      const { data } = await query;
      setRows((data as Opportunity[]) ?? []);
    }
    load();
    const t = setInterval(load, 30000);
    return () => clearInterval(t);
  }, [showUntradeable]);

  const cities = useMemo(() => Array.from(new Set(rows.map((r) => r.display_name ?? r.city_key))).sort(), [rows]);

  const filtered = useMemo(() => {
    let out = rows;
    if (cityFilter) out = out.filter((r) => (r.display_name ?? r.city_key) === cityFilter);
    if (sideFilter !== "ALL") out = out.filter((r) => r.side === sideFilter);
    const sorted = [...out].sort((a, b) => {
      const av = a[sortKey] ?? -Infinity;
      const bv = b[sortKey] ?? -Infinity;
      if (typeof av === "string" || typeof bv === "string") {
        return sortDesc ? String(bv).localeCompare(String(av)) : String(av).localeCompare(String(bv));
      }
      return sortDesc ? (bv as number) - (av as number) : (av as number) - (bv as number);
    });
    return sorted;
  }, [rows, cityFilter, sideFilter, sortKey, sortDesc]);

  function headerClick(key: SortKey) {
    if (key === sortKey) setSortDesc((d) => !d);
    else { setSortKey(key); setSortDesc(true); }
  }

  return (
    <div className="space-y-4">
      <h1 className="text-lg font-semibold">Board</h1>
      <p className="text-xs text-muted">
        Market price is executable (depth-weighted), never top-of-book. Market state thresholds
        (§2.4) are provisional placeholders, UI-settable.
      </p>

      <div className="flex flex-wrap gap-3 text-sm">
        <select value={cityFilter} onChange={(e) => setCityFilter(e.target.value)} className="rounded border border-border bg-panel2 px-2 py-1">
          <option value="">All cities</option>
          {cities.map((c) => <option key={c} value={c}>{c}</option>)}
        </select>
        <select value={sideFilter} onChange={(e) => setSideFilter(e.target.value as any)} className="rounded border border-border bg-panel2 px-2 py-1">
          <option value="ALL">Both sides</option>
          <option value="YES">YES only</option>
          <option value="NO">NO only</option>
        </select>
        <label className="flex items-center gap-1 text-muted">
          <input type="checkbox" checked={showUntradeable} onChange={(e) => setShowUntradeable(e.target.checked)} />
          show untradeable
        </label>
      </div>

      <div className="overflow-x-auto rounded border border-border">
        <table className="w-full text-sm">
          <thead className="bg-panel2 text-muted select-none">
            <tr>
              <Th onClick={() => headerClick("city_key")}>City</Th>
              <th className="p-2 text-left">Band</th>
              <th className="p-2 text-left">Side</th>
              <Th onClick={() => headerClick("market_price")} align="right">Price</Th>
              <Th onClick={() => headerClick("model_prob")} align="right">Model P</Th>
              <Th onClick={() => headerClick("edge_net_pp")} align="right">Net edge</Th>
              <Th onClick={() => headerClick("fillable_usd_5c")} align="right">Depth (5c)</Th>
              <th className="p-2 text-left">State</th>
              <th className="p-2 text-left">Regime</th>
              <th className="p-2 text-left">Block reason</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((o) => (
              <tr key={`${o.band_id}-${o.side}`} className={`border-t border-border hover:bg-panel2 ${!o.tradeable ? "opacity-50" : ""}`}>
                <td className="p-2">{o.display_name ?? o.city_key}</td>
                <td className="p-2">{o.band_label ?? `${o.band_lo ?? "..."}-${o.band_hi ?? "..."}`}</td>
                <td className="p-2">{o.side}</td>
                <td className="p-2 text-right font-mono">{fmtPrice(o.market_price)}</td>
                <td className="p-2 text-right font-mono">{fmtPct(o.model_prob)}</td>
                <td className="p-2 text-right font-mono">{fmtPp(o.edge_net_pp)}</td>
                <td className="p-2 text-right font-mono">{fmtUsd(o.fillable_usd_5c)}</td>
                <td className="p-2 text-xs">{o.market_state}</td>
                <td className={`p-2 ${regimeColor(o.regime_label)}`}>{o.regime_label}</td>
                <td className="p-2 text-xs text-muted">{o.block_reason ?? ""}</td>
              </tr>
            ))}
            {filtered.length === 0 && (
              <tr><td colSpan={10} className="p-6 text-center text-muted">No rows match these filters.</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function Th({ children, onClick, align = "left" }: { children: React.ReactNode; onClick: () => void; align?: "left" | "right" }) {
  return (
    <th onClick={onClick} className={`p-2 cursor-pointer hover:text-text text-${align}`}>
      {children}
    </th>
  );
}
