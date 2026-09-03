"use client";

import { useCallback, useEffect, useState } from "react";
import { supabase } from "@/lib/supabase";

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
  url: string | null;
  busy: boolean;
  ok: boolean | null;
  message: string | null;
  /** null while settings are still loading - the button shows neither state. */
  ready: boolean;
}

export function useWorkflow(job: string) {
  const [url, setUrl] = useState<string | null>(null);
  const [ready, setReady] = useState(false);
  const [busy, setBusy] = useState(false);
  const [ok, setOk] = useState<boolean | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const { data } = await supabase
          .from("settings").select("value").eq("key", "n8n_webhooks").limit(1);
        const v = (data?.[0]?.value ?? {}) as Record<string, { url?: string }>;
        if (!cancelled) setUrl(v[job]?.url || null);
      } catch {
        if (!cancelled) setUrl(null);
      } finally {
        if (!cancelled) setReady(true);
      }
    })();
    return () => { cancelled = true; };
  }, [job]);

  const fire = useCallback(
    async (body: Record<string, unknown> = {}, onDone?: () => void) => {
      if (!url) return;
      setBusy(true); setOk(null); setMessage(null);
      try {
        const res = await fetch(url, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ source: "ad4-ui", ...body }),
        });
        const text = await res.text();
        let msg = text;
        try { msg = JSON.parse(text).summary ?? text; } catch { /* not JSON - show it raw */ }
        setOk(res.ok);
        setMessage(
          res.ok
            ? msg || "Fired. n8n answered 200 with no body."
            : `n8n returned ${res.status}: ${msg || "(empty body)"}`
        );
        // The workflow writes its rows and then ingest_log; give it a moment
        // before re-reading, or the page reloads the same stale numbers and
        // the button looks broken.
        if (res.ok && onDone) setTimeout(onDone, 3000);
      } catch (e) {
        setOk(false);
        setMessage(
          e instanceof Error && /Failed to fetch/i.test(e.message)
            ? "Could not reach n8n. Either the URL is wrong, the workflow is not Active " +
              "(an inactive workflow only answers its /webhook-test/ URL), or the browser " +
              "was blocked by CORS."
            : e instanceof Error ? e.message : String(e)
        );
      } finally {
        setBusy(false);
      }
    },
    [url]
  );

  return { url, ready, busy, ok, message, fire } as const;
}
