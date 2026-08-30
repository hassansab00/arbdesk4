"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { supabase } from "@/lib/supabase";
import { useQuery } from "@/lib/useQuery";
import { DataState } from "@/components/DataState";
import { fmtAge, fmtCompactUsd, fmtPct, fmtPp, fmtPrice, fmtUsd, regimeColor } from "@/lib/format";
import type { Opportunity } from "@/lib/types";

export default function OpportunitiesPage() {
  const router = useRouter();
  const [minConf, setMinConf] = useState(0);
  const [minVolume, setMinVolume] = useState(0);

  const q = useQuery<Opportunity[]>(
    () =>
      supabase
        .from("v_opportunities")
        .select("*")
        .eq("tradeable", true)
        .order("score", { ascending: false, nullsFirst: false })
        .limit(200),
    [],
    30000
  );

  const rows = q.data ?? [];
  const filtered = rows.filter(
    (r) => (r.confidence ?? 0) >= minConf && (r.volume_usd ?? 0) >= minVolume
  );

  return (
    <div className="space-y-4">
      <h1 className="text-lg font-semibold">Opportunities</h1>
      <p className="max-w-3xl text-xs leading-relaxed text-muted">
        Ranked by <code>edge_net_pp × confidence × ln(1 + depth) × volume/(volume + k)</code>. The
        first three terms say a large edge on an unfillable book must rank below a modest edge with
        real depth. The last one adds the other half of liquidity: a band nobody actually trades gets
        discounted even when its quote looks deep. It can only pull a rank down, never inflate one.
        Click a card to load it into the calculator.
      </p>

      <div className="flex flex-wrap items-center gap-6 text-sm">
        <label className="flex items-center gap-2">
          <span className="text-muted">Min confidence</span>
          <input type="range" min={0} max={1} step={0.05} value={minConf} onChange={(e) => setMinConf(parseFloat(e.target.value))} />
          <span className="w-12 font-mono">{fmtPct(minConf, 0)}</span>
        </label>
        <label className="flex items-center gap-2" title="Filter out bands that have barely traded in the last 24h.">
          <span className="text-muted">Min 24h volume</span>
          <input type="range" min={0} max={5000} step={100} value={minVolume} onChange={(e) => setMinVolume(parseFloat(e.target.value))} />
          <span className="w-14 font-mono">{fmtCompactUsd(minVolume)}</span>
        </label>
      </div>

      <DataState
        loading={q.loading}
        error={q.error}
        isEmpty={rows.length === 0}
        emptyTitle="No opportunities yet"
        emptyBody={
          <>
            Run the probability and edge engines (GitHub Actions → <b>Probabilities</b>) to populate
            this. Until they have run at least once, <code>edges</code> is empty and so is{" "}
            <code>v_opportunities</code>. If SQL has never been run against this database, start with{" "}
            <code>sql/ad4_00_preflight.sql</code> — <code>docs/GO_LIVE.md</code> walks the whole
            sequence.
          </>
        }
        onRetry={q.refresh}
      >
        {filtered.length === 0 ? (
          <div className="rounded border border-dashed border-border p-6 text-center text-sm text-muted">
            {rows.length} tradeable opportunit{rows.length === 1 ? "y" : "ies"} loaded, but none meet
            these confidence and volume thresholds.
          </div>
        ) : (
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
                  <div>Depth 5c <span className="text-text">{fmtUsd(o.fillable_usd_5c)}</span></div>
                  <div className="col-span-2">
                    Vol 24h{" "}
                    <span className={o.thin_market ? "text-warn" : "text-text"}>
                      {fmtCompactUsd(o.volume_usd)}
                    </span>{" "}
                    <span className="text-[10px] text-muted">
                      ({o.n_trades ?? 0} trades{o.last_trade_at ? `, last ${fmtAge(o.last_trade_at)}` : ""})
                    </span>
                  </div>
                </div>
                {o.thin_market && (
                  <div className="mt-2 rounded bg-warn/10 px-2 py-1 text-[10px] text-warn">
                    Thin market — quoted, but barely traded in the last 24h. Expect the fill to move
                    the price further than the ladder implies.
                  </div>
                )}
              </button>
            ))}
          </div>
        )}
      </DataState>
    </div>
  );
}
