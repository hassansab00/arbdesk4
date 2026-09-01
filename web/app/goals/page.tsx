"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { supabase } from "@/lib/supabase";
import { useQuery } from "@/lib/useQuery";
import { DataState, ErrorBox, InlineError } from "@/components/DataState";
import { fmtCompactUsd, fmtPct, fmtUsd, pnlColor } from "@/lib/format";
import {
  buildTiers, centLegs, ceilC, coverageIndices, durAnalytic, guaranteedCheck,
  invertNoTarget, invertYesTarget, MIN_ORDER, minNoTarget, minYesTarget,
  slipBands, spread, spreadNo, spreadWeighted, subMinLegs,
  type RiskMode, type Solve, type SpreadBand, type SpreadResult,
} from "@/lib/spread";
import type { Opportunity } from "@/lib/types";

// ---------------------------------------------------------------------------
// GOALS — profit → spread, exactly as AD4-1's Goal section.
//
// Pick a risk mode, then either set the PROFIT you want (and see the budget
// it needs) or set the BUDGET you have (and see the profit it buys). Same
// four tiers, same $1-order-minimum handling, same guaranteed-only check,
// same cent-exact legs — the difference is that every price, ladder and
// probability here comes from AD4's own live data instead of a typed board.
//
// Market volume is carried through the whole section: each leg shows its
// 24h traded volume, thin legs are flagged on every tier card, and the
// goal feasibility check below weighs the plan against BOTH book depth and
// what the market has actually traded.
// ---------------------------------------------------------------------------

type SideMode = "yes" | "no" | "mix" | "wt";

interface GoalSetting {
  mode?: Solve;
  target_usd?: number;
  budget_usd?: number;
  period_days?: number;
  started_at?: string;
  risk_mode?: RiskMode;
  side?: SideMode;
}

interface VolumeThresholds {
  thin_band_usd_24h?: number;
  thin_city_usd_24h?: number;
  liquidity_half_saturation_usd?: number;
  provisional?: boolean;
  origin?: string;
  note?: string;
}

const RISK_MODES: Array<{ v: RiskMode; label: string; tip: string }> = [
  { v: "vsafe", label: "Very safe", tip: "Widest coverage — every band but the thinnest tail. Becomes true arbitrage on a complete book that sums under $1." },
  { v: "safe", label: "Safe", tip: "Covers the top three bands by your probability. A small tail remains." },
  { v: "mid", label: "Mid", tip: "Top two bands — the anchor-plus-insure shape." },
  { v: "risky", label: "Risky", tip: "The favourite only. Cheapest, but you lose if the high lands anywhere else." },
  { v: "custom", label: "Custom", tip: "Cover every band at or above your own cutoff." },
];

export default function GoalsPage() {
  // ---- controls ----------------------------------------------------------
  const [cityKey, setCityKey] = useState<string>("");
  const [riskMode, setRiskMode] = useState<RiskMode>("safe");
  const [solve, setSolve] = useState<Solve>("target");
  const [side, setSide] = useState<SideMode>("yes");
  const [amountA, setAmountA] = useState("100");
  const [amountB, setAmountB] = useState("25");
  const [thresh, setThresh] = useState("20");
  const [stop, setStop] = useState("0");
  const [slip, setSlip] = useState("0");
  const [useModelProb, setUseModelProb] = useState(true);
  const [manualProb, setManualProb] = useState<Record<string, string>>({});
  const [periodDays, setPeriodDays] = useState("30");
  const [saveMsg, setSaveMsg] = useState<string | null>(null);

  // ---- data --------------------------------------------------------------
  const cities = useQuery(
    () => supabase.from("v_opportunities").select("city_key,display_name,resolution_date").order("city_key"),
    []
  );

  const cityOptions = useMemo(() => {
    const rows = (cities.data as Array<{ city_key: string; display_name: string | null }> | null) ?? [];
    const m = new Map<string, string>();
    for (const r of rows) if (!m.has(r.city_key)) m.set(r.city_key, r.display_name ?? r.city_key);
    return Array.from(m.entries()).sort((a, b) => a[1].localeCompare(b[1]));
  }, [cities.data]);

  useEffect(() => {
    if (!cityKey && cityOptions.length) setCityKey(cityOptions[0][0]);
  }, [cityOptions, cityKey]);

  const opps = useQuery(
    () =>
      cityKey
        ? supabase.from("v_opportunities").select("*").eq("city_key", cityKey).eq("side", "YES")
        : Promise.resolve({ data: [] as Opportunity[], error: null }),
    [cityKey],
    30000
  );

  const books = useQuery(
    () => supabase.from("v_latest_book").select("band_id,best_bid,best_ask,bid_levels,ask_levels,ask_levels_source,bid_levels_source"),
    [cityKey],
    30000
  );

  const settings = useQuery(
    () => supabase.from("settings").select("key,value").in("key", ["goal", "volume_thresholds", "bankroll"]),
    []
  );

  const settingsMap = useMemo(() => {
    const rows = (settings.data as Array<{ key: string; value: unknown }> | null) ?? [];
    return Object.fromEntries(rows.map((r) => [r.key, r.value])) as Record<string, any>;
  }, [settings.data]);

  const volumeThresholds = (settingsMap.volume_thresholds ?? {}) as VolumeThresholds;
  const thinUsd = volumeThresholds.thin_band_usd_24h ?? 0;
  const savedGoal = (settingsMap.goal ?? null) as GoalSetting | null;
  const bankroll = (settingsMap.bankroll?.amount ?? null) as number | null;

  // Restore a saved goal once, when it first arrives.
  const [restored, setRestored] = useState(false);
  useEffect(() => {
    if (restored || !savedGoal) return;
    setRestored(true);
    if (savedGoal.mode) setSolve(savedGoal.mode);
    if (savedGoal.risk_mode) setRiskMode(savedGoal.risk_mode);
    if (savedGoal.side) setSide(savedGoal.side);
    if (savedGoal.period_days) setPeriodDays(String(savedGoal.period_days));
    if (savedGoal.mode === "budget" && savedGoal.budget_usd != null) setAmountA(String(savedGoal.budget_usd));
    else if (savedGoal.target_usd != null) setAmountA(String(savedGoal.target_usd));
  }, [savedGoal, restored]);

  // ---- realised progress against the saved goal --------------------------
  const trades = useQuery(
    () =>
      savedGoal?.started_at
        ? supabase.from("paper_trades").select("net_pnl,closed_at").gte("closed_at", savedGoal.started_at)
        : Promise.resolve({ data: [] as Array<{ net_pnl: number | null; closed_at: string | null }>, error: null }),
    [savedGoal?.started_at]
  );
  const realised = ((trades.data as Array<{ net_pnl: number | null }> | null) ?? []).reduce(
    (s, t) => s + (t.net_pnl ?? 0), 0
  );

  // ---- assemble the board -------------------------------------------------
  const rawBands: SpreadBand[] = useMemo(() => {
    const rows = (opps.data as Opportunity[] | null) ?? [];
    const bookRows = (books.data as Array<any> | null) ?? [];
    const bookByBand = new Map(bookRows.map((b) => [b.band_id as string, b]));
    return rows
      .map((o) => {
        const bk = bookByBand.get(o.band_id);
        const asks: Array<[number, number]> = Array.isArray(bk?.ask_levels)
          ? bk.ask_levels
              .filter((l: any) => l && l.price != null && l.size != null)
              .map((l: any) => [Number(l.price) * 100, Number(l.size)] as [number, number])
              .sort((a: [number, number], b: [number, number]) => a[0] - b[0])
          : [];
        // NO-side ladder is the complement of the YES bid ladder — AD4 has
        // no separately stored NO book (docs/schema_assumptions.md).
        const noAsks: Array<[number, number]> = Array.isArray(bk?.bid_levels)
          ? bk.bid_levels
              .filter((l: any) => l && l.price != null && l.size != null)
              .map((l: any) => [(1 - Number(l.price)) * 100, Number(l.size)] as [number, number])
              .sort((a: [number, number], b: [number, number]) => a[0] - b[0])
          : [];
        const yesCents = bk?.best_ask != null ? Number(bk.best_ask) * 100 : o.market_price != null ? o.market_price * 100 : 0;
        const noCents = bk?.best_bid != null ? (1 - Number(bk.best_bid)) * 100 : 0;
        const modelPct = o.model_prob != null ? o.model_prob * 100 : 0;
        const manual = manualProb[o.band_id];
        const yourPct = useModelProb
          ? modelPct
          : manual !== undefined && manual !== "" && !Number.isNaN(parseFloat(manual))
          ? parseFloat(manual)
          : 0;
        return {
          band_id: o.band_id,
          label: o.band_label ?? `${o.band_lo ?? "?"}-${o.band_hi ?? "?"}`,
          yes: yesCents,
          no: noCents,
          yourPct,
          yesBook: asks,
          noBook: noAsks,
          volumeUsd: o.volume_usd ?? 0,
          city_key: o.city_key,
          ladderSource: (bk?.ask_levels_source ?? "none") as string,
        } as SpreadBand;
      })
      .sort((a, b) => a.label.localeCompare(b.label, undefined, { numeric: true }));
  }, [opps.data, books.data, useModelProb, manualProb]);

  const B = useMemo(() => slipBands(rawBands, parseFloat(slip) || 0), [rawBands, slip]);
  const cov = useMemo(() => coverageIndices(B, riskMode, parseFloat(thresh) || 0), [B, riskMode, thresh]);

  const amtA = parseFloat(amountA) || 0;
  const amtB = parseFloat(amountB) || 0;
  const stopFloor = parseFloat(stop) || 0;

  // Solve direction. This is the whole section: profit -> budget, or
  // budget -> profit. Both engines are linear in target, so the budget
  // direction is an exact inversion, not a search.
  const targetA = solve === "budget"
    ? side === "no" ? invertNoTarget(B, cov, amtA) : invertYesTarget(B, cov, amtA)
    : amtA;
  const targetB = solve === "budget" ? invertNoTarget(B, cov, amtB) : amtB;

  const yLock: SpreadResult | null = side === "yes" || side === "mix" ? spread(B, cov, targetA, thinUsd) : null;
  const nLock: SpreadResult | null =
    side === "no" ? spreadNo(B, cov, targetA, thinUsd) : side === "mix" ? spreadNo(B, cov, targetB, thinUsd) : null;
  const wLock = side === "wt" ? spreadWeighted(B, cov, amtA, solve, thinUsd) : null;

  const tiers = useMemo(
    () => buildTiers(B, targetA, stopFloor, solve === "budget" && side === "yes", amtA, thinUsd),
    [B, targetA, stopFloor, solve, side, amtA, thinUsd]
  );
  const guaranteed = useMemo(() => guaranteedCheck(B, targetA, thinUsd), [B, targetA, thinUsd]);

  // ---- multi-day projection ----------------------------------------------
  const activePlan = side === "no" ? nLock : yLock;
  const projection = useMemo(() => {
    const days = Math.max(1, parseInt(periodDays) || 1);
    if (!activePlan?.feasible) return null;
    const q = activePlan.coverageProb;
    const P = activePlan.profitIfCovered ?? 0;
    const L = activePlan.budget ?? 0;
    return { days, ...durAnalytic(q, P, L, days) };
  }, [activePlan, periodDays]);

  // ---- save --------------------------------------------------------------
  const saveGoal = useCallback(async () => {
    const payload: GoalSetting = {
      mode: solve,
      target_usd: solve === "target" ? amtA : (activePlan?.profitIfCovered ?? 0),
      budget_usd: solve === "budget" ? amtA : (activePlan?.budget ?? 0),
      period_days: parseInt(periodDays) || 30,
      started_at: savedGoal?.started_at ?? new Date().toISOString(),
      risk_mode: riskMode,
      side,
    };
    const { error } = await supabase.rpc("update_setting", { p_key: "goal", p_value: payload });
    setSaveMsg(error ? `Save failed: ${error.message}` : "Goal saved.");
    settings.refresh();
    setTimeout(() => setSaveMsg(null), 4000);
  }, [solve, amtA, activePlan, periodDays, savedGoal, riskMode, side, settings]);

  const loading = opps.loading || books.loading || cities.loading;
  const err = opps.error ?? books.error ?? cities.error;
  const isEmpty = !loading && !err && rawBands.length === 0;

  // On the real database some bands have no stored order book at all - only
  // cumulative USD depth per cent-tier. Those ladders are reconstructed and
  // are coarser than a real one, so say so rather than let the fill prices
  // below imply a precision the data does not have.
  const syntheticLadders = rawBands.filter((b) => b.ladderSource === "synthetic_tiers");
  const noLadders = rawBands.filter((b) => !b.ladderSource || b.ladderSource === "none");

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-lg font-semibold">Goals — profit → spread</h1>
        <p className="mt-1 max-w-3xl text-xs leading-relaxed text-muted">
          Choose a risk mode, then set either the profit you want or the budget you have — the other
          side is solved for you. Every price walks the real ask ladder from{" "}
          <code>book_snapshots</code>, so the numbers here are the average fill a real market order
          gets, not top-of-book. Traded volume is shown on every leg: depth tells you what the
          current quote can absorb, volume tells you whether anyone trades this band at all.
        </p>
        {syntheticLadders.length > 0 && (
          <p className="mt-2 max-w-3xl rounded border border-warn/40 bg-warn/10 px-3 py-2 text-xs leading-relaxed text-warn">
            <b>{syntheticLadders.length} of {rawBands.length} bands have no stored order book.</b>{" "}
            Their ladders are reconstructed from the cumulative depth totals on the snapshot
            (<code>ask_usd_1c</code> … <code>ask_total_usd</code>), priced at the outer edge of each
            tier. Total depth is right; the shape inside a tier is an approximation, so treat the
            average fill prices for those bands as conservative estimates rather than quotes.
          </p>
        )}
        {noLadders.length > 0 && (
          <p className="mt-2 max-w-3xl rounded border border-border px-3 py-2 text-xs leading-relaxed text-muted">
            {noLadders.length} of {rawBands.length} bands have no book at all — no ladder and no
            depth totals on the latest snapshot. They fall back to top-of-book, so any size beyond
            the touch is unpriced for them. Run the book/volume snapshot workflow
            (<code>n8n/P0.3_book_volume_snapshot.scaffold.json</code>) to fill them in.
          </p>
        )}
      </div>

      {/* ------------------------------------------------------ controls */}
      <div className="space-y-4 rounded border border-border bg-panel p-4">
        <div className="flex flex-wrap items-center gap-3">
          <label className="text-xs text-muted">City</label>
          <select
            value={cityKey}
            onChange={(e) => setCityKey(e.target.value)}
            className="rounded border border-border bg-panel2 px-2 py-1 text-sm"
          >
            {cityOptions.length === 0 && <option value="">no cities yet</option>}
            {cityOptions.map(([k, name]) => (
              <option key={k} value={k}>{name}</option>
            ))}
          </select>
          <Seg
            label="Risk mode"
            value={riskMode}
            onChange={(v) => setRiskMode(v as RiskMode)}
            options={RISK_MODES.map((m) => ({ v: m.v, label: m.label, title: m.tip }))}
          />
        </div>

        <div className="flex flex-wrap items-end gap-4">
          <Seg
            label="Solve"
            value={solve}
            onChange={(v) => setSolve(v as Solve)}
            options={[
              { v: "target", label: "I set profit", title: "Set the profit you want and see the budget it needs." },
              { v: "budget", label: "I set budget", title: "Set a budget and see the profit it buys." },
            ]}
          />
          <Field label={solve === "budget" ? "Budget (USD)" : "Target profit (USD)"} hint={solve === "budget" ? "What you are willing to spend." : "The profit if a covered band wins."}>
            <input value={amountA} onChange={(e) => setAmountA(e.target.value)} className="input w-28" inputMode="decimal" />
          </Field>
          {side === "mix" && (
            <Field label={solve === "budget" ? "No-lock budget" : "No-lock target"} hint="Sized independently and added on top of the Yes-lock leg.">
              <input value={amountB} onChange={(e) => setAmountB(e.target.value)} className="input w-24" inputMode="decimal" />
            </Field>
          )}
          {riskMode === "custom" && (
            <Field label="Cover at/above (%)" hint="Cover every band at or above this probability. Lower = wider coverage, bigger budget.">
              <input value={thresh} onChange={(e) => setThresh(e.target.value)} className="input w-20" inputMode="decimal" />
            </Field>
          )}
          <Field label="Stop floor (max budget)" hint="Any plan needing more than this is flagged. 0 = off.">
            <input value={stop} onChange={(e) => setStop(e.target.value)} className="input w-24" inputMode="decimal" />
          </Field>
          <Field label="Slippage buffer (%)" hint="Extra headroom on top of live prices. Ladders are already walked, so leave at 0 unless you want a margin.">
            <input value={slip} onChange={(e) => setSlip(e.target.value)} className="input w-20" inputMode="decimal" />
          </Field>
          <Field label="Period (days)" hint="Horizon for the multi-day projection below.">
            <input value={periodDays} onChange={(e) => setPeriodDays(e.target.value)} className="input w-20" inputMode="numeric" />
          </Field>
        </div>

        <div className="flex flex-wrap items-center gap-4 border-t border-border pt-3">
          <Seg
            label="Position side"
            value={side}
            onChange={(v) => setSide(v as SideMode)}
            options={[
              { v: "yes", label: "Yes-lock", title: "Equal YES shares of every covered band. Any covered winner pays the target." },
              { v: "no", label: "No-lock", title: "Equal NO shares. Pays when the winner is not in the covered set — needs a cheap covered cluster." },
              { v: "mix", label: "Mix", title: "Both legs, sized independently." },
              { v: "wt", label: "Weighted", title: "Stakes split by conviction rather than equal shares — every covered band pays, unevenly." },
            ]}
            subtle
          />
          <label className="flex items-center gap-2 text-xs text-muted">
            <input type="checkbox" checked={useModelProb} onChange={(e) => setUseModelProb(e.target.checked)} />
            use model probability
          </label>
          <span className="text-[10px] text-muted">
            {useModelProb
              ? "Model probability is driving coverage and EV. Untick to type your own."
              : "Your own numbers are driving this. The model's are still shown per band below."}
          </span>
        </div>
      </div>

      {saveMsg && <div className="rounded border border-border bg-panel2 px-2 py-1 text-xs text-muted">{saveMsg}</div>}
      <InlineError message={settings.error} />

      <DataState
        loading={loading}
        error={err}
        isEmpty={isEmpty}
        emptyTitle="No priced bands to plan against yet"
        emptyBody={
          <>
            The Goals engine needs live YES prices from <code>v_opportunities</code> for at least one
            city. That view is empty until the probability and edge engines have run at least once:
            GitHub Actions → <b>Probabilities</b>, then <b>Signals</b>. See{" "}
            <code>docs/GO_LIVE.md</code> steps 2–3.
          </>
        }
        onRetry={() => { opps.refresh(); books.refresh(); }}
      >
        <>
          {/* -------------------------------------------- band inputs */}
          <section className="rounded border border-border bg-panel p-3">
            <h2 className="mb-2 text-sm font-semibold text-muted">
              Bands on this board{" "}
              <span className="font-normal text-[10px]">
                — highlighted rows are covered by the current risk mode
              </span>
            </h2>
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead className="text-muted">
                  <tr>
                    <th className="p-1 text-left">Band</th>
                    <th className="p-1 text-right">Yes ¢</th>
                    <th className="p-1 text-right">No ¢</th>
                    <th className="p-1 text-right">Model %</th>
                    <th className="p-1 text-right">Your %</th>
                    <th className="p-1 text-right">Vol 24h</th>
                    <th className="p-1 text-right">Ladder</th>
                  </tr>
                </thead>
                <tbody>
                  {rawBands.map((band, i) => {
                    const covered = cov.includes(i);
                    const thin = (band.volumeUsd ?? 0) < thinUsd;
                    const modelRow = (opps.data as Opportunity[] | null)?.find((o) => o.band_id === band.band_id);
                    return (
                      <tr key={band.band_id} className={`border-t border-border ${covered ? "bg-accent/10" : ""}`}>
                        <td className="p-1">{band.label}</td>
                        <td className="p-1 text-right font-mono">{band.yes ? band.yes.toFixed(1) : "—"}</td>
                        <td className="p-1 text-right font-mono">{band.no ? band.no.toFixed(1) : "—"}</td>
                        <td className="p-1 text-right font-mono text-muted">
                          {modelRow?.model_prob != null ? (modelRow.model_prob * 100).toFixed(1) : "—"}
                        </td>
                        <td className="p-1 text-right">
                          {useModelProb ? (
                            <span className="font-mono">{band.yourPct.toFixed(1)}</span>
                          ) : (
                            <input
                              value={manualProb[band.band_id] ?? ""}
                              onChange={(e) => setManualProb((p) => ({ ...p, [band.band_id]: e.target.value }))}
                              placeholder="0"
                              className="w-14 rounded border border-border bg-panel2 px-1 text-right font-mono"
                            />
                          )}
                        </td>
                        <td className={`p-1 text-right font-mono ${thin ? "text-warn" : band.volumeUsd ? "" : "text-muted"}`}>
                          {fmtCompactUsd(band.volumeUsd)}{thin && band.volumeUsd > 0 ? " ⚠" : ""}
                        </td>
                        <td className="p-1 text-right font-mono text-muted">
                          {band.yesBook.length ? `${band.yesBook.length} lvl` : "flat"}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <p className="mt-2 text-[10px] text-muted">
              ⚠ marks a band whose 24h traded volume is under{" "}
              {fmtUsd(thinUsd)} — the thin-market threshold in{" "}
              <code>settings.volume_thresholds</code>.{" "}
              {volumeThresholds.provisional && (
                <span className="text-warn">
                  That threshold is a provisional placeholder with no evidential basis; it is
                  UI-settable and should be replaced with a measured value.
                </span>
              )}{" "}
              &ldquo;flat&rdquo; means no depth ladder was stored for that band, so its budget is
              priced off top-of-book — use the slippage buffer as a margin.
            </p>
          </section>

          <div className="mt-4 grid gap-4 lg:grid-cols-2">
            {/* ------------------------------------------ the spread */}
            <section className="rounded border border-border bg-panel p-4">
              <h2 className="text-sm font-semibold">
                {side === "no" ? "No-lock spread" : side === "mix" ? "Mix: Yes-lock + No-lock" : side === "wt" ? "Weighted spread (by conviction)" : "Your spread"}
              </h2>
              <p className="mb-3 mt-0.5 text-[11px] text-muted">
                {side === "no"
                  ? "Equal NO shares on every covered band. If a covered band wins, exactly one leg fails to pay; if the winner is outside coverage, every leg pays."
                  : side === "wt"
                  ? "Stakes split by conviction rather than equal shares — every covered band pays, just unevenly."
                  : side === "mix"
                  ? "Both structures sized independently and shown together."
                  : "Equal YES shares of every covered band. Any covered winner pays exactly the target; an uncovered winner loses the budget."}
              </p>
              <div className="mb-2 font-mono text-[10px] text-muted">
                covering {cov.length} of {B.length} bands ·{" "}
                {cov.map((i) => B[i].label).join(", ") || "none"}
              </div>

              {side === "wt" ? (
                <WeightedPane res={wLock} solve={solve} thinUsd={thinUsd} />
              ) : (
                <>
                  {(side === "yes" || side === "mix") && (
                    <LockPane
                      title={side === "mix" ? "Yes-lock leg" : undefined}
                      res={yLock}
                      kind="yes"
                      solve={solve}
                      B={B}
                      cov={cov}
                      thinUsd={thinUsd}
                    />
                  )}
                  {(side === "no" || side === "mix") && (
                    <LockPane
                      title={side === "mix" ? "No-lock leg" : undefined}
                      res={nLock}
                      kind="no"
                      solve={solve}
                      B={B}
                      cov={cov}
                      thinUsd={thinUsd}
                    />
                  )}
                </>
              )}

              <button
                onClick={saveGoal}
                className="mt-3 w-full rounded bg-accent py-1.5 text-sm text-white hover:opacity-90 disabled:opacity-40"
                disabled={!activePlan?.feasible && side !== "wt"}
              >
                Save as my goal
              </button>
            </section>

            {/* ------------------------------------ guaranteed-only */}
            <section className="rounded border border-border bg-panel p-4">
              <h2 className="text-sm font-semibold">Guaranteed-only</h2>
              <p className="mb-3 mt-0.5 text-[11px] text-muted">
                Risk-free only if covering every outcome clears the book-wide check: Yes-lock needs
                every priced band to sum under $1; No-lock needs the same sum under (band count − 1).
              </p>
              <Kv k="Book sum (Yes, per $1)" v={`$${guaranteed.bookSumYes.toFixed(3)}`} cls={guaranteed.bookSumYes < 1 ? "text-good" : "text-muted"} />
              <Kv k="Overround" v={`${((guaranteed.bookSumYes - 1) * 100).toFixed(1)}%`} />
              <Kv k="Board complete" v={guaranteed.complete ? "yes" : `no — ${guaranteed.unpriced} unpriced`} cls={guaranteed.complete ? "text-good" : "text-warn"} />
              {guaranteed.yes.available ? (
                <div className="mt-2 rounded border border-good/50 bg-good/10 p-2 text-xs text-good">
                  Full-book Yes-lock is available: budget {fmtUsd(guaranteed.yes.budget)}, profit{" "}
                  {fmtUsd(guaranteed.yes.profitIfCovered)} whichever band wins.
                </div>
              ) : (
                <div className="mt-2 rounded border border-border bg-panel2 p-2 text-xs text-muted">
                  No risk-free Yes-lock right now:{" "}
                  {guaranteed.complete ? "the priced bands cost over $1 per $1." : "the board is incomplete, so a full-book cover cannot be priced."}
                </div>
              )}
              {guaranteed.no.available && (
                <div className="mt-2 rounded border border-good/50 bg-good/10 p-2 text-xs text-good">
                  Full-book No-lock also clears: budget {fmtUsd(guaranteed.no.budget)}.
                </div>
              )}

              <h3 className="mt-4 text-xs font-semibold text-muted">Projection over {periodDays} days</h3>
              {projection ? (
                <div className="mt-1 space-y-1">
                  <Kv k="Chance a covered band wins (per day)" v={fmtPct(activePlan?.coverageProb ?? null)} />
                  <Kv k="Expected P&L" v={fmtUsd(projection.evTotal, { signed: true })} cls={pnlColor(projection.evTotal)} />
                  <Kv k="Chance of finishing profitable" v={fmtPct(projection.pProfit)} />
                  <Kv k="Best case" v={fmtUsd(projection.best)} cls="text-good" />
                  <Kv k="Worst case" v={fmtUsd(projection.worst)} cls="text-bad" />
                  {bankroll !== null && projection.worst < -bankroll && (
                    <div className="rounded border border-bad/50 bg-bad/10 p-2 text-[11px] text-bad">
                      Worst case exceeds your whole bankroll of {fmtUsd(bankroll)}. This plan can
                      bust the account inside the period.
                    </div>
                  )}
                  {projection.dailyEV < 0 && (
                    <div className="rounded border border-warn/50 bg-warn/10 p-2 text-[11px] text-warn">
                      Daily EV is negative — running this longer just loses more, it does not
                      converge to the target.
                    </div>
                  )}
                  <p className="pt-1 text-[10px] text-muted">
                    Compounds today&apos;s single-day economics forward. It assumes each day is an
                    independent repeat of this exact spread, which is a modelling choice, not a
                    measurement — correlated weather days violate it.
                  </p>
                </div>
              ) : (
                <div className="mt-1 text-xs text-muted">
                  No feasible plan to project. Adjust the risk mode, target or coverage above.
                </div>
              )}

              {savedGoal?.started_at && (
                <div className="mt-4 border-t border-border pt-3">
                  <h3 className="text-xs font-semibold text-muted">Progress against saved goal</h3>
                  <Kv
                    k="Realised (net) since goal set"
                    v={fmtUsd(realised, { signed: true })}
                    cls={pnlColor(realised)}
                  />
                  <Kv k="Goal" v={fmtUsd(savedGoal.mode === "budget" ? savedGoal.budget_usd ?? 0 : savedGoal.target_usd ?? 0)} />
                  <div className="mt-1 h-2 rounded bg-panel2">
                    <div
                      className="h-2 rounded bg-accent"
                      style={{
                        width: `${Math.max(0, Math.min(100, (realised / Math.max(1e-9, savedGoal.target_usd ?? 1)) * 100))}%`,
                      }}
                    />
                  </div>
                  <InlineError message={trades.error} />
                </div>
              )}
            </section>
          </div>

          {/* ------------------------------------------------ tier cards */}
          <section className="mt-4">
            <h2 className="mb-1 text-sm font-semibold text-muted">Four tiers for your {solve === "budget" ? "budget" : "target"}</h2>
            <p className="mb-2 text-[11px] text-muted">
              The same four risk modes side by side, priced to the cent against the same
              {solve === "budget" ? " budget" : " profit target"}. The card matching your selected
              mode is highlighted, and matches the pane above exactly.
            </p>
            <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
              {tiers.map((t) => (
                <button
                  key={t.key}
                  onClick={() => setRiskMode(t.key)}
                  title={RISK_MODES.find((m) => m.v === t.key)?.tip}
                  className={`rounded border p-3 text-left text-xs ${
                    riskMode === t.key ? "border-accent bg-panel2" : "border-border bg-panel hover:border-muted"
                  }`}
                >
                  <div className="flex items-center justify-between">
                    <span className="font-semibold">{t.name}</span>
                    <span
                      className={`rounded px-1.5 py-0.5 text-[10px] ${
                        t.chip === "ok" ? "bg-good/20 text-good" : t.chip === "warn" ? "bg-warn/20 text-warn" : "bg-bad/20 text-bad"
                      }`}
                    >
                      {t.msg}
                    </span>
                  </div>
                  <div className="mt-0.5 text-[10px] text-muted">{t.desc}</div>
                  {t.s.feasible ? (
                    <div className="mt-2 space-y-0.5 font-mono">
                      <div className="text-base text-warn">{fmtUsd(t.s.budget)}</div>
                      <Row k="profit if hit" v={fmtUsd(t.s.profitIfCovered, { signed: true })} cls="text-good" />
                      <Row k="coverage" v={fmtPct(t.s.coverageProb, 0)} />
                      <Row k="tail loss" v={fmtUsd(t.s.lossIfUncovered)} cls="text-bad" />
                      <Row k="EV" v={fmtUsd(t.s.ev, { signed: true })} cls={pnlColor(t.s.ev)} />
                      <Row k="24h volume" v={fmtCompactUsd(t.s.volumeUsd)} cls={(t.s.thinLegs?.length ?? 0) > 0 ? "text-warn" : ""} />
                    </div>
                  ) : (
                    <div className="mt-2 text-[11px] text-muted">
                      unavailable —{" "}
                      {t.s.reason === "overpriced"
                        ? "covered bands cost over $1 per $1"
                        : t.s.reason === "depth"
                        ? `book too thin (max profit ${fmtUsd(t.s.maxProfit ?? 0)})`
                        : "no priced bands"}
                    </div>
                  )}
                </button>
              ))}
            </div>
          </section>
        </>
      </DataState>
    </div>
  );
}

// ---------------------------------------------------------------------------

function LockPane({
  title, res, kind, solve, B, cov, thinUsd,
}: {
  title?: string;
  res: SpreadResult | null;
  kind: "yes" | "no";
  solve: Solve;
  B: SpreadBand[];
  cov: number[];
  thinUsd: number;
}) {
  if (!res) return null;
  if (!res.feasible) {
    return (
      <div className="mb-3">
        {title && <div className="mb-1 text-xs font-semibold text-muted">{title}</div>}
        <div className="rounded border border-bad/50 bg-bad/10 p-2 text-xs text-bad">
          {res.reason === "overpriced"
            ? kind === "yes"
              ? `Covered bands cost ${((res.sumP ?? 0) * 100).toFixed(1)}¢ per $1 — at or over full price, so no Yes-lock exists here.`
              : "The covered NO asks sum too high for a No-lock — it needs a cheap covered cluster."
            : res.reason === "depth"
            ? `Order book too thin. The largest profit the live asks can guarantee is ${fmtUsd(res.maxProfit ?? 0)} on a budget of ${fmtUsd(res.maxBudget ?? 0)}.`
            : res.reason === "need2"
            ? "A No-lock needs at least two covered bands."
            : "No covered band has a live price."}
        </div>
      </div>
    );
  }

  const legs = centLegs(res.legs ?? []);
  const budget = legs.reduce((a, L) => a + L.cost, 0);
  const sub = subMinLegs(legs);
  const minT = kind === "yes" ? minYesTarget(B, cov) : minNoTarget(B, cov);
  const thin = res.thinLegs ?? [];

  return (
    <div className="mb-3 space-y-1">
      {title && <div className="mb-1 text-xs font-semibold text-muted">{title}</div>}
      {res.depthCapped && (
        <div className="rounded border border-warn/50 bg-warn/10 p-2 text-[11px] text-warn">
          This is the book&apos;s edge — at {res.shares?.toFixed(0)} shares you are buying every ask
          on the thinnest covered leg. The largest version of this spread the current book can fill.
        </div>
      )}
      {sub > 0 && (
        <div className="rounded border border-warn/50 bg-warn/10 p-2 text-[11px] text-warn">
          {sub} leg{sub > 1 ? "s" : ""} under Polymarket&apos;s ${MIN_ORDER} order minimum — this
          exact spread cannot be placed. Smallest {solve === "budget" ? "budget" : "profit target"}{" "}
          that clears $1 on every leg:{" "}
          <b>{minT != null ? fmtUsd(ceilC(minT)) : "unavailable"}</b>.
        </div>
      )}
      {thin.length > 0 && (
        <div className="rounded border border-warn/50 bg-warn/10 p-2 text-[11px] text-warn">
          {thin.length} leg{thin.length > 1 ? "s" : ""} sit on bands that have traded under{" "}
          {fmtUsd(thinUsd)} in the last 24h ({thin.map((L) => L.label).join(", ")}). The book quotes
          them, but almost nobody trades them — expect the fill to move the price further than the
          ladder suggests.
        </div>
      )}
      <Kv k="Budget needed" v={fmtUsd(budget)} cls="text-warn" />
      <Kv
        k={kind === "yes" ? "Profit if a covered band wins" : "Profit if a covered band wins"}
        v={fmtUsd(res.profitIfCovered, { signed: true })}
        cls="text-good"
      />
      {kind === "no" && (
        <Kv k="Profit if the winner is OUTSIDE coverage" v={fmtUsd(res.profitIfUncovered, { signed: true })} cls="text-good" />
      )}
      <Kv k="Total return if it wins (profit + stake)" v={fmtUsd(budget + (res.profitIfCovered ?? 0))} />
      <Kv k="Chance a covered band wins" v={fmtPct(res.coverageProb)} />
      {kind === "yes" && <Kv k="If an uncovered band wins" v={fmtUsd(res.lossIfUncovered)} cls="text-bad" />}
      <Kv k="Tail risk" v={fmtPct(res.tailProb ?? null)} />
      <Kv k="Expected value" v={fmtUsd(res.ev, { signed: true })} cls={pnlColor(res.ev)} />
      <Kv k="24h traded volume across legs" v={fmtCompactUsd(res.volumeUsd)} cls={thin.length ? "text-warn" : ""} />

      <div className="mt-2 divide-y divide-border rounded border border-border">
        {legs.map((L) => (
          <div key={L.band_id} className={`flex justify-between px-2 py-1 text-[11px] ${L.cost < MIN_ORDER ? "opacity-60" : ""}`}>
            <span>
              {L.label} · {L.shares.toFixed(2)} sh @ {L.price}¢ {kind === "no" ? "No" : ""}
              {(L.volumeUsd ?? 0) < thinUsd && <span className="ml-1 text-warn">thin</span>}
            </span>
            <span className="font-mono">{fmtUsd(L.cost)}</span>
          </div>
        ))}
      </div>
      <div className={`rounded p-2 text-[11px] ${(res.ev ?? 0) >= 0 ? "bg-good/10 text-good" : "bg-warn/10 text-warn"}`}>
        Buy the cent-exact legs above (≈{res.shares?.toFixed(2)} sh each) — these numbers match a
        real fill to the cent.{" "}
        {(res.ev ?? 0) >= 0
          ? "Positive EV on the probabilities driving this board."
          : "Negative EV here — this only wins if your read beats the market."}
      </div>
    </div>
  );
}

function WeightedPane({ res, solve, thinUsd }: { res: ReturnType<typeof spreadWeighted> | null; solve: Solve; thinUsd: number }) {
  if (!res) return null;
  if (!res.feasible) {
    return (
      <div className="rounded border border-bad/50 bg-bad/10 p-2 text-xs text-bad">
        {res.reason === "depth"
          ? `Order books too thin for this target in weighted mode — the largest minimum covered return the live asks can guarantee is ${fmtUsd(res.depthMax ?? 0)}.`
          : res.reason === "no_edge"
          ? "Target-solve impossible: on at least one covered band the market price is at or above your normalised read, so its return cannot be positive at any budget. Weighted needs your edge on every covered band."
          : res.reason === "inputs"
          ? "Weighted mode needs a probability and a live Yes price on every covered band."
          : "No band is covered by this mode. Try a wider one."}
      </div>
    );
  }
  const sub = subMinLegs(res.legs ?? []);
  const thin = res.thinLegs ?? [];
  return (
    <div className="space-y-1">
      {sub > 0 && (
        <div className="rounded border border-warn/50 bg-warn/10 p-2 text-[11px] text-warn">
          {sub} stake{sub > 1 ? "s" : ""} under the ${MIN_ORDER} minimum — smallest{" "}
          {solve === "budget" ? "budget" : "min-profit target"} that clears it:{" "}
          <b>{res.minOrderHint != null ? fmtUsd(res.minOrderHint) : "unavailable"}</b>.
        </div>
      )}
      {thin.length > 0 && (
        <div className="rounded border border-warn/50 bg-warn/10 p-2 text-[11px] text-warn">
          {thin.length} leg{thin.length > 1 ? "s" : ""} on bands trading under {fmtUsd(thinUsd)} in 24h.
        </div>
      )}
      <Kv k="Budget (split by conviction)" v={fmtUsd(res.budget)} cls="text-warn" />
      <Kv
        k="Covered returns"
        v={`${fmtUsd(res.minReturn, { signed: true })} to ${fmtUsd(res.maxReturn, { signed: true })}`}
        cls={pnlColor(res.minReturn)}
      />
      <Kv k="Chance a covered band wins" v={fmtPct(res.coverageProb)} />
      <Kv k="If an uncovered band wins" v={fmtUsd(res.lossIfUncovered)} cls="text-bad" />
      <Kv k="Expected value" v={fmtUsd(res.ev, { signed: true })} cls={pnlColor(res.ev)} />
      <Kv k="24h traded volume across legs" v={fmtCompactUsd(res.volumeUsd)} cls={thin.length ? "text-warn" : ""} />
      <div className="mt-2 divide-y divide-border rounded border border-border">
        {(res.legs ?? []).map((L) => (
          <div key={L.band_id} className="flex justify-between px-2 py-1 text-[11px]">
            <span>
              {L.label} · {L.shares.toFixed(2)} sh @ {L.price}¢ · {(L.weight * 100).toFixed(0)}% wt
            </span>
            <span className="font-mono">
              {fmtUsd(L.cost)} → <b className={pnlColor(L.pl)}>{fmtUsd(L.pl, { signed: true })}</b>
            </span>
          </div>
        ))}
      </div>
      <div className={`rounded p-2 text-[11px] ${(res.minReturn ?? 0) > 0 ? "bg-good/10 text-good" : "bg-warn/10 text-warn"}`}>
        {(res.minReturn ?? 0) > 0
          ? "Every covered outcome pays — just unevenly, sized by conviction."
          : "At least one covered band returns negative: its price is at or above your read there. Uneven-positive only works where your edge holds on every covered band."}
      </div>
    </div>
  );
}

function Seg({
  label, value, onChange, options, subtle = false,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  options: Array<{ v: string; label: string; title?: string }>;
  subtle?: boolean;
}) {
  return (
    <div>
      <div className="mb-1 text-[10px] uppercase tracking-wide text-muted">{label}</div>
      <div className="flex overflow-hidden rounded border border-border">
        {options.map((o) => (
          <button
            key={o.v}
            title={o.title}
            onClick={() => onChange(o.v)}
            className={`px-2.5 py-1 text-xs ${
              value === o.v
                ? subtle ? "bg-panel2 text-text" : "bg-accent text-white"
                : "bg-panel text-muted hover:text-text"
            }`}
          >
            {o.label}
          </button>
        ))}
      </div>
    </div>
  );
}

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <label className="block" title={hint}>
      <div className="mb-1 text-[10px] uppercase tracking-wide text-muted">{label}</div>
      {children}
    </label>
  );
}

function Kv({ k, v, cls }: { k: string; v: string; cls?: string }) {
  return (
    <div className="flex justify-between gap-3 text-xs">
      <span className="text-muted">{k}</span>
      <span className={`font-mono ${cls ?? ""}`}>{v}</span>
    </div>
  );
}

function Row({ k, v, cls }: { k: string; v: string; cls?: string }) {
  return (
    <div className="flex justify-between text-[10px]">
      <span className="text-muted">{k}</span>
      <span className={cls ?? ""}>{v}</span>
    </div>
  );
}
