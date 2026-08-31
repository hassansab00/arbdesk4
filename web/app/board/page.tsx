"use client";

import { useMemo, useState } from "react";
import { supabase } from "@/lib/supabase";
import { useQuery } from "@/lib/useQuery";
import { DataState } from "@/components/DataState";
import { fmtAge, fmtCompactUsd, fmtInt, fmtPct, fmtPp, fmtPrice, fmtUsd, regimeColor } from "@/lib/format";
import type { Opportunity } from "@/lib/types";

type SortKey = "score" | "edge_net_pp" | "model_prob" | "market_price" | "fillable_usd_5c" | "volume_usd" | "city_key";

export default function BoardPage() {
  const [cityFilter, setCityFilter] = useState("");
  const [sideFilter, setSideFilter] = useState<"ALL" | "YES" | "NO">("ALL");
  const [sortKey, setSortKey] = useState<SortKey>("score");
  const [sortDesc, setSortDesc] = useState(true);
  const [showUntradeable, setShowUntradeable] = useState(false);
  const [hideThin, setHideThin] = useState(false);

  const q = useQuery<Opportunity[]>(
    () => {
      let query = supabase.from("v_opportunities").select("*").limit(2000);
      if (!showUntradeable) query = query.eq("tradeable", true);
      return query;
    },
    [showUntradeable],
    30000
  );

  const rows = q.data ?? [];
  const cities = useMemo(
    () => Array.from(new Set(rows.map((r) => r.display_name ?? r.city_key))).sort(),
    [rows]
  );

  const filtered = useMemo(() => {
    let out = rows;
    if (cityFilter) out = out.filter((r) => (r.display_name ?? r.city_key) === cityFilter);
    if (sideFilter !== "ALL") out = out.filter((r) => r.side === sideFilter);
    if (hideThin) out = out.filter((r) => !r.thin_market);
    return [...out].sort((a, b) => {
      const av = a[sortKey] ?? -Infinity;
      const bv = b[sortKey] ?? -Infinity;
      if (typeof av === "string" || typeof bv === "string") {
        return sortDesc ? String(bv).localeCompare(String(av)) : String(av).localeCompare(String(bv));
      }
      return sortDesc ? (bv as number) - (av as number) : (av as number) - (bv as number);
    });
  }, [rows, cityFilter, sideFilter, hideThin, sortKey, sortDesc]);

  function headerClick(key: SortKey) {
    if (key === sortKey) setSortDesc((d) => !d);
    else { setSortKey(key); setSortDesc(true); }
  }

  return (
    <div className="space-y-4">
      <h1 className="text-lg font-semibold">Board</h1>
      <p className="max-w-3xl text-xs leading-relaxed text-muted">
        Market price is executable (depth-weighted), never top-of-book. <b>Depth (5c)</b> is what the
        current quote can absorb inside 5c of slippage; <b>Vol 24h</b> is what has actually traded on
        that band. They are different facts and a fat quote nobody hits shows high depth and near-zero
        volume — the rank column already discounts that. Market-state thresholds (§2.4) are provisional
        placeholders and UI-settable.
      </p>

      <div className="flex flex-wrap items-center gap-3 text-sm">
        <select value={cityFilter} onChange={(e) => setCityFilter(e.target.value)} className="rounded border border-border bg-panel2 px-2 py-1">
          <option value="">All cities</option>
          {cities.map((c) => <option key={c} value={c}>{c}</option>)}
        </select>
        <select value={sideFilter} onChange={(e) => setSideFilter(e.target.value as "ALL" | "YES" | "NO")} className="rounded border border-border bg-panel2 px-2 py-1">
          <option value="ALL">Both sides</option>
          <option value="YES">YES only</option>
          <option value="NO">NO only</option>
        </select>
        <label className="flex items-center gap-1 text-muted">
          <input type="checkbox" checked={showUntradeable} onChange={(e) => setShowUntradeable(e.target.checked)} />
          show untradeable
        </label>
        <label className="flex items-center gap-1 text-muted" title="Hide bands whose 24h traded volume is under the thin-market threshold in settings.volume_thresholds.">
          <input type="checkbox" checked={hideThin} onChange={(e) => setHideThin(e.target.checked)} />
          hide thin-volume markets
        </label>
        <span className="ml-auto font-mono text-[11px] text-muted">
          {filtered.length} row{filtered.length === 1 ? "" : "s"} · total 24h volume{" "}
          {fmtCompactUsd(filtered.reduce((s, r) => s + (r.volume_usd ?? 0), 0))}
        </span>
      </div>

      <DataState
        loading={q.loading}
        error={q.error}
        isEmpty={rows.length === 0}
        emptyTitle="The board is empty"
        emptyBody={
          <>
            <code>v_opportunities</code> returned no rows. It is built from <code>edges</code>, which
            the edge engine writes — run GitHub Actions → <b>Probabilities</b> then <b>Signals</b>.
            If you have not run the SQL, start at <code>sql/ad4_00_preflight.sql</code>
            (<code>docs/GO_LIVE.md</code> step 1).
          </>
        }
        onRetry={q.refresh}
      >
        {filtered.length === 0 ? (
          <div className="rounded border border-dashed border-border p-6 text-center text-sm text-muted">
            {rows.length} row{rows.length === 1 ? "" : "s"} loaded, but none match these filters.
          </div>
        ) : (
          <div className="overflow-x-auto rounded border border-border">
            <table className="w-full text-sm">
              <thead className="select-none bg-panel2 text-muted">
                <tr>
                  <Th onClick={() => headerClick("city_key")}>City</Th>
                  <th className="p-2 text-left">Band</th>
                  <th className="p-2 text-left">Side</th>
                  <Th onClick={() => headerClick("market_price")} align="right">Price</Th>
                  <Th onClick={() => headerClick("model_prob")} align="right">Model P</Th>
                  <Th onClick={() => headerClick("edge_net_pp")} align="right">Net edge</Th>
                  <Th onClick={() => headerClick("fillable_usd_5c")} align="right" title="Book depth fillable inside 5c of slippage.">Depth (5c)</Th>
                  <Th onClick={() => headerClick("volume_usd")} align="right" title="24h traded volume on this band, from trades_observed.">Vol 24h</Th>
                  <th className="p-2 text-right" title="Number of prints in the last 24h.">Trades</th>
                  <th className="p-2 text-left">State</th>
                  <th className="p-2 text-left">Regime</th>
                  <Th onClick={() => headerClick("score")} align="right" title="edge x confidence x ln(1+depth) x volume/(volume+k). The volume factor only ever discounts.">Rank</Th>
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
                    <td
                      className={`p-2 text-right font-mono ${o.thin_market ? "text-warn" : o.volume_usd ? "" : "text-muted"}`}
                      title={o.last_trade_at ? `last trade ${fmtAge(o.last_trade_at)}` : "no trades in the window"}
                    >
                      {fmtCompactUsd(o.volume_usd)}{o.thin_market ? " ⚠" : ""}
                    </td>
                    <td className="p-2 text-right font-mono text-muted">{fmtInt(o.n_trades)}</td>
                    <td className="p-2 text-xs">{o.market_state}</td>
                    <td className={`p-2 ${regimeColor(o.regime_label)}`}>{o.regime_label}</td>
                    <td className="p-2 text-right font-mono text-[11px]">
                      {o.score != null ? o.score.toFixed(4) : "—"}
                      {o.score_depth_only != null && o.score != null && o.score_depth_only > 0 && (
                        <span className="ml-1 text-[10px] text-muted" title={`Depth-only rank ${o.score_depth_only.toFixed(4)}; volume factor ${(o.liquidity_factor ?? 0).toFixed(3)}`}>
                          ×{(o.liquidity_factor ?? 0).toFixed(2)}
                        </span>
                      )}
                    </td>
                    <td className="p-2 text-xs text-muted">{o.block_reason ?? ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </DataState>
    </div>
  );
}

function Th({
  children, onClick, align = "left", title,
}: {
  children: React.ReactNode;
  onClick: () => void;
  align?: "left" | "right";
  title?: string;
}) {
  return (
    <th
      onClick={onClick}
      title={title}
      className={`cursor-pointer p-2 hover:text-text ${align === "right" ? "text-right" : "text-left"}`}
    >
      {children}
    </th>
  );
}
