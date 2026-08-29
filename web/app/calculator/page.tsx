"use client";

import { Suspense, useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "next/navigation";
import { supabase } from "@/lib/supabase";
import { fmtPct, fmtPrice, fmtUsd } from "@/lib/format";
import type { CalcRecommendation, Opportunity } from "@/lib/types";

interface SelectedLeg {
  band_id: string;
  label: string;
  model_prob: number | null;
  userProb: string;       // Hassan's own number - text so it can be blank
  autoFill: boolean;       // per-leg auto-fill toggle, DEFAULT OFF
}

function CalculatorInner() {
  const searchParams = useSearchParams();
  const [mode, setMode] = useState<"budget" | "target_profit">("budget");
  const [amount, setAmount] = useState("100");
  const [targetProfit, setTargetProfit] = useState("50");
  const [search, setSearch] = useState("");
  const [candidates, setCandidates] = useState<Opportunity[]>([]);
  const [legs, setLegs] = useState<SelectedLeg[]>([]);
  const [result, setResult] = useState<CalcRecommendation | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    const bandId = searchParams.get("band_id");
    if (!bandId) return;
    supabase.from("v_opportunities").select("*").eq("band_id", bandId).limit(1).then(({ data }) => {
      const o = (data as Opportunity[] | null)?.[0];
      if (o) addLeg(o);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchParams]);

  useEffect(() => {
    if (!search) return setCandidates([]);
    const t = setTimeout(async () => {
      const { data } = await supabase
        .from("v_opportunities")
        .select("*")
        .eq("tradeable", true)
        .or(`display_name.ilike.%${search}%,city_key.ilike.%${search}%`)
        .limit(20);
      setCandidates((data as Opportunity[]) ?? []);
    }, 250);
    return () => clearTimeout(t);
  }, [search]);

  function addLeg(o: Opportunity) {
    setLegs((prev) => {
      if (prev.some((l) => l.band_id === o.band_id)) return prev;
      return [...prev, {
        band_id: o.band_id,
        label: `${o.display_name ?? o.city_key} · ${o.band_label ?? `${o.band_lo}-${o.band_hi}`} · ${o.side}`,
        model_prob: o.model_prob, userProb: "", autoFill: false,
      }];
    });
    setSearch("");
  }

  function removeLeg(band_id: string) {
    setLegs((prev) => prev.filter((l) => l.band_id !== band_id));
  }

  function updateLeg(band_id: string, patch: Partial<SelectedLeg>) {
    setLegs((prev) => prev.map((l) => (l.band_id === band_id ? { ...l, ...patch } : l)));
  }

  const overrides = useMemo(() => {
    const out: Record<string, number> = {};
    for (const l of legs) {
      // Model probability NEVER auto-fills the user's own input unless the
      // toggle is explicitly on (default off). Both numbers are always
      // shown side by side regardless.
      if (l.autoFill && l.model_prob !== null) out[l.band_id] = l.model_prob;
      else if (l.userProb !== "" && !Number.isNaN(parseFloat(l.userProb))) out[l.band_id] = parseFloat(l.userProb);
    }
    return out;
  }, [legs]);

  const recompute = useCallback(async () => {
    if (legs.length === 0) return setResult(null);
    setLoading(true);
    const params: Record<string, unknown> = {
      mode, bands: legs.map((l) => l.band_id), user_prob_overrides: overrides, max_slippage_c: 0.05,
    };
    if (mode === "budget") params.amount = parseFloat(amount) || 0;
    else params.target_profit_usd = parseFloat(targetProfit) || 0;

    const { data, error } = await supabase.rpc("calc_recommendation", { p_params: params });
    setLoading(false);
    if (!error) setResult(data as CalcRecommendation);
  }, [mode, amount, targetProfit, legs, overrides]);

  useEffect(() => {
    const t = setTimeout(recompute, 300); // live preview, debounced
    return () => clearTimeout(t);
  }, [recompute]);

  return (
    <div className="space-y-6">
      <h1 className="text-lg font-semibold">Calculator</h1>

      <div className="flex gap-2 text-sm">
        <button onClick={() => setMode("budget")} className={`rounded px-3 py-1 border ${mode === "budget" ? "border-accent text-accent" : "border-border text-muted"}`}>Budget mode</button>
        <button onClick={() => setMode("target_profit")} className={`rounded px-3 py-1 border ${mode === "target_profit" ? "border-accent text-accent" : "border-border text-muted"}`}>Target-profit mode</button>
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        <div className="space-y-4">
          <div>
            <label className="text-xs text-muted">Search bands (single or multi-city)</label>
            <input
              value={search} onChange={(e) => setSearch(e.target.value)}
              placeholder="e.g. paris, tokyo..."
              className="mt-1 w-full rounded border border-border bg-panel2 px-2 py-1 text-sm"
            />
            {candidates.length > 0 && (
              <div className="mt-1 max-h-48 overflow-y-auto rounded border border-border bg-panel2">
                {candidates.map((c) => (
                  <button key={`${c.band_id}-${c.side}`} onClick={() => addLeg(c)} className="block w-full px-2 py-1 text-left text-xs hover:bg-panel">
                    {c.display_name ?? c.city_key} · {c.band_label ?? `${c.band_lo}-${c.band_hi}`} · {c.side} · {fmtPrice(c.market_price)}
                  </button>
                ))}
              </div>
            )}
          </div>

          {mode === "budget" ? (
            <div>
              <label className="text-xs text-muted">Budget (USD)</label>
              <input value={amount} onChange={(e) => setAmount(e.target.value)} className="mt-1 w-full rounded border border-border bg-panel2 px-2 py-1 text-sm font-mono" />
            </div>
          ) : (
            <div>
              <label className="text-xs text-muted">Target profit (USD)</label>
              <input value={targetProfit} onChange={(e) => setTargetProfit(e.target.value)} className="mt-1 w-full rounded border border-border bg-panel2 px-2 py-1 text-sm font-mono" />
            </div>
          )}

          <div className="space-y-2">
            {legs.map((l) => (
              <div key={l.band_id} className="rounded border border-border bg-panel p-2 text-xs">
                <div className="flex items-center justify-between">
                  <span>{l.label}</span>
                  <button onClick={() => removeLeg(l.band_id)} className="text-bad hover:underline">remove</button>
                </div>
                <div className="mt-1 flex items-center gap-3">
                  <span className="text-muted">Model P: <span className="font-mono text-text">{fmtPct(l.model_prob)}</span></span>
                  <span className="text-muted">Your P:</span>
                  <input
                    value={l.userProb} onChange={(e) => updateLeg(l.band_id, { userProb: e.target.value })}
                    disabled={l.autoFill} placeholder="0.00"
                    className="w-16 rounded border border-border bg-panel2 px-1 font-mono disabled:opacity-40"
                  />
                  <label className="flex items-center gap-1 text-muted">
                    <input type="checkbox" checked={l.autoFill} onChange={(e) => updateLeg(l.band_id, { autoFill: e.target.checked })} />
                    auto-fill from model
                  </label>
                </div>
              </div>
            ))}
            {legs.length === 0 && <div className="text-muted text-sm">No bands selected. Search above or click through from Opportunities.</div>}
          </div>
        </div>

        <div className="rounded border border-border bg-panel p-4">
          <div className="mb-2 flex items-center justify-between">
            <h2 className="text-sm font-semibold text-muted">Recommendation</h2>
            {loading && <span className="text-xs text-muted">computing…</span>}
          </div>
          {!result && <div className="text-muted text-sm">Add at least one band to see a live recommendation.</div>}
          {result && (
            <div className="space-y-3 text-sm">
              {result.feasible === false ? (
                <div className="rounded border border-bad p-2 text-bad">{result.reason ?? "Not feasible."}</div>
              ) : (
                <>
                  <div className="grid grid-cols-2 gap-2 font-mono">
                    <Metric label="Total cost" value={fmtUsd(result.total_cost)} />
                    <Metric label="EV net" value={fmtUsd(result.ev_net)} />
                    <Metric label="Best case" value={fmtUsd(result.best_case)} />
                    <Metric label="Worst case" value={fmtUsd(result.worst_case)} />
                    <Metric label="Breakeven" value={fmtPrice(result.breakeven)} />
                    <Metric label="P(covered wins)" value={fmtPct(result.p_covered)} />
                    {result.n_shares !== undefined && <Metric label="N shares" value={result.n_shares.toFixed(1)} />}
                    <Metric label="Fillability" value={fmtPct(result.fillability_pct)} />
                  </div>
                  <div className="flex flex-wrap gap-2">
                    <Badge ok={result.insurance_cap_pass} label="Insurance cap" />
                    {result.capacity_warning && <Warn label="Capacity warning" />}
                    {result.correlation_warning && <Warn label="Correlation warning - these cities may be one bet" />}
                  </div>
                  {result.legs?.length > 0 && (
                    <table className="w-full text-xs">
                      <thead className="text-muted"><tr><th className="text-left">Leg</th><th className="text-right">Shares</th><th className="text-right">Price</th></tr></thead>
                      <tbody>
                        {result.legs.map((leg: any, i: number) => (
                          <tr key={i} className="border-t border-border">
                            <td className="py-1">{leg.band_id?.slice(0, 8)}</td>
                            <td className="py-1 text-right font-mono">{leg.shares?.toFixed?.(1) ?? "—"}</td>
                            <td className="py-1 text-right font-mono">{fmtPrice(leg.avg_fill_price ?? leg.ask)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  )}
                </>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-[10px] text-muted">{label}</div>
      <div>{value}</div>
    </div>
  );
}

function Badge({ ok, label }: { ok: boolean | null | undefined; label: string }) {
  if (ok === null || ok === undefined) return null;
  return (
    <span className={`rounded px-2 py-0.5 text-xs ${ok ? "bg-good/20 text-good" : "bg-bad/20 text-bad"}`}>
      {label}: {ok ? "PASS" : "FAIL"}
    </span>
  );
}

function Warn({ label }: { label: string }) {
  return <span className="rounded bg-warn/20 px-2 py-0.5 text-xs text-warn">{label}</span>;
}

export default function CalculatorPage() {
  return (
    <Suspense fallback={<div className="text-muted">Loading…</div>}>
      <CalculatorInner />
    </Suspense>
  );
}
