"use client";

import type { ReactNode } from "react";
import { SQL_OWNER } from "@/lib/sqlOwner";

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
}: {
  loading: boolean;
  error: string | null;
  isEmpty: boolean;
  emptyTitle: string;
  emptyBody: ReactNode;
  onRetry?: () => void;
  children: ReactNode;
  compact?: boolean;
}) {
  if (loading) return <Loading compact={compact} />;
  if (error) return <ErrorBox message={error} onRetry={onRetry} compact={compact} />;
  if (isEmpty) return <EmptyBox title={emptyTitle} body={emptyBody} onRetry={onRetry} compact={compact} />;
  return <>{children}</>;
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
      {missingRelation && !configIssue && (
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
}: {
  title: string;
  body: ReactNode;
  onRetry?: () => void;
  compact?: boolean;
}) {
  return (
    <div className={`rounded border border-dashed border-border bg-panel/60 text-center ${compact ? "p-4" : "p-8"}`}>
      <div className="text-sm font-semibold text-text">{title}</div>
      <div className="mx-auto mt-1 max-w-lg text-xs leading-relaxed text-muted">{body}</div>
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
