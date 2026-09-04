"use client";

import type { StatsMode } from "@/lib/useCityStats";

/**
 * One line saying which numbers are missing and exactly what supplies them.
 * Shown only in fallback mode - when the view is there, it says nothing.
 */
export default function StatsNotice({ mode, viewError }: { mode: StatsMode; viewError: string | null }) {
  if (mode !== "fallback") return null;
  return (
    <div className="rounded border border-warn/40 bg-warn/10 px-3 py-2 text-xs leading-relaxed text-warn">
      <b>Running without <code>v_city_stats</code>.</b> Temperatures, forecasts and volume below are
      live and correct. What is missing is anything needing each city&apos;s climate history —
      hotness against normal, volatility, forecast MAE, book depth — which is why those columns read
      &ldquo;—&rdquo;. Run <code>sql/ad4_17_city_stats.sql</code> in the Supabase SQL editor and they
      fill in.
      {viewError && (
        <span className="mt-1 block font-mono text-[10px] opacity-80">Postgres said: {viewError}</span>
      )}
    </div>
  );
}
