"use client";

import { useMemo, useState } from "react";
import { supabase } from "@/lib/supabase";
import { useQuery } from "@/lib/useQuery";
import { DataState } from "@/components/DataState";
import { FreshnessRow } from "@/components/Provenance";
import DataBank from "@/components/DataBank";
import { fmtAge, fmtInt } from "@/lib/format";

/**
 * The archive, and what has been built out of it.
 *
 * WHY THIS IS A PAGE AND NOT A PANEL. The desk's one genuine asset is its own
 * record: station observations, the forecasts made against them, the books
 * that were quoted, and the frozen outcome of each. That lived in a small
 * block at the bottom of Analytics covering three fact tables, so "how much
 * do we actually have, and is any of it current" was a question answered by
 * typing count(*) into a SQL editor.
 *
 * TWO LAYERS, KEPT APART. What came in from outside, and what the desk
 * computed from it. The distinction is not cosmetic: a thin COLLECTED layer
 * means everything below it is decoration, while a stale SYNTHESISED layer is
 * a job that has not run - a different problem with a different fix, and each
 * row names the job.
 *
 * Reads sql/ad4_35_databank_inventory.sql and sql/ad4_18_databank.sql.
 */

interface Inv {
  dataset: string; ord: number; feeder: string; rows: number; cities: number;
  first_at: string | null; last_at: string | null; days_covered: number | null;
  rows_per_day: number | null; hours_since: number | null; freshness: string;
}
interface Syn {
  layer: string; ord: number; what: string; builder: string; rows: number;
  last_at: string | null; installed: boolean; hours_since: number | null; freshness: string;
}
interface ByCity {
  city_key: string; display_name: string | null; status: string;
  observations: number; obs_first_at: string | null; obs_last_at: string | null;
  obs_days: number; obs_age_h: number | null; readings_per_day: number | null;
  forecasts: number; forecasts_forward: number; forecast_last_at: string | null;
  book_snapshots: number; book_last_at: string | null; verdict: string;
}
interface Daily { dataset: string; day: string; rows: number; cities: number }

const FRESH_TONE: Record<string, string> = {
  CURRENT: "text-good",
  LAGGING: "text-warn",
  AGEING: "text-warn",
  STALE: "text-bad",
  EMPTY: "text-bad",
  "NEVER BUILT": "text-bad",
  "NOT INSTALLED": "text-muted",
  "NO TIMESTAMP": "text-muted",
  "BUILT, NO TIMESTAMP": "text-muted",
};

export default function DataBankPage() {
  const invQ = useQuery<Inv[]>(() => supabase.from("v_archive_inventory").select("*"), [], 300000);
  const synQ = useQuery<Syn[]>(() => supabase.from("v_synthesis_inventory").select("*"), [], 300000);
  const cityQ = useQuery<ByCity[]>(() => supabase.from("v_archive_by_city").select("*"), [], 300000);
  const dayQ = useQuery<Daily[]>(() => supabase.from("v_archive_daily").select("*"), [], 300000);
  const [onlyProblems, setOnlyProblems] = useState(true);

  const totalRows = (invQ.data ?? []).reduce((s, r) => s + Number(r.rows ?? 0), 0);
  const cityRows = cityQ.data ?? [];
  const problems = cityRows.filter((c) => c.verdict !== "complete" && c.status === "active");
  const shownCities = onlyProblems ? problems : cityRows;

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-lg font-semibold">Data Bank</h1>
        <p className="mt-1 max-w-3xl text-xs leading-relaxed text-muted">
          Everything the desk has collected, and everything it has computed from it.{" "}
          <b className="text-text">{fmtInt(totalRows)}</b> rows of primary record. The two layers are
          kept apart on purpose: a thin collection makes everything below it decoration, while a
          stale derived layer is a job that has not run — a different problem with a different fix.
        </p>
        {/* WHAT THIS PAGE STANDS ON. A thin page and an unfed page look
            identical, and only one of them is worth investigating. */}
        <div className="mt-2">
          <FreshnessRow relations={["bands", "book_snapshots", "markets", "trades_observed", "weather_events", "weather_forecasts", "weather_observations", "cities"]} />
        </div>
      </div>

      {/* ---------------------------------------------------- collected ---- */}
      <section>
        <div className="flex items-baseline gap-2">
          <span className="text-[10px] font-semibold text-accent">1</span>
          <h2 className="text-base font-semibold">Collected</h2>
        </div>
        <p className="mt-0.5 max-w-3xl text-xs leading-relaxed text-muted">
          What came in from outside. Every row here was written by a job, and every job is named —
          because &ldquo;0 rows&rdquo; is only useful next to what to run about it.
        </p>
        <DataState
          relation="v_archive_inventory"
          loading={invQ.loading} error={invQ.error} isEmpty={(invQ.data ?? []).length === 0}
          emptyTitle="No inventory"
          emptyBody={<>Run <code className="rounded bg-panel2 px-1">sql/ad4_35_databank_inventory.sql</code>.</>}
          onRetry={invQ.refresh}
        >
          <div className="mt-2 overflow-x-auto rounded border border-border">
            <table className="w-full text-xs">
              <thead className="bg-panel2 text-muted">
                <tr>
                  <th className="px-2 py-1.5 text-left">Dataset</th>
                  <th className="px-2 py-1.5 text-right">Rows</th>
                  <th className="px-2 py-1.5 text-right">Cities</th>
                  <th className="px-2 py-1.5 text-right" title="Calendar days between the first and last row. Not the number of days actually covered - a gap in the middle is invisible here and visible in the chart below.">
                    Span
                  </th>
                  <th className="px-2 py-1.5 text-right">Rows/day</th>
                  <th className="px-2 py-1.5 text-left">Newest</th>
                  <th className="px-2 py-1.5 text-left">Filled by</th>
                </tr>
              </thead>
              <tbody>
                {(invQ.data ?? []).map((r) => (
                  <tr key={r.dataset} className="border-t border-border">
                    <td className="px-2 py-1.5">
                      <div>{r.dataset}</div>
                      <div className={`text-[10px] ${FRESH_TONE[r.freshness] ?? "text-muted"}`}>{r.freshness}</div>
                    </td>
                    <td className="px-2 py-1.5 text-right font-mono tabular-nums">{fmtInt(r.rows)}</td>
                    <td className="px-2 py-1.5 text-right font-mono tabular-nums">{r.cities || "—"}</td>
                    <td className="px-2 py-1.5 text-right font-mono tabular-nums text-muted">
                      {r.days_covered ? `${r.days_covered}d` : "—"}
                    </td>
                    <td className="px-2 py-1.5 text-right font-mono tabular-nums text-muted">
                      {r.rows_per_day ?? "—"}
                    </td>
                    <td className="px-2 py-1.5 text-muted">{r.last_at ? fmtAge(r.last_at) : "—"}</td>
                    <td className="px-2 py-1.5 text-[10px] text-muted">{r.feeder}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </DataState>

        {/* ---- the shape of the collection over time --------------------- */}
        <ArchiveChart rows={dayQ.data ?? []} loading={dayQ.loading} error={dayQ.error} onRetry={dayQ.refresh} />
      </section>

      {/* -------------------------------------------------- synthesised ---- */}
      <section className="border-t border-border pt-5">
        <div className="flex items-baseline gap-2">
          <span className="text-[10px] font-semibold text-accent">2</span>
          <h2 className="text-base font-semibold">Synthesised</h2>
        </div>
        <p className="mt-0.5 max-w-3xl text-xs leading-relaxed text-muted">
          What the desk built out of the archive. All of it is derived, so nothing here is lost when
          it goes stale — it is recomputed by the job named on its row.
        </p>
        <DataState
          relation="v_synthesis_inventory"
          loading={synQ.loading} error={synQ.error} isEmpty={(synQ.data ?? []).length === 0}
          emptyTitle="No derived layers"
          emptyBody={<>Run <code className="rounded bg-panel2 px-1">sql/ad4_35_databank_inventory.sql</code>.</>}
          onRetry={synQ.refresh}
        >
          <div className="mt-2 grid gap-2 md:grid-cols-2">
            {(synQ.data ?? []).map((r) => (
              <div key={r.layer} className="rounded border border-border bg-panel p-2.5">
                <div className="flex items-baseline gap-2">
                  <span className="text-xs font-semibold">{r.layer}</span>
                  <span className="font-mono text-xs tabular-nums text-muted">{fmtInt(r.rows)}</span>
                  <span className={`ml-auto text-[10px] ${FRESH_TONE[r.freshness] ?? "text-muted"}`}>
                    {r.freshness}
                    {r.last_at && r.freshness !== "NOT INSTALLED" ? ` · ${fmtAge(r.last_at)}` : ""}
                  </span>
                </div>
                <p className="mt-0.5 text-[11px] leading-relaxed text-muted">{r.what}</p>
                <p className="mt-1 text-[10px] text-muted">
                  <span className="text-muted">built by </span>
                  <code>{r.builder}</code>
                </p>
              </div>
            ))}
          </div>
        </DataState>
      </section>

      {/* ------------------------------------------------------ per city ---- */}
      <section className="border-t border-border pt-5">
        <div className="flex flex-wrap items-baseline gap-2">
          <span className="text-[10px] font-semibold text-accent">3</span>
          <h2 className="text-base font-semibold">Per city</h2>
          <span className="text-xs text-muted">
            {problems.length === 0
              ? "every active city is complete"
              : `${problems.length} of ${cityRows.filter((c) => c.status === "active").length} active cities are missing something`}
          </span>
          <label className="ml-auto flex items-center gap-1.5 text-[11px] text-muted">
            <input type="checkbox" checked={onlyProblems} onChange={(e) => setOnlyProblems(e.target.checked)} />
            only the incomplete ones
          </label>
        </div>
        <p className="mt-0.5 max-w-3xl text-xs leading-relaxed text-muted">
          A total hides the thing that decides whether a city is tradeable at all. A city with no
          forward forecast has no price to disagree with; a city observed under twelve times a day
          has an understated daily maximum, and every feature built on it inherits that.
        </p>
        <DataState
          relation="v_archive_by_city"
          loading={cityQ.loading} error={cityQ.error} isEmpty={shownCities.length === 0}
          emptyTitle={onlyProblems ? "Nothing incomplete" : "No cities"}
          emptyBody={
            onlyProblems ? (
              <>Every active city has observations, a forward forecast and a book. Untick the filter to see them all.</>
            ) : (
              <>Run <code className="rounded bg-panel2 px-1">sql/ad4_00_preflight.sql</code> to seed cities.</>
            )
          }
          onRetry={cityQ.refresh}
        >
          <div className="mt-2 overflow-x-auto rounded border border-border">
            <table className="w-full text-xs">
              <thead className="bg-panel2 text-muted">
                <tr>
                  <th className="px-2 py-1.5 text-left">City</th>
                  <th className="px-2 py-1.5 text-right">Observations</th>
                  <th className="px-2 py-1.5 text-right" title="Readings per observed day. Under about 12 the day's maximum is understated.">
                    Per day
                  </th>
                  <th className="px-2 py-1.5 text-right">Days</th>
                  <th className="px-2 py-1.5 text-right">Newest</th>
                  <th className="px-2 py-1.5 text-right" title="Forecasts for today or later. Zero means nothing to price a market against.">
                    Forward fc
                  </th>
                  <th className="px-2 py-1.5 text-right">Books</th>
                  <th className="px-2 py-1.5 text-left">Verdict</th>
                </tr>
              </thead>
              <tbody>
                {shownCities.map((c) => (
                  <tr key={c.city_key} className="border-t border-border">
                    <td className="px-2 py-1.5">
                      {c.display_name ?? c.city_key}
                      {c.status !== "active" && <span className="ml-1 text-[10px] text-muted">{c.status}</span>}
                    </td>
                    <td className="px-2 py-1.5 text-right font-mono tabular-nums">{fmtInt(c.observations)}</td>
                    <td className={`px-2 py-1.5 text-right font-mono tabular-nums ${
                      c.readings_per_day != null && c.readings_per_day < 12 ? "text-warn" : "text-muted"
                    }`}>
                      {c.readings_per_day ?? "—"}
                    </td>
                    <td className="px-2 py-1.5 text-right font-mono tabular-nums text-muted">{c.obs_days || "—"}</td>
                    <td className={`px-2 py-1.5 text-right ${
                      c.obs_age_h != null && c.obs_age_h > 24 ? "text-warn" : "text-muted"
                    }`}>
                      {c.obs_last_at ? fmtAge(c.obs_last_at) : "never"}
                    </td>
                    <td className={`px-2 py-1.5 text-right font-mono tabular-nums ${
                      c.forecasts_forward === 0 ? "text-bad" : "text-muted"
                    }`}>
                      {c.forecasts_forward}
                    </td>
                    <td className="px-2 py-1.5 text-right font-mono tabular-nums text-muted">{fmtInt(c.book_snapshots)}</td>
                    <td className={`px-2 py-1.5 text-[11px] ${c.verdict === "complete" ? "text-good" : "text-warn"}`}>
                      {c.verdict}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </DataState>
      </section>

      {/* ------------------------------------------------- is it any good --- */}
      <section className="border-t border-border pt-5">
        <div className="flex items-baseline gap-2">
          <span className="text-[10px] font-semibold text-accent">4</span>
          <h2 className="text-base font-semibold">Is any of it true</h2>
        </div>
        <p className="mt-0.5 max-w-3xl text-xs leading-relaxed text-muted">
          The frozen record, asked the only two questions that matter: does a band priced at 30%
          settle 30% of the time, and did a claimed edge ever show up in money.
        </p>
        <div className="mt-2">
          <DataBank />
        </div>
      </section>
    </div>
  );
}

/**
 * Rows per day, per dataset. The SHAPE is the point: a flat stretch is a job
 * that stopped, not a quiet week, and no total can show that.
 */
function ArchiveChart({
  rows, loading, error, onRetry,
}: { rows: Daily[]; loading: boolean; error: string | null; onRetry: () => void }) {
  const series = useMemo(() => {
    const byDataset = new Map<string, Map<string, number>>();
    const days = new Set<string>();
    for (const r of rows) {
      days.add(r.day);
      const m = byDataset.get(r.dataset) ?? new Map<string, number>();
      m.set(r.day, Number(r.rows));
      byDataset.set(r.dataset, m);
    }
    const dayList = Array.from(days).sort();
    return { dayList, byDataset };
  }, [rows]);

  if (loading || error || series.dayList.length === 0) {
    return (
      <DataState
        loading={loading} error={error} isEmpty
        compact
        emptyTitle="Nothing collected in the last 90 days"
        emptyBody={<>Every feed is quiet. Start with n8n <b>P1.5</b> (every city) and <b>P0.3</b> (books).</>}
        onRetry={onRetry}
      >
        <span />
      </DataState>
    );
  }

  const W = 900, ROW = 34, PAD_L = 130, PAD_R = 12;
  const datasets = Array.from(series.byDataset.keys());
  const H = datasets.length * ROW + 22;
  const n = series.dayList.length;
  const cw = (W - PAD_L - PAD_R) / n;

  return (
    <div className="mt-3 overflow-x-auto rounded border border-border bg-panel p-2">
      <div className="mb-1 text-[10px] uppercase tracking-wide text-muted">
        Rows per day, last 90 days — a gap is an outage, not a quiet week
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" style={{ minWidth: 620 }}>
        {datasets.map((d, di) => {
          const m = series.byDataset.get(d)!;
          const max = Math.max(...Array.from(m.values()), 1);
          return (
            <g key={d} transform={`translate(0, ${di * ROW})`}>
              <text x={PAD_L - 8} y={ROW / 2 + 4} textAnchor="end" className="fill-muted" fontSize={10}>
                {d}
              </text>
              {series.dayList.map((day, i) => {
                const v = m.get(day) ?? 0;
                const h = v === 0 ? 2 : Math.max(2, (v / max) * (ROW - 10));
                return (
                  <rect
                    key={day}
                    x={PAD_L + i * cw}
                    y={ROW - 5 - h}
                    width={Math.max(1, cw - 0.7)}
                    height={h}
                    className={v === 0 ? "fill-bad/40" : "fill-accent/70"}
                  >
                    <title>{`${d} · ${day} · ${v.toLocaleString()} rows`}</title>
                  </rect>
                );
              })}
              <line x1={PAD_L} x2={W - PAD_R} y1={ROW - 3} y2={ROW - 3} className="stroke-border" strokeWidth={0.5} />
            </g>
          );
        })}
        <text x={PAD_L} y={H - 4} className="fill-muted" fontSize={9}>{series.dayList[0]}</text>
        <text x={W - PAD_R} y={H - 4} textAnchor="end" className="fill-muted" fontSize={9}>
          {series.dayList[n - 1]}
        </text>
      </svg>
    </div>
  );
}
