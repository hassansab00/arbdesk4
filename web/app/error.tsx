"use client";

import { useEffect } from "react";

/**
 * Route-level error boundary. Task 2.3's rule is that the user must never
 * get a blank screen - so if a page throws in spite of every guard, show
 * the actual message and a way out, not "Application error: a client-side
 * exception has occurred".
 */
export default function Error({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  useEffect(() => {
    console.error(error);
  }, [error]);

  const configIssue = /supabase is not configured/i.test(error.message);

  return (
    <div className="mx-auto max-w-2xl p-8">
      <h1 className="text-lg font-semibold text-bad">This page failed to render</h1>
      <pre className="mt-2 whitespace-pre-wrap break-words rounded border border-bad/50 bg-bad/10 p-3 font-mono text-xs text-bad">
        {error.message}
        {error.digest ? `\n\ndigest: ${error.digest}` : ""}
      </pre>
      {configIssue ? (
        <p className="mt-3 text-xs text-muted">
          Set <code>NEXT_PUBLIC_SUPABASE_URL</code> and <code>NEXT_PUBLIC_SUPABASE_ANON_KEY</code> in
          Vercel → Settings → Environment Variables, then redeploy. They are inlined at build time,
          so a redeploy is required — restarting is not enough.
        </p>
      ) : (
        <p className="mt-3 text-xs text-muted">
          The full stack is in the browser console. If the message mentions a missing relation, run
          the SQL files in order starting with <code>sql/ad4_00_preflight.sql</code> — see{" "}
          <code>docs/GO_LIVE.md</code>.
        </p>
      )}
      <button onClick={reset} className="mt-4 rounded border border-border px-3 py-1 text-sm text-muted hover:text-text">
        Try again
      </button>
    </div>
  );
}
