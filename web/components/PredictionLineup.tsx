"use client";

import { useEffect, useMemo, useState } from "react";
import { supabase } from "@/lib/supabase";
import { useQuery } from "@/lib/useQuery";
import { DataState } from "@/components/DataState";
import { MOMENT } from "@/components/PredictionHindsight";
import {
  CHECKPOINTS, FAMILY, learningStatus, lineupDates, lineupTable, num, shortVersion,
  type LineupRow, type StatusRow,
} from "@/lib/lineup";

/**
 * MAIN, S10 AND THE CHALLENGERS, APART (P2.2 part 2, 4 Oct).
 *
 * Two panels from the one prediction contract (20261004190000 and
 * 20261004200000):
 *
 *   where each predictor stands   v_learning_status - every version's latest
 *                                 registry state in the plan's four words,
 *                                 with the evidence and the rollback target
 *   who called what               v_prediction_lineup - one date and
 *                                 checkpoint, a line per city, a column per
 *                                 predictor, the venue's winner once settled
 *
 * A blinded version (under a pre-registered test, before its first look) shows
 * its calls for today only and is never graded here: a past call beside the
 * winner would be its score read early. The lib (web/lib/lineup.ts) holds the
 * table logic and its tests.
 */

const STAGE_TONE: Record<string, string> = {
  serving: "text-good border-good/40",
  evaluation: "text-accent border-accent/40",
  "candidate fitting": "text-warn border-warn/40",
  "data capture": "text-muted border-border",
  retired: "text-muted border-border",
};

const pct = (v: unknown) => {
  const x = num(v);
  return x === null ? "—" : `${Math.round(x * 100)}%`;
};

const rate = (hits: number, n: number) => (n ? `${hits}/${n} (${Math.round((hits / n) * 100)}%)` : "—");

export default function PredictionLineup() {
  const [dates, setDates] = useState<string[]>([]);
  const [date, setDate] = useState("");
  const [checkpoint, setCheckpoint] = useState("noon");

  // The dates follow the database's clock (UTC) and are set after mount, so the
  // prerendered page and the browser agree.
  useEffect(() => {
    const d = lineupDates(new Date());
    setDates(d);
    setDate(d[1]);
  }, []);

  const sq = useQuery<StatusRow[]>(
    () => supabase.from("v_learning_status").select("*").limit(200),
    [], 300000, 200);
  const lq = useQuery<LineupRow[]>(
    () => date
      ? supabase.from("v_prediction_lineup").select("*")
          .eq("target_date", date).eq("checkpoint", checkpoint)
          .order("city_key").order("model_family").order("version")
          .limit(1000)
      : Promise.resolve({ data: [] as LineupRow[], error: null }),
    [date, checkpoint], 120000, 1000);

  const status = useMemo(() => learningStatus(sq.data ?? []), [sq.data]);
  const table = useMemo(() => lineupTable(lq.data ?? [], sq.data ?? []), [lq.data, sq.data]);
  const blindCols = table.columns.filter((c) => c.blind);

  return <section className="space-y-3">
    <div>
      <h2 className="text-sm font-semibold">Where each predictor stands</h2>
      <p className="max-w-3xl text-xs leading-relaxed text-muted">
        Every predictor version and its latest recorded state: <strong>serving</strong> prices the
        desk, <strong>evaluation</strong> is recorded beside it and scored forward,{" "}
        <strong>candidate fitting</strong> has a fit that applies to nothing yet, and{" "}
        <strong>data capture</strong> is recording inputs only. A version moves only by a new event
        naming its evidence; the nightly station-correction and MOS refits serve automatically,
        bounded by Rule 11 (Hassan, 4 Oct).
      </p>
    </div>
    <DataState
      relation="v_learning_status"
      truncated={sq.truncated}
      loading={sq.loading} error={sq.error} isEmpty={!status.length}
      emptyTitle="No predictor is registered"
      emptyBody={<>The registry (<code className="rounded bg-panel2 px-1">model_registry</code>) is
        seeded by 20261004190000_one_prediction_contract.sql.</>}
      onRetry={sq.refresh}
    >
      <div className="overflow-x-auto rounded border border-border">
        <table className="w-full text-xs">
          <thead className="bg-panel2 text-muted"><tr>
            {["Stage", "Predictor", "Version", "Horizon", "Evidence", "Decided by", "Recorded"]
              .map((h) => <th key={h} className="px-2 py-1.5 text-left font-normal">{h}</th>)}
          </tr></thead>
          <tbody>
            {status.map((s) => <tr key={`${s.family}|${s.version}|${s.horizon}`} className="border-t border-border align-top">
              <td className="whitespace-nowrap px-2 py-1">
                <span className={`rounded border px-1.5 py-0.5 ${STAGE_TONE[s.stage] ?? "text-muted border-border"}`}
                      title={`registry state: ${s.state}`}>
                  {s.stage}
                </span>
                {s.blind && <span className="ml-1 rounded border border-border px-1.5 py-0.5 text-muted"
                  title="Under a pre-registered test: no score is shown before its first look.">blinded</span>}
              </td>
              <td className="whitespace-nowrap px-2 py-1">{FAMILY[s.family] ?? s.family}</td>
              <td className="px-2 py-1 font-mono" title={s.version}>{s.version}</td>
              <td className="whitespace-nowrap px-2 py-1 text-muted">{s.horizon}</td>
              <td className="max-w-md px-2 py-1 text-muted"
                  title={[s.note, s.rollback_to ? `rollback: ${s.rollback_to}` : null].filter(Boolean).join(" - ")}>
                {s.evidence}
                {s.rollback_to && <span className="block">rollback: <span className="font-mono">{shortVersion(s.rollback_to)}</span></span>}
              </td>
              <td className="px-2 py-1 text-muted">{s.decided_by}</td>
              <td className="whitespace-nowrap px-2 py-1 font-mono text-muted"
                  title="when this state was written to the registry, not when the version began">
                {s.decided_at.slice(0, 10)}
              </td>
            </tr>)}
          </tbody>
        </table>
      </div>
    </DataState>

    <div className="pt-2">
      <h2 className="text-sm font-semibold">Who called what</h2>
      <p className="max-w-3xl text-xs leading-relaxed text-muted">
        The served engine, S10 and the engine variants at the same checkpoint, a line per city: each
        cell is the predictor&apos;s top bucket and the probability it gave it, green when the venue
        paid that bucket. The engine counts once per checkpoint, its first capture, as in the record
        above. A <strong>blinded</strong> version is under a pre-registered test and shows its calls
        for today only, never graded: a past call beside the winner would be its score read before
        the first look.
      </p>
    </div>
    <div className="flex flex-wrap items-center gap-2 text-xs">
      <span className="text-muted">Target date (UTC)</span>
      {dates.map((d) =>
        <button key={d} onClick={() => setDate(d)}
          className={`rounded border px-2 py-0.5 font-mono ${date === d ? "border-accent text-accent" : "border-border text-muted"}`}>
          {d.slice(5)}
        </button>)}
    </div>
    <div className="flex flex-wrap items-center gap-2 text-xs">
      <span className="text-muted">Checkpoint</span>
      {CHECKPOINTS.map((k) =>
        <button key={k} onClick={() => setCheckpoint(k)}
          className={`rounded border px-2 py-0.5 ${checkpoint === k ? "border-accent text-accent" : "border-border text-muted"}`}>
          {MOMENT[k] ?? k}
        </button>)}
    </div>
    <DataState
      relation="v_prediction_lineup"
      truncated={lq.truncated}
      loading={lq.loading || !date} error={lq.error} isEmpty={!table.lines.length}
      emptyTitle="No calls at this checkpoint on this date"
      emptyBody={<>The evening-before checkpoint is the engine&apos;s alone, and S10 and the variants
        record same-day checkpoints only; a city appears once its checkpoint has passed.</>}
      onRetry={lq.refresh}
    >
      <div className="overflow-x-auto rounded border border-border">
        <table className="w-full text-xs">
          <thead className="bg-panel2 text-muted"><tr>
            <th className="px-2 py-1.5 text-left font-normal">City</th>
            <th className="px-2 py-1.5 text-left font-normal">Venue paid</th>
            {table.columns.map((c) =>
              <th key={c.key} className="px-2 py-1.5 text-left font-normal"
                  title={[c.version ?? "the version is recorded on each call", c.state ? `registry: ${c.state}` : null,
                          c.blind ? "blinded until its first look" : null].filter(Boolean).join(" - ")}>
                {c.title}
                {c.state === "retired" && <span className="ml-1 text-muted">(retired)</span>}
                {c.blind && <span className="ml-1 text-muted">(blinded)</span>}
              </th>)}
          </tr></thead>
          <tbody>
            {table.lines.map((line) => <tr key={line.city_key} className="border-t border-border">
              <td className="whitespace-nowrap px-2 py-1">{line.city_key}</td>
              <td className="whitespace-nowrap px-2 py-1">{line.winner_label ?? <span className="text-muted">not settled</span>}</td>
              {table.columns.map((c) => {
                const cell = line.cells[c.key];
                if (!cell) return <td key={c.key} className="px-2 py-1 text-muted">—</td>;
                if (cell.withheld) {
                  return <td key={c.key} className="px-2 py-1 text-muted"
                             title="blinded: a past call is not shown before the first look">blinded</td>;
                }
                const tone = cell.hit === null ? "" : cell.hit ? "text-good" : "text-bad";
                return <td key={c.key} className={`whitespace-nowrap px-2 py-1 ${tone}`}
                           title={[`${cell.version}`, `as of ${cell.as_of}`,
                                   num(cell.priced_centre_c) !== null ? `centre ${num(cell.priced_centre_c)!.toFixed(2)}°C` : null,
                                   cell.station ? `station ${cell.station}` : null].filter(Boolean).join(" - ")}>
                  {cell.top_label ?? "—"} <span className="font-mono text-muted">{pct(cell.top_prob)}</span>
                </td>;
              })}
            </tr>)}
          </tbody>
          <tfoot className="bg-panel2 text-muted">
            <tr className="border-t border-border">
              <td className="px-2 py-1" colSpan={2}>Right, of the settled</td>
              {table.columns.map((c) => {
                const t = table.tallies[c.key];
                return <td key={c.key} className="whitespace-nowrap px-2 py-1 font-mono"
                           title={c.key === "engine" || c.blind ? "" : `on the ${t.common} city-days both were graded: this ${rate(t.hitsOnCommon, t.common)}, the engine ${rate(t.engineHitsOnCommon, t.common)}`}>
                  {c.blind ? "not shown" : rate(t.hits, t.graded)}
                </td>;
              })}
            </tr>
          </tfoot>
        </table>
      </div>
      <p className="text-xs text-muted">
        One date and one checkpoint is a small sample: {table.lines.length} cities. The record
        above grades every settled day.
        {blindCols.length > 0 && <> Blinded here: {blindCols.map((c) => c.title).join(", ")}; when
          each is first looked at is in its evidence, above.</>}
      </p>
    </DataState>
  </section>;
}
