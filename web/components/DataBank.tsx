"use client";

import { useMemo } from "react";
import { supabase } from "@/lib/supabase";
import { useQuery } from "@/lib/useQuery";
import { Empty } from "@/components/charts";
import { fmtCompactUsd, fmtInt, fmtPct } from "@/lib/format";

/**
 * The desk's own record: how much of it exists, whether the model's stated
 * probabilities are true, and whether a claimed edge ever showed up in money.
 *
 * These three read from the fact tables in sql/ad4_18_databank.sql. Before
 * that file is run - and before scripts/databank.py has banked a few hundred
 * settled bands - they say so plainly rather than drawing a curve through
 * nothing. A calibration plot from forty outcomes is worse than no plot: it
 * looks like evidence.
 */

interface Coverage { dataset: string; rows: number; since: string | null; latest: string | null; cities: number }
interface CalRow { bucket: number; predicted_mid: number; n: number; predicted: number; observed: number; gap: number; model_mae: number | null; market_mae: number | null }
interface EdgeRow { edge_bucket: string; ord: number; n: number; claimed_pp: number; realised_pp: number; hit_rate: number; avg_price: number; volume_usd: number }
interface OutcomeHealth {
  raw_band_facts: number; verified_band_facts: number; mismatched_band_facts: number;
  raw_forecast_facts: number; verified_forecast_facts: number;
  venue_evidence_rows: number; weather_evidence_rows: number; confirmed_markets: number;
}

const MISSING = /does not exist|could not find|schema cache/i;
const MIN_FOR_A_CURVE = 300;

export default function DataBank() {
  const covQ = useQuery<Coverage[]>(() => supabase.from("v_databank_coverage").select("*"), [], 300000);
  const calQ = useQuery<CalRow[]>(() => supabase.from("v_calibration").select("*"), [], 300000);
  const edgeQ = useQuery<EdgeRow[]>(() => supabase.from("v_edge_realisation").select("*"), [], 300000);
  const evidenceQ = useQuery<OutcomeHealth[]>(
    () => supabase.from("v_outcome_evidence_health").select("*"), [], 300000
  );

  const notInstalled = MISSING.test(covQ.error ?? "");
  const total = (covQ.data ?? []).reduce((s, r) => s + Number(r.rows ?? 0), 0);
  const evidence = evidenceQ.data?.[0];
  const rawBanded = Number(evidence?.raw_band_facts ??
    (covQ.data ?? []).find((r) => r.dataset === "band outcomes")?.rows ?? 0);
  const banded = Number(evidence?.verified_band_facts ?? 0);
  const cal = (calQ.data ?? []).filter((r) => r.n >= 10);

  // Is the model beating the market on the bands we have settled? The only
  // question that decides whether any of this is a business.
  const verdict = useMemo(() => {
    const rows = (calQ.data ?? []).filter((r) => r.model_mae !== null && r.market_mae !== null);
    if (!rows.length) return null;
    const w = rows.reduce((s, r) => s + r.n, 0);
    const model = rows.reduce((s, r) => s + (r.model_mae as number) * r.n, 0) / w;
    const market = rows.reduce((s, r) => s + (r.market_mae as number) * r.n, 0) / w;
    return { model, market, n: w };
  }, [calQ.data]);

  if (notInstalled) {
    return (
      <div className="rounded border border-warn/40 bg-warn/10 px-3 py-2 text-xs leading-relaxed text-warn">
        <b>The data bank is not installed.</b> Run <code>sql/ad4_18_databank.sql</code>, then let{" "}
        <b>GitHub Actions → Data Bank</b> run once a day. It freezes what the desk predicted against
        what actually happened — the only asset here nobody else has, and until it exists this desk
        cannot answer whether a band it prices at 30% settles 30% of the time.
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* ---- how much evidence exists ---------------------------------- */}
      <div className="grid gap-2 sm:grid-cols-3">
        {(covQ.data ?? []).map((c) => (
          <div key={c.dataset} className="rounded border border-border bg-panel p-3">
            <div className="text-[10px] uppercase tracking-wide text-muted">{c.dataset}</div>
            <div className="font-mono text-lg">{fmtInt(Number(c.rows))}</div>
            <div className="text-[10px] text-muted">
              {Number(c.rows) > 0 ? `${c.since} → ${c.latest} · ${c.cities} cities` : "nothing banked yet"}
            </div>
          </div>
        ))}
      </div>

      {evidence && (evidence.raw_band_facts > evidence.verified_band_facts ||
                    evidence.raw_forecast_facts > evidence.verified_forecast_facts) && (
        <div className="rounded border border-warn/40 bg-warn/10 px-3 py-2 text-xs leading-relaxed text-warn">
          <b>Historical evidence is preserved, but not all of it is verified.</b>{" "}
          {fmtInt(evidence.verified_band_facts)} of {fmtInt(evidence.raw_band_facts)} band outcomes
          have a complete Gamma+CLOB-confirmed ladder; {fmtInt(evidence.verified_forecast_facts)} of{" "}
          {fmtInt(evidence.raw_forecast_facts)} forecast outcomes have final station-authority evidence.
          Unverified rows remain in the proprietary archive and exports, but are excluded from model
          calibration, edge-realisation claims, and predictive accuracy.
          {evidence.mismatched_band_facts > 0 ? (
            <> {fmtInt(evidence.mismatched_band_facts)} band rows disagree with venue evidence and require review.</>
          ) : null}
        </div>
      )}

      {total === 0 && (
        <div className="rounded border border-dashed border-border p-4 text-center text-xs leading-relaxed text-muted">
          <b className="text-text">Nothing banked yet.</b> The tables exist; the collector has not run.
          <b> GitHub Actions → Data Bank</b> freezes only outcomes backed by final venue or
          station-authority evidence. Running maxima stay provisional.
        </div>
      )}

      {/* ---- is the model telling the truth? ---------------------------- */}
      {rawBanded > 0 && (
        <div className="rounded border border-border bg-panel p-3">
          <div className="mb-1 flex flex-wrap items-baseline justify-between gap-2">
            <h3 className="text-sm font-semibold">Calibration — do the probabilities mean what they say?</h3>
            <span className="font-mono text-[10px] text-muted">{fmtInt(banded)} venue-verified bands</span>
          </div>
          <p className="mb-2 max-w-3xl text-[11px] leading-relaxed text-muted">
            Of every band priced near <b>p</b>, how many settled yes? On the diagonal the model is
            honest. <b>Above it the model is underconfident</b> — those bands happen more often than
            it says. Below, it is overconfident. The shape matters more than any single point: a
            model whose low buckets sit high and whose high buckets sit low is systematically too
            sure of itself, and <code>scripts/calibration.py</code> corrects exactly that.
          </p>
          {banded < MIN_FOR_A_CURVE ? (
            <Empty height={120}>
              {fmtInt(banded)} venue-verified bands. The fitter needs {MIN_FOR_A_CURVE} before it will write
              a correction — a calibration map from this little data looks like knowledge and is not.
            </Empty>
          ) : cal.length === 0 ? (
            <Empty height={120}>No bucket has ten outcomes in it yet.</Empty>
          ) : (
            <CalibrationPlot rows={cal} />
          )}

          {verdict && (
            <div className={`mt-2 rounded px-2 py-1.5 text-[11px] leading-relaxed ${
              verdict.model < verdict.market ? "bg-good/10 text-good" : "bg-warn/10 text-warn"}`}>
              {verdict.model < verdict.market ? (
                <>
                  <b>The model is beating the market</b> on the {fmtInt(verdict.n)} bands settled so
                  far — mean absolute error {verdict.model.toFixed(3)} against the book&apos;s{" "}
                  {verdict.market.toFixed(3)}. That gap is the edge, measured rather than claimed.
                </>
              ) : (
                <>
                  <b>The market is currently better calibrated than the model</b> —{" "}
                  {verdict.market.toFixed(3)} against {verdict.model.toFixed(3)} over{" "}
                  {fmtInt(verdict.n)} settled bands. An edge computed from these probabilities is not
                  yet evidence of one. This is the number to move before sizing up.
                </>
              )}
            </div>
          )}
        </div>
      )}

      {/* ---- did a claimed edge ever pay? ------------------------------- */}
      {(edgeQ.data ?? []).length > 0 && (
        <div className="rounded border border-border bg-panel p-3">
          <h3 className="text-sm font-semibold">Edge realisation — did the claimed edge pay?</h3>
          <p className="mb-2 max-w-3xl text-[11px] leading-relaxed text-muted">
            Bands grouped by the edge claimed when they were priced, against what buying them at the
            market would actually have returned. <b>Claimed and realised should track each other.</b>{" "}
            A desk whose claimed edge does not appear in the realised column has an edge in a
            spreadsheet and nowhere else.
          </p>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead className="bg-panel2 text-[10px] uppercase tracking-wide text-muted">
                <tr>
                  <th className="p-1.5 text-left">Edge claimed</th>
                  <th className="p-1.5 text-right">Bands</th>
                  <th className="p-1.5 text-right">Claimed</th>
                  <th className="p-1.5 text-right">Realised</th>
                  <th className="p-1.5 text-right">Hit rate</th>
                  <th className="p-1.5 text-right">Avg price</th>
                </tr>
              </thead>
              <tbody>
                {(edgeQ.data ?? []).map((r) => {
                  const thin = r.n < 30;
                  return (
                    <tr key={r.edge_bucket} className="border-t border-border">
                      <td className="p-1.5 font-mono">{r.edge_bucket}</td>
                      <td className={`p-1.5 text-right font-mono ${thin ? "text-warn" : "text-muted"}`}>
                        {fmtInt(r.n)}{thin ? " ⚠" : ""}
                      </td>
                      <td className="p-1.5 text-right font-mono">{r.claimed_pp > 0 ? "+" : ""}{r.claimed_pp}pp</td>
                      <td className={`p-1.5 text-right font-mono ${r.realised_pp > 0 ? "text-good" : "text-bad"}`}>
                        {r.realised_pp > 0 ? "+" : ""}{r.realised_pp}pp
                      </td>
                      <td className="p-1.5 text-right font-mono text-muted">{fmtPct(r.hit_rate, 1)}</td>
                      <td className="p-1.5 text-right font-mono text-muted">{Math.round(r.avg_price * 100)}¢</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <p className="mt-1 text-[10px] text-muted">
            ⚠ marks a bucket with fewer than 30 bands — too few for its realised figure to mean
            anything yet.
          </p>
        </div>
      )}
    </div>
  );
}

function CalibrationPlot({ rows }: { rows: CalRow[] }) {
  const W = 720, H = 300, PAD = 44;
  const X = (p: number) => PAD + p * (W - PAD - 16);
  const Y = (p: number) => H - PAD - p * (H - PAD - 16);
  const maxN = Math.max(...rows.map((r) => r.n), 1);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full" style={{ maxHeight: 420 }}>
      {[0, 0.25, 0.5, 0.75, 1].map((v) => (
        <g key={v}>
          <line x1={PAD} x2={W - 16} y1={Y(v)} y2={Y(v)} stroke="var(--chart-grid)" />
          <text x={PAD - 6} y={Y(v) + 3} textAnchor="end" fontSize="9" fill="var(--chart-axis)">{v * 100}%</text>
          <text x={X(v)} y={H - PAD + 13} textAnchor="middle" fontSize="9" fill="var(--chart-axis)">{v * 100}%</text>
        </g>
      ))}
      <line x1={X(0)} y1={Y(0)} x2={X(1)} y2={Y(1)} stroke="var(--chart-axis)" strokeDasharray="4 3" />
      <text x={X(0.68)} y={Y(0.75)} fontSize="9" fill="var(--chart-axis)">perfectly calibrated</text>
      <text x={W / 2} y={H - 4} textAnchor="middle" fontSize="9" fill="var(--chart-axis)">what the model said</text>
      <text x={10} y={18} fontSize="9" fill="var(--chart-axis)">what actually happened</text>

      <path
        d={rows.map((r, i) => `${i === 0 ? "M" : "L"}${X(r.predicted).toFixed(1)},${Y(r.observed).toFixed(1)}`).join(" ")}
        fill="none" stroke="var(--c-accent)" strokeWidth={1.6} className="chart-draw"
      />
      {rows.map((r) => (
        <g key={r.bucket} className="chart-pop">
          {/* radius carries the sample size: a point from 12 outcomes should
              not look as solid as one from 400 */}
          <circle
            cx={X(r.predicted)} cy={Y(r.observed)}
            r={3 + 5 * Math.sqrt(r.n / maxN)}
            fill={Math.abs(r.gap) < 0.05 ? "var(--c-good)" : Math.abs(r.gap) < 0.12 ? "var(--c-warn)" : "var(--c-bad)"}
            opacity={0.85}
          />
          <title>
            {`said ${(r.predicted * 100).toFixed(1)}%, happened ${(r.observed * 100).toFixed(1)}% ` +
             `(${r.gap > 0 ? "+" : ""}${(r.gap * 100).toFixed(1)}pp) over ${r.n} bands`}
          </title>
        </g>
      ))}
    </svg>
  );
}
