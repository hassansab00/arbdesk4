"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { supabase } from "@/lib/supabase";
import { fmtPct, fmtPp, fmtPrice, fmtUsd, regimeColor } from "@/lib/format";
import type { Opportunity } from "@/lib/types";

export default function OpportunitiesPage() {
  const router = useRouter();
  const [rows, setRows] = useState<Opportunity[]>([]);
  const [minConf, setMinConf] = useState(0);

  useEffect(() => {
    async function load() {
      const { data } = await supabase
        .from("v_opportunities")
        .select("*")
        .eq("tradeable", true)
        .order("score", { ascending: false, nullsFirst: false })
        .limit(100);
      setRows((data as Opportunity[]) ?? []);
    }
    load();
    const t = setInterval(load, 30000);
    return () => clearInterval(t);
  }, []);

  const filtered = rows.filter((r) => (r.confidence ?? 0) >= minConf);

  return (
    <div className="space-y-4">
      <h1 className="text-lg font-semibold">Opportunities</h1>
      <p className="text-xs text-muted">
        Ranked by edge_net_pp x confidence x log(1 + fillable_usd_5c) - a large edge on a dead
        book ranks below a modest edge with real depth. Click a row to load it into the calculator.
      </p>

      <div className="flex items-center gap-2 text-sm">
        <span className="text-muted">Min confidence</span>
        <input type="range" min={0} max={1} step={0.05} value={minConf} onChange={(e) => setMinConf(parseFloat(e.target.value))} />
        <span className="font-mono">{fmtPct(minConf)}</span>
      </div>

      <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
        {filtered.map((o, i) => (
          <button
            key={`${o.band_id}-${o.side}`}
            onClick={() => router.push(`/calculator?band_id=${o.band_id}`)}
            className="rounded border border-border bg-panel p-3 text-left text-sm hover:border-accent"
          >
            <div className="flex justify-between">
              <span className="font-semibold">#{i + 1} {o.display_name ?? o.city_key}</span>
              <span className={regimeColor(o.regime_label)}>{o.regime_label}</span>
            </div>
            <div className="text-muted">{o.band_label ?? `${o.band_lo}-${o.band_hi}`} · {o.side}</div>
            <div className="mt-2 grid grid-cols-2 gap-1 font-mono text-xs">
              <div>Price <span className="text-text">{fmtPrice(o.market_price)}</span></div>
              <div>Model P <span className="text-text">{fmtPct(o.model_prob)}</span></div>
              <div>Net edge <span className="text-good">{fmtPp(o.edge_net_pp)}</span></div>
              <div>Depth (5c) <span className="text-text">{fmtUsd(o.fillable_usd_5c)}</span></div>
            </div>
          </button>
        ))}
        {filtered.length === 0 && (
          <div className="col-span-full p-8 text-center text-muted">No opportunities meet this confidence threshold.</div>
        )}
      </div>
    </div>
  );
}
