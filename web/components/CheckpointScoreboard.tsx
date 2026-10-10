"use client";

import { MOMENT } from "@/components/PredictionHindsight";
import { DataState } from "@/components/DataState";
import { checkpointScores, scoreboardShort, type CheckpointScore, type CheckpointScoreRow } from "@/lib/predictive";

/**
 * THROUGH THE DAY, AT EACH CHECKPOINT (R28, Hassan 10 Oct: option 1).
 *
 * The day-ahead record beside it stays as it is: one call per settled day,
 * the last pricing before local midnight. This panel adds the checkpoint
 * calls, each frozen once at a fixed local time (prediction_checkpoints,
 * graded in fact_checkpoint_outcome), read from v_checkpoint_scoreboard: the
 * hit rates "Was it right?" already shows per moment, and what no page showed
 * until now, the proper scores against the market at the same moment.
 *
 * The market's scores are only on the days its book priced every bucket the
 * engine priced; ours are shown on those same days beside it, and over every
 * day against a uniform guess. No interval is drawn: the view holds means,
 * not the paired daily differences an interval needs.
 *
 * The page reads the whole view once (294 rows on 10 Oct: 48 cities and the
 * total, six checkpoints; it grows with cities, not days) and every city's
 * panel is cut from it. Timed as anon on 10 Oct: 3,095 ms the first read,
 * 488-940 ms after.
 */

const pct = (x: number | null) => (x === null ? "—" : `${(x * 100).toFixed(1)}%`);
const dec = (x: number | null, d = 3) => (x === null ? "—" : x.toFixed(d));

function Table({ scores, label }: { scores: CheckpointScore[]; label: string }) {
  return <div className="overflow-x-auto">
    <table className="w-full text-[11px]" aria-label={label}>
      <thead className="text-muted">
        <tr className="border-b border-border">
          <th className="p-2 text-left">checkpoint</th>
          <th className="p-2 text-right">days</th>
          <th className="p-2 text-right" title="Our top bucket was the one that paid">we called it</th>
          <th className="p-2 text-right" title="The average probability we put on our top bucket">we claimed</th>
          <th className="p-2 text-right" title="The bucket the book priced highest at the same moment">market&apos;s favourite</th>
          <th className="p-2 text-right" title="On the days the book priced every bucket we priced; lower is better">Brier us / market</th>
          <th className="p-2 text-right" title="The same days; lower is better">log loss us / market</th>
          <th className="p-2 text-right" title="Every graded day; a uniform guess over the ladder beside it">Brier us / uniform</th>
        </tr>
      </thead>
      <tbody>
        {scores.map((s) => (
          <tr key={s.checkpoint} className={`border-b border-border/40 ${s.afterPeak ? "text-muted" : ""}`}>
            <td className="p-2">{MOMENT[s.checkpoint] ?? s.checkpoint}{s.afterPeak ? " (answered)" : ""}</td>
            <td className="p-2 text-right font-mono">{s.days}</td>
            <td className="p-2 text-right font-mono">{pct(s.hitRate)} <span className="text-muted">({s.hits})</span></td>
            <td className="p-2 text-right font-mono">{pct(s.claimed)}</td>
            <td className="p-2 text-right font-mono">
              {pct(s.marketRate)} <span className="text-muted">({s.marketHits} of {s.marketCalled})</span>
            </td>
            <td className="p-2 text-right font-mono"
                style={{ color: s.brierLeader === "us" ? "var(--c-good)" : s.brierLeader === "market" ? "var(--c-bad)" : undefined }}>
              {s.common ? <>{dec(s.brierCommon)} / {dec(s.brierMarket)} <span className="text-muted">on {s.common}</span></> : "no common day"}
            </td>
            <td className="p-2 text-right font-mono">
              {s.common ? `${dec(s.logLossCommon)} / ${dec(s.logLossMarket)}` : "—"}
            </td>
            <td className="p-2 text-right font-mono"
                style={{ color: s.brier !== null && s.brierUniform !== null
                  ? (s.brier < s.brierUniform ? "var(--c-good)" : "var(--c-bad)") : undefined }}>
              {dec(s.brier)} / {dec(s.brierUniform)}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  </div>;
}

export default function CheckpointScoreboard({ rows, cityKey, cityName, loading, error, truncated, onRetry }: {
  rows: CheckpointScoreRow[]; cityKey: string; cityName: string;
  loading: boolean; error: string | null; truncated: boolean; onRetry: () => void;
}) {
  const mine = checkpointScores(rows, cityKey);
  const every = checkpointScores(rows, "all");
  const short = scoreboardShort(rows);
  const first = every.map((s) => s.firstDay).filter(Boolean).sort()[0] ?? null;
  const last = every.map((s) => s.lastDay).filter(Boolean).sort().slice(-1)[0] ?? null;
  return <div className="space-y-2 rounded border border-border bg-panel p-3">
    <div className="flex flex-wrap items-baseline justify-between gap-2">
      <h3 className="text-xs font-semibold">Through the day: {cityName} at each checkpoint</h3>
      {first && <span className="text-[11px] text-muted">{first} → {last}</span>}
    </div>
    <p className="max-w-3xl text-[11px] leading-relaxed text-muted">
      The record above is one call a day, made before the day began. These are the calls frozen at
      fixed local times through the day, each written once and graded against the bucket the venue
      confirmed (the first capture of each checkpoint). Brier and log loss score the whole ladder;
      lower is better. The market is the book at the same moment: each bucket&apos;s mid, or its last
      trade inside a one-sided quote, renormalised over the buckets we priced, and scored only on
      days the book priced all of them; our scores beside it are on those same days. The Brier is
      green where we scored lower than the market and red where the market did. An hour after the
      peak the day has mostly answered itself, so that row is greyed.
    </p>
    {short.length > 0 && !loading && !error && <p className="text-[11px] text-warn">
      The city rows do not add up to the view&apos;s own total at {short.map((k) => MOMENT[k] ?? k).join(", ")}:
      a day may have been graded between the reads, or the read is short. Reload before relying on these.
    </p>}
    <DataState
      relation="v_checkpoint_scoreboard"
      truncated={truncated}
      loading={loading} error={error} isEmpty={mine.length === 0}
      emptyTitle={`No graded checkpoint for ${cityName} yet`}
      emptyBody={<>A checkpoint is graded once its day has settled and the venue has confirmed the
        winner, into <code className="rounded bg-panel2 px-1">fact_checkpoint_outcome</code>.</>}
      onRetry={onRetry}
    >
      <Table scores={mine} label={`${cityName} at each checkpoint`} />
    </DataState>
    {every.length > 0 && <details className="text-[11px]">
      <summary className="cursor-pointer text-muted">Every city, the same table</summary>
      <Table scores={every} label="Every city at each checkpoint" />
    </details>}
  </div>;
}
