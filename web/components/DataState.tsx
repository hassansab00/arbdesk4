"use client";

import type { ReactNode } from "react";
import { SQL_OWNER } from "@/lib/sqlOwner";
import { WhatFillsThis } from "@/components/Provenance";

/**
 * The three honest states, rendered the same way on every page (Task 2.2 /
 * 2.3). Wrap any data-driven region in <DataState> and it can never show a
 * blank screen, a forever-spinner, or a swallowed error.
 */
export function DataState({
  loading,
  error,
  isEmpty,
  emptyTitle,
  emptyBody,
  onRetry,
  children,
  compact = false,
  relation,
  truncated = false,
}: {
  loading: boolean;
  error: string | null;
  isEmpty: boolean;
  emptyTitle: string;
  emptyBody: ReactNode;
  onRetry?: () => void;
  children: ReactNode;
  compact?: boolean;
  /**
   * The TABLE this panel ultimately depends on - not the view it reads.
   *
   * Given it, an empty panel stops being a dead end: it names the job that
   * fills the table, how often that job runs, and where the button is. That
   * mapping is derived by tools/gen_provenance.py, so it cannot drift from
   * the Actions and workflows it describes.
   */
  relation?: string;
  /**
   * The query came back exactly full, so rows were dropped.
   *
   * Rendered ABOVE the content rather than instead of it, because truncated
   * data is still data - the chart is right about the rows it has and wrong
   * about being complete, and hiding it would be a worse answer than showing
   * it with a warning. Pass `q.truncated` from useQuery.
   */
  truncated?: boolean;
}) {
  if (loading) return <Loading compact={compact} />;
  if (error) return <ErrorBox message={error} onRetry={onRetry} compact={compact} />;
  if (isEmpty)
    return (
      <EmptyBox title={emptyTitle} body={emptyBody} onRetry={onRetry} compact={compact} relation={relation} />
    );
  if (truncated)
    return (
      <>
        <TruncatedBar relation={relation} />
        {children}
      </>
    );
  return <>{children}</>;
}

/**
 * The warning for a query that hit its own ceiling.
 *
 * PostgREST does not tell you it truncated - it returns exactly as many rows
 * as you asked for, in view order, and says nothing. A page that then filters
 * those rows in the browser shows a confident, complete-looking answer built
 * on the alphabetically first slice of the data. That is how the Predictive
 * page came to show one city: it was not a bug in the other cities, they were
 * past row 20,000.
 */
export function TruncatedBar({ relation }: { relation?: string }) {
  return (
    <div className="mb-2 rounded border border-warn/40 bg-warn/10 px-3 py-2 text-[11px] leading-relaxed text-warn">
      <strong className="font-semibold">Showing part of the data.</strong>{" "}
      This query came back exactly full{relation ? <> from <code className="rounded bg-panel2 px-1">{relation}</code></> : null},
      which means rows were dropped — what you see below is the first slice, not
      everything. Narrow it (pick one city, a shorter window) or raise the row
      limit for this panel.
    </div>
  );
}

export function Loading({ compact = false, label = "Loading…" }: { compact?: boolean; label?: string }) {
  return (
    <div className={`flex items-center gap-2 text-muted ${compact ? "p-2 text-xs" : "p-8 text-sm"}`}>
      <span className="inline-block h-3 w-3 animate-spin rounded-full border-2 border-border border-t-accent" />
      {label}
    </div>
  );
}

export function ErrorBox({
  message,
  onRetry,
  compact = false,
}: {
  message: string;
  onRetry?: () => void;
  compact?: boolean;
}) {
  const secretKey = /secret api key|SECRET Supabase key/i.test(message);
  const configIssue = /supabase is not configured/i.test(message);
  const missingRelation = /does not exist|schema cache|PGRST\d+/i.test(message);
  // 57014 is Supabase cancelling a query that ran past the statement_timeout
  // it sets for the browser's role - a few seconds. It is not a broken view
  // and re-running does not help; it means the query is too slow for the
  // budget. Shown as its own case because the remedy is nothing like the
  // remedy for a missing relation, and the raw text says neither.
  const timedOut = /57014|statement timeout|canceling statement/i.test(message);

  // The secret-key case is page-wide: ConfigBanner already states it in full
  // at the top. Repeating the whole remediation in every failed query - and
  // there are a dozen per page - buries the one instruction that matters.
  if (secretKey) {
    return (
      <div className={`rounded border border-bad/60 bg-bad/10 ${compact ? "p-2" : "p-3"}`}>
        <div className="text-sm font-semibold text-bad">Blocked: secret key in the browser</div>
        <p className="mt-0.5 text-xs text-bad">
          No request was sent. See the banner at the top of the page — the key must be rotated.
        </p>
      </div>
    );
  }

  return (
    <div className={`rounded border border-bad/60 bg-bad/10 ${compact ? "p-2" : "p-4"}`}>
      <div className="text-sm font-semibold text-bad">Query failed</div>
      {/* The raw Supabase/Postgres message, verbatim and unswallowed. */}
      <pre className="mt-1 whitespace-pre-wrap break-words font-mono text-xs text-bad">{message}</pre>
      {configIssue && (
        <p className="mt-2 text-xs text-muted">
          Set <code>NEXT_PUBLIC_SUPABASE_URL</code> and <code>NEXT_PUBLIC_SUPABASE_ANON_KEY</code> in
          Vercel → Settings → Environment Variables, then redeploy. See <code>web/README.md</code>.
        </p>
      )}
      {timedOut && (
        <div className="mt-2 space-y-1 text-xs text-muted">
          <p className="text-warn">
            The database cancelled this query for taking too long — not because anything is
            missing. Supabase gives the browser&apos;s key a few seconds per query, and this one
            went past it.
          </p>
          <p>
            Run <code className="rounded bg-panel2 px-1">sql/ad4_44_indexes.sql</code>. Four of the
            busiest tables shipped with no index except a primary key nobody queries by, so every
            lookup was a full table scan — which is fast while a table is small and becomes this
            once it is not. It is safe to run at any time and changes no data.
          </p>
          <p>
            Then <code className="rounded bg-panel2 px-1">select * from v_table_scan_risk</code>{" "}
            lists any table still carrying nothing but its primary key.
          </p>
        </div>
      )}
      {missingRelation && !configIssue && !timedOut && (
        <p className="mt-2 text-xs text-muted">
          {(() => {
            // NAME THE FILE THAT CREATES IT. This used to say "run
            // sql/ad4_00_preflight.sql" for every missing relation, which is
            // right about one file in thirty - every Predictive panel failed
            // on a view created by ad4_31 and sent the reader to preflight.
            const m = message.match(/'public\.([a-z0-9_]+)'|relation "([a-z0-9_]+)"/i);
            const rel = (m?.[1] ?? m?.[2] ?? "").toLowerCase();
            const file = SQL_OWNER[rel];
            if (rel && file) {
              return (
                <>
                  <code>{rel}</code> is created by <code>sql/{file}</code>. Run that file in the
                  Supabase SQL editor — it is safe to run again.
                </>
              );
            }
            return (
              <>
                A table or view this page reads does not exist yet. Run the SQL files in order —
                see <code>docs/EVERYTHING.md</code> step 3.
              </>
            );
          })()}
        </p>
      )}
      {onRetry && (
        <button onClick={onRetry} className="mt-2 rounded border border-bad/60 px-2 py-0.5 text-xs text-bad hover:bg-bad/20">
          Retry
        </button>
      )}
    </div>
  );
}

export function EmptyBox({
  title,
  body,
  onRetry,
  compact = false,
  relation,
}: {
  title: string;
  body: ReactNode;
  onRetry?: () => void;
  compact?: boolean;
  relation?: string;
}) {
  return (
    <div className={`rounded border border-dashed border-border bg-panel/60 text-center ${compact ? "p-4" : "p-8"}`}>
      <div className="text-sm font-semibold text-text">{title}</div>
      <div className="mx-auto mt-1 max-w-lg text-xs leading-relaxed text-muted">{body}</div>
      {relation ? (
        <div className="mx-auto mt-3 max-w-lg border-t border-border/60 pt-2">
          <WhatFillsThis relation={relation} />
        </div>
      ) : null}
      {onRetry && (
        <button onClick={onRetry} className="mt-3 rounded border border-border px-2 py-0.5 text-xs text-muted hover:text-text">
          Check again
        </button>
      )}
    </div>
  );
}

/** Inline banner for a secondary query that failed inside an otherwise-fine page. */
export function InlineError({ message }: { message: string | null }) {
  if (!message) return null;
  // Same reasoning as ErrorBox: the secret-key case is stated once, at the
  // top of the page, not repeated in every corner of it.
  const short = /secret api key|SECRET Supabase key/i.test(message)
    ? "Blocked: secret key in the browser - see the banner at the top of the page."
    : message;
  return (
    <div className="rounded border border-bad/50 bg-bad/10 px-2 py-1 font-mono text-[11px] text-bad">{short}</div>
  );
}
