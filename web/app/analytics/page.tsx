"use client";

import { useEffect, useState } from "react";
import { supabase } from "@/lib/supabase";
import { fmtPct, fmtUsd, pnlColor } from "@/lib/format";

const MIN_TRADES_FOR_RATE = 20; // matches scripts/backtest/metrics.py's own threshold

interface SkillRow { city_key: string; mae_c: number; bias_c: number; mae_bands: number; n_days: number; lead_days: number; }
interface StrategyAgg { strategy_id: string; n: number; netPnl: number; winRate: number | null; }

export default function AnalyticsPage() {
  const [skill, setSkill] = useState<SkillRow[]>([]);
  const [byStrategy, setByStrategy] = useState<StrategyAgg[]>([]);
  const [signalFreq, setSignalFreq] = useState<Record<string, number>>({});
  const [capacity, setCapacity] = useState<Array<{ city_key: string; usd_at_5c: number }>>([]);

  useEffect(() => {
    async function load() {
      const { data: skillRows } = await supabase
        .from("derived_forecast_skill")
        .select("city_key,mae_c,bias_c,mae_bands,n_days,lead_days,computed_at")
        .eq("lead_days", 1)
        .order("computed_at", { ascending: false })
        .limit(60);
      const latestByCity = new Map<string, SkillRow>();
      for (const r of (skillRows as any[]) ?? []) if (!latestByCity.has(r.city_key)) latestByCity.set(r.city_key, r);
      setSkill(Array.from(latestByCity.values()).sort((a, b) => a.mae_c - b.mae_c));

      const { data: trades } = await supabase.from("paper_trades").select("strategy_id,net_pnl,gross_pnl").not("closed_at", "is", null);
      const grouped = new Map<string, { n: number; netPnl: number; wins: number }>();
      for (const t of (trades as any[]) ?? []) {
        const g = grouped.get(t.strategy_id) ?? { n: 0, netPnl: 0, wins: 0 };
        g.n += 1;
        g.netPnl += t.net_pnl ?? 0;
        if ((t.net_pnl ?? 0) > 0) g.wins += 1;
        grouped.set(t.strategy_id, g);
      }
      setByStrategy(Array.from(grouped.entries()).map(([strategy_id, g]) => ({
        strategy_id, n: g.n, netPnl: g.netPnl, winRate: g.n > 0 ? g.wins / g.n : null,
      })));

      const since = new Date(Date.now() - 7 * 24 * 3600 * 1000).toISOString();
      const { data: sig } = await supabase.from("signals").select("strategy_id").gte("fired_at", since);
      const freq: Record<string, number> = {};
      for (const s of (sig as any[]) ?? []) freq[s.strategy_id] = (freq[s.strategy_id] ?? 0) + 1;
      setSignalFreq(freq);

      const { data: cap } = await supabase.from("derived_capacity").select("city_key,usd_at_5c").order("computed_at", { ascending: false }).limit(54);
      setCapacity((cap as any[]) ?? []);
    }
    load();
  }, []);

  return (
    <div className="space-y-8">
      <h1 className="text-lg font-semibold">Analytics</h1>

      <section>
        <h2 className="mb-2 text-sm font-semibold text-muted">Forecast skill by city (lead 1)</h2>
        <div className="overflow-x-auto rounded border border-border">
          <table className="w-full text-sm">
            <thead className="bg-panel2 text-muted"><tr>
              <th className="p-2 text-left">City</th><th className="p-2 text-right">MAE (C)</th>
              <th className="p-2 text-right">Bias (C)</th><th className="p-2 text-right">MAE (bands)</th>
              <th className="p-2 text-right">n days</th>
            </tr></thead>
            <tbody>
              {skill.map((s) => (
                <tr key={s.city_key} className="border-t border-border">
                  <td className="p-2">{s.city_key}</td>
                  <td className="p-2 text-right font-mono">{s.mae_c?.toFixed(2)}</td>
                  <td className="p-2 text-right font-mono">{s.bias_c?.toFixed(2)}</td>
                  <td className="p-2 text-right font-mono">{s.mae_bands?.toFixed(2)}</td>
                  <td className={`p-2 text-right font-mono ${s.n_days < 200 ? "text-warn" : ""}`}>{s.n_days}{s.n_days < 200 ? " ⚠" : ""}</td>
                </tr>
              ))}
              {skill.length === 0 && <tr><td colSpan={5} className="p-4 text-center text-muted">No derived_forecast_skill rows yet - see docs/skill_baseline.md.</td></tr>}
            </tbody>
          </table>
        </div>
      </section>

      <section>
        <h2 className="mb-2 text-sm font-semibold text-muted">Strategy attribution (net P&amp;L, never merged with microstructure edge)</h2>
        <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
          {byStrategy.map((s) => (
            <div key={s.strategy_id} className="rounded border border-border bg-panel p-3 text-sm">
              <div className="font-semibold">{s.strategy_id}</div>
              <div className={`font-mono text-lg ${pnlColor(s.netPnl)}`}>{fmtUsd(s.netPnl, { signed: true })}</div>
              <div className="text-xs text-muted">{s.n} trades · win rate {fmtPct(s.winRate)}</div>
              {s.n < MIN_TRADES_FOR_RATE && (
                <div className="mt-1 text-[10px] text-warn">Too few trades for this to mean anything yet.</div>
              )}
            </div>
          ))}
          {byStrategy.length === 0 && <div className="col-span-full p-4 text-center text-muted">No settled trades yet.</div>}
        </div>
      </section>

      <section>
        <h2 className="mb-2 text-sm font-semibold text-muted">Signal frequency per strategy (7d)</h2>
        <div className="flex flex-wrap gap-3">
          {Object.entries(signalFreq).map(([id, n]) => (
            <div key={id} className="rounded border border-border bg-panel px-3 py-2 text-sm">
              <span className="text-muted">{id}</span> <span className="font-mono">{n}</span>
            </div>
          ))}
          {Object.keys(signalFreq).length === 0 && <div className="text-muted text-sm">No signals fired in the last 7 days.</div>}
        </div>
      </section>

      <section>
        <h2 className="mb-2 text-sm font-semibold text-muted">Capacity utilisation (usd_at_5c, latest)</h2>
        <p className="mb-2 text-xs text-muted">Capacity is a curve, not a number - this shows the 5c-slippage slice only; see Board for full depth.</p>
        <div className="flex flex-wrap gap-2 text-xs">
          {capacity.map((c) => (
            <div key={c.city_key} className="rounded bg-panel2 px-2 py-1">{c.city_key}: {fmtUsd(c.usd_at_5c)}</div>
          ))}
          {capacity.length === 0 && <div className="text-muted">No derived_capacity rows yet.</div>}
        </div>
      </section>

      <section>
        <h2 className="mb-2 text-sm font-semibold text-muted">Calibration reliability diagram</h2>
        <p className="text-xs text-muted">
          Requires realised settlement outcomes (Task 11). Populated once enough paper trades
          have settled - see scripts/backtest/metrics.py:calibration() for the same computation
          run against backtest results in the meantime.
        </p>
      </section>
    </div>
  );
}
