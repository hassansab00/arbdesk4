"use client";

import { useEffect, useState } from "react";
import { supabase } from "@/lib/supabase";
import { fmtPct, fmtUsd, pnlColor } from "@/lib/format";
import type { BacktestRun } from "@/lib/types";

interface ResultRow { run_id: string; scope: string; data: any; }

export default function BacktestPage() {
  const [runs, setRuns] = useState<BacktestRun[]>([]);
  const [selectedRun, setSelectedRun] = useState<string | null>(null);
  const [compareRun, setCompareRun] = useState<string | null>(null);
  const [results, setResults] = useState<Record<string, any>>({});
  const [compareResults, setCompareResults] = useState<Record<string, any>>({});

  const [startDate, setStartDate] = useState("2026-06-01");
  const [endDate, setEndDate] = useState("2026-08-29");
  const [cities, setCities] = useState("");
  const [strategyIds, setStrategyIds] = useState("s2_combination_arb");
  const [startingBudget, setStartingBudget] = useState("10000");
  const [label, setLabel] = useState("");

  async function loadRuns() {
    const { data } = await supabase.from("backtest_runs").select("*").order("created_at", { ascending: false }).limit(30);
    setRuns((data as BacktestRun[]) ?? []);
  }

  useEffect(() => {
    loadRuns();
    const t = setInterval(loadRuns, 15000);
    return () => clearInterval(t);
  }, []);

  useEffect(() => {
    if (!selectedRun) return setResults({});
    supabase.from("backtest_results").select("*").eq("run_id", selectedRun).then(({ data }) => {
      const byScope: Record<string, any> = {};
      for (const r of (data as ResultRow[]) ?? []) byScope[r.scope] = r.data;
      setResults(byScope);
    });
  }, [selectedRun]);

  useEffect(() => {
    if (!compareRun) return setCompareResults({});
    supabase.from("backtest_results").select("*").eq("run_id", compareRun).then(({ data }) => {
      const byScope: Record<string, any> = {};
      for (const r of (data as ResultRow[]) ?? []) byScope[r.scope] = r.data;
      setCompareResults(byScope);
    });
  }, [compareRun]);

  async function queueRun() {
    const params = {
      start_date: startDate, end_date: endDate,
      cities: cities.trim() ? cities.split(",").map((s) => s.trim()) : [],
      strategies: strategyIds.split(",").map((s) => ({ strategy_id: s.trim(), enabled: true })),
      starting_budget: parseFloat(startingBudget) || 10000,
      evaluation_lead_days: 1,
    };
    const { data } = await supabase.rpc("queue_backtest", { p_params: params });
    if (label && data) await supabase.from("backtest_runs").update({ label }).eq("run_id", data);
    setLabel("");
    loadRuns();
  }

  return (
    <div className="space-y-6">
      <h1 className="text-lg font-semibold">Backtest</h1>
      <div className="rounded border border-warn/50 bg-warn/10 p-3 text-xs text-warn">
        Book depth history begins 22 Aug 2026 - Polymarket publishes no depth history, so there is
        no way to obtain more. Forecast accuracy can be backtested against ~2.5 years; strategy
        profitability cannot, until AD4&apos;s own market data accumulates. This is a property of the
        data, not the harness.
      </div>

      <div className="grid gap-6 lg:grid-cols-[1fr_2fr]">
        <div className="space-y-2 rounded border border-border bg-panel p-4 text-sm">
          <h2 className="font-semibold text-muted">Queue a run</h2>
          <Field label="Start date"><input value={startDate} onChange={(e) => setStartDate(e.target.value)} className="input" /></Field>
          <Field label="End date"><input value={endDate} onChange={(e) => setEndDate(e.target.value)} className="input" /></Field>
          <Field label="Cities (comma-separated, blank = all)"><input value={cities} onChange={(e) => setCities(e.target.value)} className="input" /></Field>
          <Field label="Strategies (comma-separated ids)"><input value={strategyIds} onChange={(e) => setStrategyIds(e.target.value)} className="input" /></Field>
          <Field label="Starting budget"><input value={startingBudget} onChange={(e) => setStartingBudget(e.target.value)} className="input" /></Field>
          <Field label="Label (optional)"><input value={label} onChange={(e) => setLabel(e.target.value)} className="input" /></Field>
          <button onClick={queueRun} className="mt-2 w-full rounded bg-accent py-1.5 text-white hover:opacity-90">Queue backtest</button>
          <p className="text-[10px] text-muted">Polled every 10 minutes by .github/workflows/backtest.yml - no Vercel function.</p>
        </div>

        <div>
          <h2 className="mb-2 text-sm font-semibold text-muted">Saved runs</h2>
          <div className="max-h-56 overflow-y-auto rounded border border-border">
            <table className="w-full text-sm">
              <thead className="bg-panel2 text-muted"><tr><th className="p-2 text-left">Run</th><th className="p-2 text-left">Status</th><th className="p-2 text-left">Created</th><th className="p-2" /></tr></thead>
              <tbody>
                {runs.map((r) => (
                  <tr key={r.run_id} className="border-t border-border">
                    <td className="p-2">{r.label || r.run_id.slice(0, 8)}</td>
                    <td className="p-2"><StatusBadge status={r.status} /></td>
                    <td className="p-2 text-xs text-muted">{new Date(r.created_at).toLocaleString()}</td>
                    <td className="p-2 space-x-2 text-xs">
                      <button className="text-accent hover:underline" onClick={() => setSelectedRun(r.run_id)}>view</button>
                      <button className="text-muted hover:underline" onClick={() => setCompareRun(r.run_id)}>compare</button>
                    </td>
                  </tr>
                ))}
                {runs.length === 0 && <tr><td colSpan={4} className="p-4 text-center text-muted">No runs queued yet.</td></tr>}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      {selectedRun && <RunDashboard results={results} compareResults={compareRun ? compareResults : undefined} />}
    </div>
  );
}

function RunDashboard({ results, compareResults }: { results: Record<string, any>; compareResults?: Record<string, any> }) {
  const headline = results.headline;
  const compareHeadline = compareResults?.headline;
  if (!headline) return <div className="text-muted text-sm">No results yet for this run - it may still be queued/running.</div>;

  return (
    <div className="space-y-6">
      {headline.data_limitation && (
        <div className="rounded border border-warn/50 bg-warn/10 p-2 text-xs text-warn">{headline.data_limitation}</div>
      )}

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <HeadlineStat label="Trades" value={String(headline.n_trades)} />
        <HeadlineStat label="Win rate" value={fmtPct(headline.win_rate)} />
        <HeadlineStat label="Net P&L" value={fmtUsd(headline.total_net_pnl, { signed: true })} color={pnlColor(headline.total_net_pnl)} />
        <HeadlineStat label="Gross P&L" value={fmtUsd(headline.total_gross_pnl, { signed: true })} />
      </div>
      {headline.note && <div className="text-xs text-muted">{headline.note}</div>}

      {compareHeadline && (
        <div className="rounded border border-border bg-panel p-3 text-sm">
          <h3 className="mb-2 text-muted">Comparison</h3>
          <table className="w-full text-xs">
            <thead><tr><th /><th className="text-right">Run A</th><th className="text-right">Run B</th></tr></thead>
            <tbody>
              <tr><td>Trades</td><td className="text-right font-mono">{headline.n_trades}</td><td className="text-right font-mono">{compareHeadline.n_trades}</td></tr>
              <tr><td>Net P&L</td><td className={`text-right font-mono ${pnlColor(headline.total_net_pnl)}`}>{fmtUsd(headline.total_net_pnl)}</td><td className={`text-right font-mono ${pnlColor(compareHeadline.total_net_pnl)}`}>{fmtUsd(compareHeadline.total_net_pnl)}</td></tr>
              <tr><td>Win rate</td><td className="text-right font-mono">{fmtPct(headline.win_rate)}</td><td className="text-right font-mono">{fmtPct(compareHeadline.win_rate)}</td></tr>
            </tbody>
          </table>
        </div>
      )}

      {results.equity_curve && <EquityCurve curve={results.equity_curve} />}

      {results.strategy && (
        <Section title="Strategy attribution (never merged)">
          {Object.entries(results.strategy).map(([id, s]: [string, any]) => (
            <div key={id} className="flex justify-between border-b border-border py-1 text-sm">
              <span>{id} {s.insufficient_sample && <span className="text-warn text-xs">(too few trades)</span>}</span>
              <span className={pnlColor(s.total_net_pnl)}>{fmtUsd(s.total_net_pnl, { signed: true })}</span>
            </div>
          ))}
        </Section>
      )}

      {results.costs && (
        <Section title="Cost analysis (gross vs net)">
          <div className="grid grid-cols-2 gap-2 text-sm sm:grid-cols-4">
            <HeadlineStat label="Gross" value={fmtUsd(results.costs.total_gross_pnl)} />
            <HeadlineStat label="Net" value={fmtUsd(results.costs.total_net_pnl)} color={pnlColor(results.costs.total_net_pnl)} />
            <HeadlineStat label="Fees" value={fmtUsd(results.costs.total_fees)} />
            <HeadlineStat label="Drag %" value={results.costs.cost_drag_pct !== null ? `${results.costs.cost_drag_pct?.toFixed(1)}%` : "—"} />
          </div>
        </Section>
      )}

      {results.calibration && results.calibration.length > 0 && (
        <Section title="Calibration">
          <table className="w-full text-xs">
            <thead className="text-muted"><tr><th className="text-left">Bin</th><th className="text-right">n</th><th className="text-right">Predicted</th><th className="text-right">Actual</th></tr></thead>
            <tbody>
              {results.calibration.map((r: any) => (
                <tr key={r.bin} className="border-t border-border">
                  <td>{r.bin}</td><td className="text-right font-mono">{r.n}{r.insufficient_sample && " ⚠"}</td>
                  <td className="text-right font-mono">{fmtPct(r.avg_predicted_prob)}</td>
                  <td className="text-right font-mono">{fmtPct(r.actual_win_rate)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Section>
      )}
    </div>
  );
}

function EquityCurve({ curve }: { curve: Array<{ equity: number; drawdown: number }> }) {
  if (!curve.length) return null;
  const w = 600, h = 160;
  const values = curve.map((c) => c.equity);
  const min = Math.min(...values), max = Math.max(...values);
  const range = max - min || 1;
  const points = curve.map((c, i) => `${(i / (curve.length - 1 || 1)) * w},${h - ((c.equity - min) / range) * h}`).join(" ");
  const ddPoints = curve.map((c, i) => `${(i / (curve.length - 1 || 1)) * w},${h - ((c.equity + c.drawdown - min) / range) * h}`);
  return (
    <Section title="Equity curve (with drawdown shading)">
      <svg viewBox={`0 0 ${w} ${h}`} className="w-full">
        <polyline points={points} fill="none" stroke="#4f8cff" strokeWidth={1.5} />
        <polygon points={`0,${h} ${points} ${w},${h}`} fill="#4f8cff" opacity={0.08} />
      </svg>
    </Section>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="rounded border border-border bg-panel p-4">
      <h3 className="mb-2 text-sm font-semibold text-muted">{title}</h3>
      {children}
    </div>
  );
}

function HeadlineStat({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div className="rounded border border-border bg-panel2 p-2">
      <div className="text-[10px] text-muted">{label}</div>
      <div className={`font-mono ${color ?? ""}`}>{value}</div>
    </div>
  );
}

function StatusBadge({ status }: { status: string }) {
  const color = status === "complete" ? "text-good" : status === "failed" ? "text-bad" : "text-warn";
  return <span className={color}>{status}</span>;
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <div className="text-[10px] text-muted">{label}</div>
      {children}
    </label>
  );
}
