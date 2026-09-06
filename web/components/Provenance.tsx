"use client";

import Link from "next/link";
import { fillersFor, FILLED_BY, tablesBehind, type Filler } from "@/lib/provenance";
import { SQL_OWNER } from "@/lib/sqlOwner";
import { useFreshness, ageWords, type FreshRow } from "@/lib/useFreshness";

/**
 * WHY IS THIS EMPTY, AND WHAT DO I DO ABOUT IT?
 *
 * Two facts answer that, and both are derived rather than written out by hand
 * in each panel, because a hand-written note is wrong the first time a job is
 * renamed:
 *
 *   web/lib/provenance.ts     which job fills this table, and how often
 *                             (tools/gen_provenance.py reads the Actions,
 *                             the scripts and the n8n workflows)
 *   v_data_freshness          how many rows it has and how old they are
 *                             (sql/ad4_39_freshness.sql)
 *
 * Every component here is safe with neither: with no freshness view it still
 * names the job, and with no job it still says nothing in this repo fills the
 * table, which is itself the answer.
 */

const STATE_STYLE: Record<FreshRow["state"], { dot: string; text: string; word: string }> = {
  absent: { dot: "bg-bad",              text: "text-bad",   word: "not installed" },
  empty:  { dot: "bg-warn",             text: "text-warn",  word: "no data yet" },
  stale:  { dot: "bg-warn",             text: "text-warn",  word: "stale" },
  ok:     { dot: "bg-good",             text: "text-good",  word: "current" },
};

/** Does this job write any of the tables that are currently a problem? */
function fixesAProblem(f: Filler, problem: Set<string>): boolean {
  for (const t of problem) {
    if ((FILLED_BY[t] ?? []).some((x) => x.file === f.file && x.kind === f.kind)) return true;
  }
  return false;
}

function runInstruction(f: Filler): string {
  return f.kind === "action"
    ? `GitHub → Actions → “${f.name}” → Run workflow`
    : `n8n → “${f.name}” → Execute workflow`;
}

/**
 * The chip that goes next to a section heading: is this section's data
 * current, and how old is it. One glance, no reading.
 */
const RANK: Record<FreshRow["state"], number> = { absent: 0, empty: 1, stale: 2, ok: 3 };

/**
 * The freshness rows behind a relation.
 *
 * A panel names the VIEW it reads; what actually goes quiet is a table under
 * it. VIEW_TABLES resolves that, so `relation="v_calibration"` reports on
 * fact_band_outcome without every call site having to know the schema.
 */
function rowsBehind(relation: string, byTable: Map<string, FreshRow>): FreshRow[] {
  const direct = byTable.get(relation);
  if (direct) return [direct];
  return tablesBehind(relation)
    .map((t) => byTable.get(t))
    .filter((r): r is FreshRow => Boolean(r));
}

/** The worst state wins: one stale input makes the panel stale. */
function worstOf(rows: FreshRow[]): FreshRow | null {
  if (!rows.length) return null;
  return rows.slice().sort((a, b) => RANK[a.state] - RANK[b.state])[0];
}

export function Freshness({
  relation,
  showRows = true,
}: {
  relation: string;
  showRows?: boolean;
}) {
  const { byTable, loading } = useFreshness();
  const behind = rowsBehind(relation, byTable);
  const row = worstOf(behind);
  if (loading && !row) return null;
  // No row means ad4_39 has not been run. Say nothing rather than guess.
  if (!row) return null;

  const s = STATE_STYLE[row.state];
  const others = behind.length > 1 ? ` (worst of ${behind.length} inputs)` : "";
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded border border-border/70 bg-panel2/60 px-1.5 py-0.5 text-[10px] ${s.text}`}
      title={`${row.table_name}: ${row.plain_english} ${(row.rows ?? 0).toLocaleString()} row(s), newest ${ageWords(row.age_hours)}.${others}`}
    >
      <span className={`h-1.5 w-1.5 rounded-full ${s.dot}`} />
      {showRows && row.rows !== null ? (
        <span className="tabular-nums">{row.rows.toLocaleString()}</span>
      ) : null}
      <span>{row.state === "ok" ? ageWords(row.age_hours) : s.word}</span>
    </span>
  );
}

/**
 * A row of chips for a section that draws on several tables, so "this panel is
 * thin" can be traced to the one input that is missing rather than the whole
 * section being written off.
 */
export function FreshnessRow({ relations }: { relations: string[] }) {
  const { byTable } = useFreshness();
  const known = relations.filter((r) => byTable.has(r));
  if (!known.length) return null;
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <span className="text-[10px] uppercase tracking-wide text-muted">reads</span>
      {known.map((r) => (
        <span key={r} className="inline-flex items-center gap-1">
          <span className="font-mono text-[10px] text-muted">{r}</span>
          <Freshness relation={r} />
        </span>
      ))}
    </div>
  );
}

/**
 * The block that goes inside an empty panel. Names the job, its cadence, where
 * to press the button, and - when the table is not merely empty but absent -
 * the SQL file that creates it.
 */
export function WhatFillsThis({ relation }: { relation: string }) {
  const { byTable } = useFreshness();
  const behind = rowsBehind(relation, byTable);
  const row = worstOf(behind);
  const fillers = fillersFor(relation);
  const sqlFile = SQL_OWNER[relation];

  if (row?.state === "absent") {
    const missing = row.table_name;
    return (
      <div className="mt-2 space-y-1 text-left text-xs text-muted">
        <p className="text-bad">
          <code className="rounded bg-panel2 px-1">{missing}</code> does not exist in the database.
        </p>
        {SQL_OWNER[missing] || sqlFile ? (
          <p>
            Run <code className="rounded bg-panel2 px-1">sql/{SQL_OWNER[missing] ?? sqlFile}</code> in
            the Supabase SQL editor. It is safe to run again.
          </p>
        ) : null}
      </div>
    );
  }

  if (!fillers.length) {
    return (
      <div className="mt-2 text-left text-xs text-muted">
        <p>
          Nothing in this repo writes{" "}
          <code className="rounded bg-panel2 px-1">{relation}</code>. It fills up as the desk runs,
          or it is filled by hand.
        </p>
      </div>
    );
  }

  // RANK BY WHAT IS ACTUALLY MISSING, and show a few.
  //
  // A view over eight tables lists eight jobs, which is not an answer - it is
  // the whole pipeline printed out. Almost always one input is the problem and
  // the rest are fine, so the jobs that feed an empty or stale table come
  // first, three are shown, and the rest fold away.
  const problem = new Set(
    behind.filter((r) => r.state !== "ok").map((r) => r.table_name)
  );
  const ranked = fillers.slice().sort((a, b) => Number(fixesAProblem(b, problem)) - Number(fixesAProblem(a, problem)));
  const primary = ranked.filter((f) => fixesAProblem(f, problem));
  const shown = (primary.length ? primary : ranked).slice(0, 3);
  const rest = ranked.filter((f) => !shown.includes(f));

  return (
    <div className="mt-3 space-y-2 text-left">
      <div className="text-[10px] uppercase tracking-wide text-muted">
        {primary.length && primary.length < fillers.length
          ? "What fills the part that is missing"
          : "What fills this"}
      </div>
      <ul className="space-y-1.5">
        {shown.map((f) => (
          <li key={f.kind + f.file} className="text-xs leading-relaxed text-muted">
            <span className="font-medium text-text">{f.name}</span>{" "}
            <span className="rounded border border-border px-1 py-px text-[10px] uppercase tracking-wide">
              {f.kind === "action" ? "GitHub Action" : "n8n"}
            </span>
            <br />
            <span>Runs {f.cadence}. </span>
            <span className="text-text">{runInstruction(f)}</span>
            <span className="ml-1 font-mono text-[10px] opacity-70">{f.file}</span>
          </li>
        ))}
      </ul>
      {rest.length > 0 && (
        <details className="text-xs text-muted">
          <summary className="cursor-pointer text-[11px] hover:text-text">
            {rest.length} other job{rest.length === 1 ? "" : "s"} also feed this, and {rest.length === 1 ? "it is" : "they are"} current
          </summary>
          <ul className="mt-1 space-y-0.5 pl-3 text-[11px]">
            {rest.map((f) => (
              <li key={f.kind + f.file}>
                {f.name} <span className="opacity-70">· {f.cadence}</span>
              </li>
            ))}
          </ul>
        </details>
      )}
      {behind.length ? (
        <div className="space-y-0.5 text-xs text-muted">
          <div className="text-[10px] uppercase tracking-wide">Right now</div>
          {behind
            .slice()
            .sort((a, b) => RANK[a.state] - RANK[b.state])
            .map((r) => (
              <p key={r.table_name}>
                <span className={`mr-1 inline-block h-1.5 w-1.5 rounded-full ${STATE_STYLE[r.state].dot}`} />
                <span className="font-mono">{r.table_name}</span>{" "}
                <span className="tabular-nums text-text">{(r.rows ?? 0).toLocaleString()}</span> row(s)
                {r.age_hours !== null ? `, newest ${ageWords(r.age_hours)}` : ""}
              </p>
            ))}
        </div>
      ) : null}
    </div>
  );
}

/**
 * The page-level strip: one line saying whether the desk's inputs are current,
 * expandable into the per-table list. This is the thing that turns "the data is
 * outdated" from a feeling into a list of jobs to run.
 */
export function DataHealthStrip({ layers }: { layers?: string[] }) {
  const { rows, loading, error } = useFreshness();
  if (loading && !rows.length) return null;

  if (error) {
    // The one place the missing view is worth reporting: everywhere else it
    // degrades quietly rather than putting a red box on every panel.
    return (
      <div className="rounded border border-border bg-panel/60 px-3 py-2 text-xs text-muted">
        Freshness tracking is not installed — run{" "}
        <code className="rounded bg-panel2 px-1">sql/ad4_39_freshness.sql</code> to see which jobs
        have stopped filling which tables.
      </div>
    );
  }

  const scope = layers?.length ? rows.filter((r) => layers.includes(r.layer)) : rows;
  if (!scope.length) return null;

  const bad = scope.filter((r) => r.state !== "ok");
  const worst = scope.some((r) => r.state === "absent")
    ? "absent"
    : scope.some((r) => r.state === "stale")
      ? "stale"
      : scope.some((r) => r.state === "empty")
        ? "empty"
        : "ok";
  const s = STATE_STYLE[worst as FreshRow["state"]];

  return (
    <details className="group rounded border border-border bg-panel/60">
      <summary className="flex cursor-pointer list-none items-center gap-2 px-3 py-2 text-xs">
        <span className={`h-2 w-2 rounded-full ${s.dot}`} />
        <span className={s.text}>
          {worst === "ok"
            ? `All ${scope.length} inputs current`
            : `${bad.length} of ${scope.length} inputs need attention`}
        </span>
        <span className="ml-auto text-[10px] text-muted group-open:hidden">show</span>
        <span className="ml-auto hidden text-[10px] text-muted group-open:inline">hide</span>
      </summary>
      <div className="border-t border-border px-3 py-2">
        <table className="w-full text-left text-[11px]">
          <thead className="text-[10px] uppercase tracking-wide text-muted">
            <tr>
              <th className="py-1 pr-2">Table</th>
              <th className="py-1 pr-2 text-right">Rows</th>
              <th className="py-1 pr-2">Newest</th>
              <th className="py-1 pr-2">What it holds</th>
              <th className="py-1">Filled by</th>
            </tr>
          </thead>
          <tbody>
            {scope
              .slice()
              .sort((a, b) => {
                const rank = { absent: 0, empty: 1, stale: 2, ok: 3 } as const;
                return rank[a.state] - rank[b.state] || a.table_name.localeCompare(b.table_name);
              })
              .map((r) => {
                const st = STATE_STYLE[r.state];
                const f = fillersFor(r.table_name);
                return (
                  <tr key={r.table_name} className="border-t border-border/40">
                    <td className="py-1 pr-2">
                      <span className="inline-flex items-center gap-1.5">
                        <span className={`h-1.5 w-1.5 rounded-full ${st.dot}`} />
                        <span className="font-mono">{r.table_name}</span>
                      </span>
                    </td>
                    <td className="py-1 pr-2 text-right tabular-nums">
                      {r.rows === null ? "—" : r.rows.toLocaleString()}
                    </td>
                    <td className={`py-1 pr-2 ${r.state === "ok" ? "text-muted" : st.text}`}>
                      {r.state === "absent" ? "not installed" : ageWords(r.age_hours)}
                    </td>
                    <td className="py-1 pr-2 text-muted">{r.plain_english}</td>
                    <td className="py-1 text-muted">
                      {f.length ? f.map((x) => x.name).join(", ") : "—"}
                    </td>
                  </tr>
                );
              })}
          </tbody>
        </table>
        <p className="mt-2 text-[11px] text-muted">
          Stale means the newest row is older than that job&apos;s own cadence — not older than some
          global rule. A daily job is not late at 25 hours.{" "}
          <Link href="/workflows" className="text-accent hover:underline">
            Workflows
          </Link>{" "}
          shows the last run of each.
        </p>
      </div>
    </details>
  );
}
