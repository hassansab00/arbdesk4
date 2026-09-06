"use client";

import { useEffect, useState } from "react";
import { supabase } from "@/lib/supabase";
import { useQuery } from "@/lib/useQuery";
import { DataState, ErrorBox, Loading } from "@/components/DataState";
import { Freshness } from "@/components/Provenance";
import { fmtInt, fmtPct, fmtUsd, pnlColor } from "@/lib/format";
import type { BacktestRun } from "@/lib/types";

interface ResultRow { run_id: string; scope: string; data: any; }

/** sql/ad4_42_backtest.sql - the widest window that could produce trades. */
interface BacktestWindow {
  book_from: string | null; book_to: string | null; n_books: number;
  obs_from: string | null; obs_to: string | null; n_obs: number;
  mkt_from: string | null; mkt_to: string | null; n_markets: number;
  usable_from: string | null; usable_to: string | null;
  blocked_because: string | null;
}

/** What backtest_readiness() answers for the exact window in the form. */
interface Readiness {
  ok: boolean;
  days: number;
  markets: number;
  with_bands: number;
  with_observation: number;
  with_forecast: number;
  simulatable: number;
  blocked_because: string | null;
  summary: string;
}

export default function BacktestPage() {
  const [selectedRun, setSelectedRun] = useState<string | null>(null);
  const [compareRun, setCompareRun] = useState<string | null>(null);
  const [results, setResults] = useState<Record<string, any>>({});
  const [compareResults, setCompareResults] = useState<Record<string, any>>({});
  const [resultsLoading, setResultsLoading] = useState(false);
  const [resultsError, setResultsError] = useState<string | null>(null);
  const [queueError, setQueueError] = useState<string | null>(null);
  const [queueMsg, setQueueMsg] = useState<string | null>(null);

  // Dates start empty and are filled from the archive's own bounds below. They
  // used to be hardcoded to a window somebody typed once, which is how the
  // first run anyone tried came back with nothing to evaluate.
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [readiness, setReadiness] = useState<Readiness | null>(null);
  const [readinessError, setReadinessError] = useState<string | null>(null);
  const [cities, setCities] = useState("");
  const [strategyIds, setStrategyIds] = useState("s2_combination_arb");
  const [startingBudget, setStartingBudget] = useState("10000");
  const [label, setLabel] = useState("");

  const runsQ = useQuery<BacktestRun[]>(
    () => supabase.from("backtest_runs").select("*").order("created_at", { ascending: false }).limit(30),
    [],
    15000
  );
  const runs = runsQ.data ?? [];

  const windowQ = useQuery<BacktestWindow[]>(
    () => supabase.from("v_backtest_window").select("*").limit(1), []
  );
  const bounds = (windowQ.data ?? [])[0] ?? null;

  // Default the form to a window that could actually produce trades.
  useEffect(() => {
    if (!bounds || startDate || endDate) return;
    if (bounds.usable_from) setStartDate(bounds.usable_from);
    if (bounds.usable_to) setEndDate(bounds.usable_to);
  }, [bounds, startDate, endDate]);

  /**
   * ASK BEFORE RUNNING, NOT AFTER.
   *
   * runner.py skips a city-day silently when any of four inputs is missing,
   * so an unrunnable window looks exactly like an unprofitable strategy: a
   * completed run with no trades. This asks the same four questions against
   * the exact window in the form, as the form changes, so the answer arrives
   * before twenty minutes of Actions time rather than after.
   */
  useEffect(() => {
    if (!startDate || !endDate) { setReadiness(null); return; }
    let cancelled = false;
    const t = setTimeout(async () => {
      const list = cities.split(",").map((c) => c.trim()).filter(Boolean);
      const { data, error } = await supabase.rpc("backtest_readiness", {
        p_start: startDate, p_end: endDate, p_cities: list.length ? list : null,
      });
      if (cancelled) return;
      if (error) { setReadinessError(error.message); setReadiness(null); return; }
      setReadinessError(null);
      setReadiness(data as Readiness);
    }, 350);
    return () => { cancelled = true; clearTimeout(t); };
  }, [startDate, endDate, cities]);

  useEffect(() => {
    if (!selectedRun) { setResults({}); setResultsError(null); return; }
    setResultsLoading(true);
    supabase.from("backtest_results").select("*").eq("run_id", selectedRun).then(({ data, error }) => {
      setResultsLoading(false);
      if (error) { setResultsError(error.message); setResults({}); return; }
      setResultsError(null);
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
    const { data, error } = await supabase.rpc("queue_backtest", { p_params: params });
    if (error) {
      setQueueError(`${error.message}${error.hint ? ` — ${error.hint}` : ""}`);
      setQueueMsg(null);
      return;
    }
    setQueueError(null);
    // `label` is written by the queueing client rather than by
    // queue_backtest(jsonb), which takes only params. anon has SELECT but
    // no UPDATE on backtest_runs under RLS, so a label set from the
    // browser is best-effort and its failure must not read as a failed
    // queue - the run itself is already recorded.
    let labelNote = "";
    if (label && data) {
      const { error: labelErr } = await supabase.from("backtest_runs").update({ label }).eq("run_id", data);
      if (labelErr) labelNote = " (label not saved: writes to backtest_runs are blocked for the anon key)";
    }
    setQueueMsg(`Queued run ${String(data).slice(0, 8)}. Now press Run in GitHub Actions → Backtest — it no longer polls.${labelNote}`);
    setLabel("");
    runsQ.refresh();
  }

  return (
    <div className="space-y-6">
      <h1 className="text-lg font-semibold">Backtest</h1>
      {/* The binding constraint, read from the archive rather than asserted.
          It said "begins 22 Aug 2026" in prose, which was true on the day it
          was written and is a claim about someone else's database now. */}
      <div className="rounded border border-warn/50 bg-warn/10 p-3 text-xs leading-relaxed text-warn">
        {bounds?.book_from ? (
          <>
            Your book archive begins <b>{bounds.book_from}</b> and holds{" "}
            {fmtInt(bounds.n_books)} snapshot(s). Polymarket publishes no depth history, so there is
            no way to obtain more: <b>strategy profitability cannot be backtested before that date</b>,
            however far back you set the start. Forecast accuracy can, because observations and
            forecasts go back further — {bounds.obs_from ?? "no observations"} onward.
          </>
        ) : (
          <>
            There are no book snapshots yet, so no strategy can be backtested at all: every entry
            price the simulation pays comes from a real book, and Polymarket publishes no depth
            history to backfill from. Run P0.3 for a few days first.
          </>
        )}
      </div>

      <div className="grid gap-6 lg:grid-cols-[1fr_2fr]">
        <div className="space-y-2 rounded border border-border bg-panel p-4 text-sm">
          <div className="flex items-baseline gap-2">
            <h2 className="font-semibold text-muted">Queue a run</h2>
            <Freshness relation="book_snapshots" />
          </div>
          <Field label="Start date"><input value={startDate} onChange={(e) => setStartDate(e.target.value)} className="input" /></Field>
          <Field label="End date"><input value={endDate} onChange={(e) => setEndDate(e.target.value)} className="input" /></Field>
          <Field label="Cities (comma-separated, blank = all)"><input value={cities} onChange={(e) => setCities(e.target.value)} className="input" /></Field>
          <Field label="Strategies (comma-separated ids)"><input value={strategyIds} onChange={(e) => setStrategyIds(e.target.value)} className="input" /></Field>
          <Field label="Starting budget"><input value={startingBudget} onChange={(e) => setStartingBudget(e.target.value)} className="input" /></Field>
          <Field label="Label (optional)"><input value={label} onChange={(e) => setLabel(e.target.value)} className="input" /></Field>
          {/* ---- what this window actually contains -------------------- */}
          {readinessError && <ErrorBox message={readinessError} compact />}
          {readiness && (
            <div
              className={`mt-2 rounded border p-2.5 text-[11px] leading-relaxed ${
                readiness.ok ? "border-good/50 bg-good/10 text-good" : "border-warn/50 bg-warn/10 text-warn"
              }`}
            >
              <div className="font-semibold">
                {readiness.ok ? "This window has data" : "This window will come back empty"}
              </div>
              <p className="mt-0.5">{readiness.summary}</p>
              <dl className="mt-1.5 grid grid-cols-2 gap-x-3 gap-y-0.5 font-mono text-[10px] text-muted">
                <Step label="market-days" n={readiness.markets} of={readiness.markets} />
                <Step label="with buckets" n={readiness.with_bands} of={readiness.markets} />
                <Step label="with an outcome" n={readiness.with_observation} of={readiness.markets} />
                <Step label="with a forecast" n={readiness.with_forecast} of={readiness.markets} />
                <Step label="with a book price" n={readiness.simulatable} of={readiness.markets} />
                <span>
                  <span className="text-muted">window </span>
                  {fmtInt(readiness.days)} days
                </span>
              </dl>
            </div>
          )}
          <button
            onClick={queueRun}
            className={`mt-2 w-full rounded py-1.5 text-white hover:opacity-90 ${
              readiness && !readiness.ok ? "bg-muted" : "bg-accent"
            }`}
          >
            {readiness && !readiness.ok ? "Queue anyway" : "Queue backtest"}
          </button>
          {queueError && <ErrorBox message={queueError} compact />}
          {queueMsg && <div className="rounded border border-good/50 bg-good/10 p-2 text-[11px] text-good">{queueMsg}</div>}
          <p className="text-[10px] text-muted">Run from GitHub Actions → Backtest — no Vercel function, and no polling: a job that checks every 10 minutes and usually finds nothing costs a billed minute each time. See docs/compute_budget.md.</p>
        </div>

        <div>
          <h2 className="mb-2 text-sm font-semibold text-muted">Saved runs</h2>
          <DataState
          relation="backtest_runs"
            loading={runsQ.loading}
            error={runsQ.error}
            isEmpty={runs.length === 0}
            emptyTitle="No backtest runs yet"
            emptyBody={<>Queue one on the left. The UI only writes a row to <code>backtest_runs</code> with <code>status = &apos;queued&apos;</code>; GitHub Actions → <b>Backtest</b> then runs it — press <b>Run workflow</b>, or wait for the daily sweep. Nothing computes in the browser.</>}
            onRetry={runsQ.refresh}
            compact
          >
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
              </tbody>
            </table>
          </div>
          </DataState>
        </div>
      </div>

      {selectedRun && resultsLoading && <Loading label="loading results…" />}
      {selectedRun && resultsError && <ErrorBox message={resultsError} />}
      {selectedRun && !resultsLoading && !resultsError && (
        <RunDashboard
          results={results}
          compareResults={compareRun ? compareResults : undefined}
          status={runs.find((r) => r.run_id === selectedRun)?.status}
          error={runs.find((r) => r.run_id === selectedRun)?.error ?? null}
        />
      )}
    </div>
  );
}

function RunDashboard({
  results, compareResults, status, error,
}: {
  results: Record<string, any>;
  compareResults?: Record<string, any>;
  status?: string;
  error?: string | null;
}) {
  const headline = results.headline;
  const compareHeadline = compareResults?.headline;
  if (!headline) {
    return (
      <div className="rounded border border-dashed border-border p-6 text-center text-sm">
        {status === "failed" ? (
          <>
            <div className="font-semibold text-bad">This run failed.</div>
            {error && <pre className="mx-auto mt-1 max-w-2xl whitespace-pre-wrap break-words text-left font-mono text-xs text-bad">{error}</pre>}
          </>
        ) : status === "queued" ? (
          <>
            <div className="font-semibold">Queued — not picked up yet.</div>
            <div className="mt-1 text-xs text-muted">
              GitHub Actions → <b>Backtest</b> runs it. Press <b>Run workflow</b> there to start it now; a daily sweep picks up anything left queued.
            </div>
          </>
        ) : status === "running" ? (
          <>
            <div className="font-semibold">Running.</div>
            <div className="mt-1 text-xs text-muted">Results appear here as each scope is written.</div>
          </>
        ) : (
          <>
            <div className="font-semibold">No results for this run.</div>
            <div className="mt-1 text-xs text-muted">
              It completed but wrote no <code>backtest_results</code> rows — usually because the date
              range contained no book snapshots to trade against.
            </div>
          </>
        )}
      </div>
    );
  }

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

/**
 * One of the four inputs a simulated city-day needs, as a fraction of the
 * market-days in the window. The number that matters is the last one: a
 * market-day missing any of them is skipped in silence by runner.py.
 */
function Step({ label, n, of }: { label: string; n: number; of: number }) {
  const short = of > 0 && n < of;
  return (
    <span className={short ? "text-warn" : ""}>
      <span className="text-muted">{label} </span>
      <span className="tabular-nums">{n}</span>
      {of > 0 && n !== of ? <span className="text-muted">/{of}</span> : null}
    </span>
  );
}
