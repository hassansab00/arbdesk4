"use client";

import Link from "next/link";
import { supabase } from "@/lib/supabase";
import { useQuery } from "@/lib/useQuery";
import { fmtAge } from "@/lib/format";

/**
 * What is actually running, and what each stopped job costs you.
 *
 * Every empty container on this platform has the same handful of causes, and
 * the pages could only say "this table is empty" one at a time. That leaves
 * the reader to work out that no book snapshot is why every bucket is blocked,
 * why the Overview has nothing to rank, and why the Opportunities cards have
 * nothing to draw. It is one chain, and it should be shown as one chain.
 *
 * Ordered by dependency, not alphabetically: the first broken link is the one
 * to fix, and everything below it is a symptom rather than a separate problem.
 */

interface Row { job: string; status: string | null; logged_at: string | null; rows: number | null }

const CHAIN: Array<{
  job: string;
  label: string;
  where: string;
  staleAfterH: number;
  feeds: string;
}> = [
  { job: "P0.2_market_discovery", label: "Markets & buckets", where: "n8n · P0.2",
    staleAfterH: 12, feeds: "the buckets on the Board. Missing ones mean a ladder that cannot sum to 100¢." },
  { job: "P0.3_book_volume_snapshot", label: "Order books", where: "n8n · P0.3",
    staleAfterH: 3, feeds: "every price. Without it each bucket is blocked, and Opportunities and Overview have nothing to rank." },
  { job: "observations", label: "Station observations", where: "Actions · Station Observations",
    staleAfterH: 6, feeds: "the running max, the climate baseline, hotness and the weather model." },
  { job: "live_weather", label: "Live weather", where: "Actions · Live Weather / n8n · P1.2",
    staleAfterH: 2, feeds: "the temperatures on the Board and City Watch." },
  { job: "forecasts", label: "Forecasts", where: "Actions · Forecasts / n8n · P1.3",
    staleAfterH: 12, feeds: "the centre of every probability. Nothing prices without one." },
  { job: "probabilities", label: "Probability + edge", where: "Actions · Probabilities",
    staleAfterH: 8, feeds: "model probabilities and edges — the Opportunities list itself." },
  { job: "P0.4_trade_history", label: "Trade history", where: "n8n · P0.4",
    staleAfterH: 12, feeds: "all volume figures and the thin-market flags." },
];

export default function PipelineStatus({ compact }: { compact?: boolean }) {
  const q = useQuery<Row[]>(
    () => supabase.from("ingest_log").select("job,status,logged_at,rows").order("logged_at", { ascending: false }).limit(400),
    [],
    60000
  );

  const latest = new Map<string, Row>();
  for (const r of q.data ?? []) if (!latest.has(r.job)) latest.set(r.job, r);

  const state = CHAIN.map((c) => {
    const r = latest.get(c.job);
    const ageH = r?.logged_at ? (Date.now() - new Date(r.logged_at).getTime()) / 3600_000 : null;
    return {
      ...c, row: r, ageH,
      status: !r ? "never" : ageH !== null && ageH > c.staleAfterH ? "stale" : "ok",
    };
  });

  const broken = state.filter((s) => s.status !== "ok");
  // The first broken link. Everything after it is a symptom of this one.
  const first = broken[0] ?? null;

  if (q.loading) return null;

  if (broken.length === 0) {
    return (
      <div className="rounded border border-good/40 bg-good/10 px-3 py-2 text-xs text-good">
        <b>Pipeline healthy.</b> Every feed has written recently.
      </div>
    );
  }

  return (
    <div className="rounded border border-warn/40 bg-warn/5">
      <div className="border-b border-warn/30 px-3 py-2">
        <div className="text-xs font-semibold text-warn">
          {broken.length} of {CHAIN.length} feeds {broken.length === 1 ? "is" : "are"} not running
        </div>
        {first && (
          <p className="mt-1 max-w-3xl text-[11px] leading-relaxed text-muted">
            Start with <b className="text-warn">{first.label}</b> ({first.where}). It feeds{" "}
            {first.feeds} Anything below it in this list is most likely a symptom of that rather
            than a separate fault.
          </p>
        )}
      </div>
      {!compact && (
        <ul className="divide-y divide-border text-[11px]">
          {state.map((s) => (
            <li key={s.job} className="flex flex-wrap items-baseline gap-x-2 px-3 py-1.5">
              <span
                className={`inline-block h-1.5 w-1.5 shrink-0 rounded-full ${
                  s.status === "ok" ? "bg-good" : s.status === "stale" ? "bg-warn" : "bg-bad"
                }`}
              />
              <span className="w-40 shrink-0 font-medium">{s.label}</span>
              <span className={`w-28 shrink-0 font-mono ${s.status === "ok" ? "text-muted" : "text-warn"}`}>
                {s.row ? fmtAge(s.row.logged_at) : "never run"}
              </span>
              <span className="font-mono text-[10px] text-muted">{s.where}</span>
              {s.status !== "ok" && (
                <span className="w-full pl-4 text-muted sm:w-auto sm:pl-0">— feeds {s.feeds}</span>
              )}
            </li>
          ))}
        </ul>
      )}
      <div className="border-t border-warn/30 px-3 py-1.5 text-[11px]">
        <Link href="/workflows" className="text-accent hover:underline">
          Workflows → set schedules and run any of these →
        </Link>
      </div>
    </div>
  );
}
