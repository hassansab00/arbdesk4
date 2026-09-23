"use client";

import { useCallback, useEffect, useState } from "react";
import { fireWorkflow, readWorkflowSettings } from "@/lib/operator";

/**
 * Fire an n8n workflow from any page.
 *
 * The Workflows page has had a Run button since it was built; every other
 * page had a stale table and no way to do anything about it. "Refresh" on the
 * Board means "go and re-read the book from Polymarket", which is P0.3's job -
 * so the button belongs where the stale number is, not three tabs away.
 *
 * The URL lives in settings.n8n_webhooks, pasted after import. Until it is
 * there the button is disabled and says why, rather than failing on click.
 */
export interface WorkflowState {
  /** Whether a webhook URL is saved for this job. The URL itself stays on the server. */
  configured: boolean;
  busy: boolean;
  ok: boolean | null;
  message: string | null;
  /** false while settings are still loading - the button shows neither state. */
  ready: boolean;
}

/**
 * THE BROWSER NO LONGER HOLDS THE URL (plan v2 P1.2). It used to read
 * settings.n8n_webhooks as anon and POST to n8n itself, so every visitor had
 * every webhook URL, and a webhook URL is all it takes to fire a job. Now it
 * asks /api/operator whether the job is configured, and firing goes through
 * the same route, which needs a signed-in operator.
 */
export function useWorkflow(job: string) {
  const [configured, setConfigured] = useState(false);
  const [ready, setReady] = useState(false);
  const [busy, setBusy] = useState(false);
  const [ok, setOk] = useState<boolean | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const { data } = await readWorkflowSettings();
      if (cancelled) return;
      setConfigured(!!data?.configured?.[job]);
      setReady(true);
    })();
    return () => { cancelled = true; };
  }, [job]);

  const fire = useCallback(
    async (body: Record<string, unknown> = {}, onDone?: () => void) => {
      if (!configured) return;
      setBusy(true); setOk(null); setMessage(null);
      const { data, error } = await fireWorkflow(job, body);
      setBusy(false);
      if (error || !data) {
        setOk(false);
        setMessage(error?.message ?? "The run could not be started.");
        return;
      }
      let msg = data.text;
      try { msg = JSON.parse(data.text).summary ?? data.text; } catch { /* not JSON - show it raw */ }
      setOk(data.ok);
      setMessage(
        data.ok
          ? msg || "Fired. n8n answered 200 with no body."
          : `n8n returned ${data.status}: ${msg || "(empty body)"}`
      );
      // The workflow writes its rows and then ingest_log; give it a moment
      // before re-reading, or the page reloads the same stale numbers and
      // the button looks broken.
      if (data.ok && onDone) setTimeout(onDone, 3000);
    },
    [job, configured]
  );

  return { configured, ready, busy, ok, message, fire } as const;
}
