"use client";

import { useMemo } from "react";
import { supabase } from "@/lib/supabase";
import { useQuery } from "@/lib/useQuery";
import { DataState, InlineError } from "@/components/DataState";
import { fmtCompactUsd, fmtInt, fmtPct, fmtUsd, pnlColor } from "@/lib/format";

const MIN_TRADES_FOR_RATE = 20; // matches scripts/backtest/metrics.py's own threshold

interface SkillRow { city_key: string; mae_c: number; bias_c: number; mae_bands: number; n_days: number; lead_days: number; }
interface StrategyAgg { strategy_id: string; n: number; netPnl: number; winRate: number | null; }

export default function AnalyticsPage() {
  const skillQ = useQuery<any[]>(
    () =>
      supabase
        .from("derived_forecast_skill")
        .select("city_key,mae_c,bias_c,mae_bands,n_days,lead_days,computed_at")
        .eq("lead_days", 1)
        .order("computed_at", { ascending: false })
        .limit(500),
    []
  );

  const tradesQ = useQuery<any[]>(
    () => supabase.from("paper_trades").select("strategy_id,net_pnl,gross_pnl").not("closed_at", "is", null),
    []
  );

  const signalsQ = useQuery<any[]>(
    () =>
      supabase
        .from("signals")
        .select("strategy_id")
        .gte("fired_at", new Date(Date.now() - 7 * 24 * 3600 * 1000).toISOString()),
    []
  );

  const capacityQ = useQuery<any[]>(
    () => supabase.from("derived_capacity").select("city_key,usd_at_5c,computed_at").order("computed_at", { ascending: false }).limit(200),
    []
  );

  const volumeQ = useQuery<Array<{ city_key: string; volume_usd: number; n_trades: number }>>(
    () => supabase.from("v_city_volume").select("city_key,volume_usd,n_trades"),
    [],
    60000
  );

  const skill: SkillRow[] = useMemo(() => {
    const latestByCity = new Map<string, SkillRow>();
    for (const r of skillQ.data ?? []) if (!latestByCity.has(r.city_key)) latestByCity.set(r.city_key, r);
    return Array.from(latestByCity.values()).sort((a, b) => a.mae_c - b.mae_c);
  }, [skillQ.data]);

  const byStrategy: StrategyAgg[] = useMemo(() => {
    const grouped = new Map<string, { n: number; netPnl: number; wins: number }>();
    for (const t of tradesQ.data ?? []) {
      const g = grouped.get(t.strategy_id) ?? { n: 0, netPnl: 0, wins: 0 };
      g.n += 1;
      g.netPnl += t.net_pnl ?? 0;
      if ((t.net_pnl ?? 0) > 0) g.wins += 1;
      grouped.set(t.strategy_id, g);
    }
    return Array.from(grouped.entries()).map(([strategy_id, g]) => ({
      strategy_id, n: g.n, netPnl: g.netPnl, winRate: g.n > 0 ? g.wins / g.n : null,
    }));
  }, [tradesQ.data]);

  const signalFreq: Record<string, number> = useMemo(() => {
    const freq: Record<string, number> = {};
    for (const s of signalsQ.data ?? []) freq[s.strategy_id] = (freq[s.strategy_id] ?? 0) + 1;
    return freq;
  }, [signalsQ.data]);

  // Latest capacity row per city (the query is ordered newest-first).
  const capacity = useMemo(() => {
    const seen = new Map<string, { city_key: string; usd_at_5c: number }>();
    for (const c of capacityQ.data ?? []) if (!seen.has(c.city_key)) seen.set(c.city_key, c);
    return Array.from(seen.values());
  }, [capacityQ.data]);

  const volume = volumeQ.data ?? [];
  const totalVolume = volume.reduce((s, v) => s + (v.volume_usd ?? 0), 0);
  const capacityByCity = new Map(capacity.map((c) => [c.city_key, c.usd_at_5c]));

  return (
    <div className="space-y-8">
      <h1 className="text-lg font-semibold">Analytics</h1>

      <section>
        <h2 className="mb-2 text-sm font-semibold text-muted">Forecast skill by city (lead 1)</h2>
        <DataState
          loading={skillQ.loading}
          error={skillQ.error}
          isEmpty={skill.length === 0}
          emptyTitle="No forecast skill measured yet"
          emptyBody={<>Run GitHub Actions → <b>Skill</b> (<code>scripts/measure_skill.py</code>). It needs both forecast and observation history for a city before it can measure bias. See <code>docs/skill_baseline.md</code>.</>}
          onRetry={skillQ.refresh}
        >
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
            </tbody>
          </table>
        </div>
        </DataState>
      </section>

      <section>
        <h2 className="mb-2 text-sm font-semibold text-muted">Strategy attribution (net P&amp;L, never merged with microstructure edge)</h2>
        <DataState
          loading={tradesQ.loading}
          error={tradesQ.error}
          isEmpty={byStrategy.length === 0}
          emptyTitle="No settled trades yet"
          emptyBody={<>Attribution needs closed paper trades. Nothing settles until a strategy is enabled, fires a signal, and its market resolves — GitHub Actions → <b>Settlement</b> writes the close.</>}
          onRetry={tradesQ.refresh}
        >
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
        </div>
        </DataState>
      </section>

      <section>
        <h2 className="mb-2 text-sm font-semibold text-muted">Signal frequency per strategy (7d)</h2>
        <div className="flex flex-wrap gap-3">
          {Object.entries(signalFreq).map(([id, n]) => (
            <div key={id} className="rounded border border-border bg-panel px-3 py-2 text-sm">
              <span className="text-muted">{id}</span> <span className="font-mono">{n}</span>
            </div>
          ))}
          {Object.keys(signalFreq).length === 0 && !signalsQ.loading && (
            <div className="text-sm text-muted">
              No signals fired in the last 7 days. All six strategies ship disabled — nothing fires
              until you enable one.
            </div>
          )}
          <InlineError message={signalsQ.error} />
        </div>
      </section>

      <section>
        <h2 className="mb-2 text-sm font-semibold text-muted">Liquidity: book depth vs traded volume</h2>
        <p className="mb-2 max-w-3xl text-xs leading-relaxed text-muted">
          Two different facts, shown side by side rather than merged. <b>Depth (5c)</b> is what the
          current quotes can absorb inside 5c of slippage — capacity is a curve, this is one slice of
          it. <b>Volume (24h)</b> is what actually traded. A city with depth and no volume is quoted
          but not traded; a city with volume and no depth trades in bursts against a thin book. Both
          shapes cost money in different ways, and the opportunity ranking discounts the first.
        </p>
        <DataState
          loading={capacityQ.loading || volumeQ.loading}
          error={capacityQ.error ?? volumeQ.error}
          isEmpty={capacity.length === 0 && volume.length === 0}
          emptyTitle="No liquidity data yet"
          emptyBody={
            <>
              <code>derived_capacity</code> is filled by <code>recompute_capacity()</code> (GitHub
              Actions → <b>Derived Recompute</b>) and <code>v_city_volume</code> reads{" "}
              <code>trades_observed</code>, which the trade ingest fills. Neither has run yet.
            </>
          }
          onRetry={() => { capacityQ.refresh(); volumeQ.refresh(); }}
        >
          <div className="mb-2 text-xs text-muted">
            Total traded volume across all cities in the last 24h:{" "}
            <span className="font-mono text-text">{fmtCompactUsd(totalVolume)}</span>
          </div>
          <div className="overflow-x-auto rounded border border-border">
            <table className="w-full text-sm">
              <thead className="bg-panel2 text-muted">
                <tr>
                  <th className="p-2 text-left">City</th>
                  <th className="p-2 text-right">Depth 5c (latest)</th>
                  <th className="p-2 text-right">Volume 24h</th>
                  <th className="p-2 text-right">Trades 24h</th>
                  <th className="p-2 text-left">Shape</th>
                </tr>
              </thead>
              <tbody>
                {Array.from(new Set([...capacity.map((c) => c.city_key), ...volume.map((v) => v.city_key)]))
                  .sort((a, b) => (volumeByKey(volume, b) ?? 0) - (volumeByKey(volume, a) ?? 0))
                  .map((city) => {
                    const d = capacityByCity.get(city) ?? null;
                    const v = volume.find((x) => x.city_key === city);
                    const vol = v?.volume_usd ?? 0;
                    const shape =
                      d && d > 0 && vol === 0 ? "quoted, not traded"
                      : vol > 0 && (!d || d === 0) ? "traded, thin book"
                      : d && vol ? "both" : "neither";
                    return (
                      <tr key={city} className="border-t border-border">
                        <td className="p-2">{city}</td>
                        <td className="p-2 text-right font-mono">{d !== null ? fmtUsd(d) : "—"}</td>
                        <td className="p-2 text-right font-mono">{fmtCompactUsd(vol)}</td>
                        <td className="p-2 text-right font-mono text-muted">{fmtInt(v?.n_trades ?? 0)}</td>
                        <td className={`p-2 text-xs ${shape === "both" ? "text-good" : shape === "neither" ? "text-muted" : "text-warn"}`}>{shape}</td>
                      </tr>
                    );
                  })}
              </tbody>
            </table>
          </div>
        </DataState>
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

function volumeByKey(rows: Array<{ city_key: string; volume_usd: number }>, key: string): number | null {
  return rows.find((r) => r.city_key === key)?.volume_usd ?? null;
}
