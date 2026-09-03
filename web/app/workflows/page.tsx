"use client";

import { useMemo, useState } from "react";
import { supabase } from "@/lib/supabase";
import { useQuery } from "@/lib/useQuery";
import { DataState, InlineError } from "@/components/DataState";
import { fmtAge, fmtInt } from "@/lib/format";

/**
 * Workflows — run the n8n jobs on demand and see how the last run went.
 *
 * The Run button POSTs straight to each workflow's n8n production webhook.
 * The URLs live in settings.n8n_webhooks (see sql/ad4_14_workflows.sql) so
 * they are editable here rather than compiled into the bundle. A workflow
 * with no URL saved shows a disabled button and says why.
 *
 * Every workflow also writes to ingest_log at the end of every execution —
 * scheduled, manual-in-n8n, or fired from here — so the "Last run" column is
 * the truth for all three, not just the ones started from this page.
 */

interface WebhookEntry {
  url?: string;
  path?: string;
  label?: string;
}

interface WorkflowRun {
  job: string;
  status: string | null;
  rows: number | null;
  logged_at: string | null;
  trigger: string | null;
  summary: string | null;
}

// Static description of each workflow: what it does and when it runs on its
// own. The schedule text mirrors the Schedule Trigger in each n8n file.
const CATALOGUE: Array<{
  job: string;
  label: string;
  schedule: string;
  what: string;
  variants?: Array<{ label: string; body: Record<string, unknown> }>;
  note?: string;
}> = [
  {
    job: "P0.2_market_discovery",
    label: "Market Discovery",
    schedule: "scheduled in n8n",
    what: "Finds new Polymarket temperature markets and upserts markets + bands.",
  },
  {
    job: "P0.3_book_volume_snapshot",
    label: "Book + Volume Snapshot",
    schedule: "scheduled in n8n",
    what:
      "Writes one book_snapshots row per live band: the ladder into raw_book, the cumulative USD depth tiers, and the exchange's 24h volume. This is what every fill price on Goals and Calculator walks.",
  },
  {
    job: "P0.4_trade_history",
    label: "Trade History",
    schedule: "scheduled in n8n",
    what: "Pulls recent prints into trades_observed, then calls refresh_derived() to rebuild the city-day and band-day volume rollups.",
  },
  {
    job: "P0.5_refresh_rules_text",
    label: "Refresh Rules Text",
    schedule: "scheduled in n8n",
    what: "Re-reads each open market's resolution rules and flags any that changed. A changed rule invalidates probabilities computed against the old text.",
  },
  {
    job: "P1.1_live_weather_alerts",
    label: "Live Weather Alerts",
    schedule: "webhook, fired by live_weather.py",
    what: "Emails high/critical weather events and marks them notified. Normally called by scripts/live_weather.py, not by hand.",
    note: "Running this from here sends an empty payload, so it exercises the workflow without emailing anything. That is the intended smoke test.",
  },
  {
    job: "P1.2_nws_monitor",
    label: "NWS Monitor",
    schedule: "every 2 hours",
    what:
      "Reads api.weather.gov \u2014 the service most of these markets settle on \u2014 for every US city: the current observation with its quality-control flag, any active heat advisory or warning, and today's solar transit (the sun's zenith, which is where the daily peak sits). Writes observations beside the IEM ones so the two feeds can be compared, and raises an alert only when it changes.",
    note: "api.weather.gov has no rate limit, but n8n's execution budget does \u2014 hence 2 hours, with this button for refreshing sooner. Non-US cities are checked once, marked unsupported, and skipped from then on.",
  },
  {
    job: "P1.3_nws_forecast",
    label: "NWS Forecast",
    schedule: "every 6 hours",
    what:
      "The second forecast model. Takes NWS's hourly gridpoint forecast and writes a daily maximum per city, beside Open-Meteo's. Where the two disagree about a day, that day's sigma widens \u2014 disagreement can only ever make AD4 less confident, never more.",
    note: "A day whose hourly series misses the 12:00\u201318:00 peak window is skipped rather than written low: an understated max would invent disagreement that is not there.",
  },
  {
    job: "P3.1_email_digests",
    label: "Email Digests",
    schedule: "04:00 and 21:00 UTC",
    what: "Builds and sends the morning brief and the end-of-day report.",
    variants: [
      { label: "Run morning", body: { digest: "morning" } },
      { label: "Run EOD", body: { digest: "eod" } },
    ],
  },
  {
    job: "P4.1_health_watchdog",
    label: "Health Watchdog",
    schedule: "every 6 hours",
    what: "Checks snapshot freshness, forecast runs, failed ingest jobs, anomalies and traded volume. Emails only on failure.",
  },
];

type RunState = { busy: boolean; ok: boolean | null; message: string | null };

export default function WorkflowsPage() {
  const [runs, setRuns] = useState<Record<string, RunState>>({});
  const [editing, setEditing] = useState<string | null>(null);
  const [draftUrl, setDraftUrl] = useState("");
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const settingsQ = useQuery<Array<{ key: string; value: any }>>(
    () => supabase.from("settings").select("key,value").eq("key", "n8n_webhooks"),
    []
  );
  const runsQ = useQuery<WorkflowRun[]>(
    () => supabase.from("v_workflow_runs").select("job,status,rows,logged_at,trigger,summary"),
    [],
    30000
  );

  const webhooks: Record<string, WebhookEntry> = useMemo(() => {
    const row = (settingsQ.data ?? [])[0];
    const v = (row?.value ?? {}) as Record<string, unknown>;
    const out: Record<string, WebhookEntry> = {};
    for (const [k, entry] of Object.entries(v)) {
      if (entry && typeof entry === "object") out[k] = entry as WebhookEntry;
    }
    return out;
  }, [settingsQ.data]);

  const runByJob = useMemo(() => {
    const m: Record<string, WorkflowRun> = {};
    for (const r of runsQ.data ?? []) m[r.job] = r;
    return m;
  }, [runsQ.data]);

  const configured = CATALOGUE.filter((w) => webhooks[w.job]?.url).length;

  async function fire(job: string, body: Record<string, unknown> = {}) {
    const url = webhooks[job]?.url;
    if (!url) return;
    setRuns((s) => ({ ...s, [job]: { busy: true, ok: null, message: null } }));
    try {
      const res = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ source: "ad4-ui", ...body }),
      });
      const text = await res.text();
      let msg = text;
      try {
        const parsed = JSON.parse(text);
        msg = parsed.summary ?? text;
      } catch {
        /* not JSON — show whatever n8n returned, verbatim */
      }
      if (!res.ok) {
        setRuns((s) => ({
          ...s,
          [job]: { busy: false, ok: false, message: `n8n returned ${res.status}: ${msg || "(empty body)"}` },
        }));
      } else {
        setRuns((s) => ({
          ...s,
          [job]: { busy: false, ok: true, message: msg || "Fired. n8n answered 200 with no body." },
        }));
        // the run writes ingest_log at the end; give it a moment then refresh
        setTimeout(() => runsQ.refresh(), 2500);
      }
    } catch (e: any) {
      // A thrown fetch is almost always CORS or an unreachable host — the
      // browser deliberately hides which. Say that instead of "failed".
      setRuns((s) => ({
        ...s,
        [job]: {
          busy: false,
          ok: false,
          message:
            `${e?.message ?? e}. The browser blocks the detail, but this is almost always one of: ` +
            `the workflow is not Active in n8n (inactive workflows only answer the /webhook-test/ URL), ` +
            `the Webhook Trigger's "allowedOrigins" does not include this site, ` +
            `or the n8n host is unreachable from here.`,
        },
      }));
    }
  }

  async function saveUrl(job: string) {
    setSaving(true);
    setSaveError(null);
    const current = (settingsQ.data ?? [])[0]?.value ?? {};
    const next = {
      ...current,
      [job]: { ...(webhooks[job] ?? {}), url: draftUrl.trim() },
    };
    const { data, error } = await supabase.rpc("update_setting", { p_key: "n8n_webhooks", p_value: next });
    setSaving(false);
    if (error) {
      setSaveError(error.message);
      return;
    }
    if (data && (data as any).ok === false) {
      setSaveError((data as any).error ?? "update_setting refused the write.");
      return;
    }
    setEditing(null);
    setDraftUrl("");
    settingsQ.refresh();
  }

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-lg font-semibold">Workflows</h1>
        <p className="mt-1 max-w-3xl text-xs leading-relaxed text-muted">
          The seven n8n jobs, and the state of their last run. Each one writes to{" "}
          <code>ingest_log</code> when it finishes — whether it was fired from here, run by
          hand in n8n, or started by its own schedule — so &ldquo;Last run&rdquo; is the truth for all
          three. Running a job here POSTs to its n8n production webhook; the URLs are stored in{" "}
          <code>settings.n8n_webhooks</code> and are editable below.
        </p>
        {settingsQ.data && configured === 0 && (
          <p className="mt-2 max-w-3xl rounded border border-border px-3 py-2 text-xs leading-relaxed text-muted">
            No webhook URLs saved yet, so every Run button is disabled. Import the workflows into
            n8n, open each one&rsquo;s <b>Webhook Trigger</b> node, copy its <b>Production URL</b>,
            and paste it in with <b>set URL</b>. Until then this page is a read-only status board,
            which is still useful — the schedules populate it on their own.
          </p>
        )}
      </div>

      <DataState
        loading={settingsQ.loading || runsQ.loading}
        error={settingsQ.error ?? runsQ.error}
        isEmpty={false}
        emptyTitle=""
        emptyBody={null}
        onRetry={() => {
          settingsQ.refresh();
          runsQ.refresh();
        }}
      >
        <div className="overflow-x-auto rounded border border-border">
          <table className="w-full text-sm">
            <thead className="bg-panel2 text-muted">
              <tr>
                <th className="p-2 text-left">Workflow</th>
                <th className="p-2 text-left">Own schedule</th>
                <th className="p-2 text-left">Last run</th>
                <th className="p-2 text-right">Rows</th>
                <th className="p-2 text-left">Result</th>
                <th className="p-2 text-left">Run</th>
              </tr>
            </thead>
            <tbody>
              {CATALOGUE.map((w) => {
                const hook = webhooks[w.job];
                const last = runByJob[w.job];
                const st = runs[w.job];
                const variants = w.variants ?? [{ label: "Run now", body: {} }];
                return (
                  <tr key={w.job} className="border-t border-border align-top">
                    <td className="p-2">
                      <div className="font-medium">{w.label}</div>
                      <div className="font-mono text-[10px] text-muted">{w.job}</div>
                      <div className="mt-1 max-w-md text-[11px] leading-relaxed text-muted">{w.what}</div>
                      {w.note && (
                        <div className="mt-1 max-w-md text-[11px] leading-relaxed text-warn">{w.note}</div>
                      )}
                    </td>
                    <td className="p-2 text-xs text-muted">{w.schedule}</td>
                    <td className="p-2 text-xs">
                      {last?.logged_at ? (
                        <>
                          <div title={last.logged_at}>{fmtAge(last.logged_at)}</div>
                          <div className="text-[10px] text-muted">via {last.trigger ?? "unknown"}</div>
                        </>
                      ) : (
                        <span className="text-muted">never</span>
                      )}
                    </td>
                    <td className="p-2 text-right font-mono text-xs">
                      {last ? fmtInt(last.rows) : "—"}
                    </td>
                    <td className="p-2 text-xs">
                      {last?.status && (
                        <span
                          className={
                            last.status === "ok"
                              ? "text-good"
                              : last.status === "error"
                              ? "text-bad"
                              : "text-warn"
                          }
                        >
                          {last.status}
                        </span>
                      )}
                      {last?.summary && (
                        <div className="mt-1 max-w-md text-[11px] leading-relaxed text-muted">
                          {last.summary}
                        </div>
                      )}
                      {st?.message && (
                        <div
                          className={`mt-1 max-w-md text-[11px] leading-relaxed ${
                            st.ok ? "text-good" : "text-bad"
                          }`}
                        >
                          {/* what n8n answered just now, as distinct from the
                              ingest_log row above, which is what the run
                              recorded in the database */}
                          <span className="text-muted">this run: </span>
                          {st.message}
                        </div>
                      )}
                    </td>
                    <td className="p-2">
                      <div className="flex flex-col gap-1">
                        {variants.map((v) => (
                          <button
                            key={v.label}
                            onClick={() => fire(w.job, v.body)}
                            disabled={!hook?.url || st?.busy}
                            title={
                              hook?.url
                                ? `POST ${hook.url}`
                                : "No webhook URL saved for this workflow — use “set URL”."
                            }
                            className="whitespace-nowrap rounded border border-border px-2 py-1 text-xs hover:bg-panel2 disabled:cursor-not-allowed disabled:opacity-40"
                          >
                            {st?.busy ? "running…" : v.label}
                          </button>
                        ))}
                        <button
                          onClick={() => {
                            setEditing(editing === w.job ? null : w.job);
                            setDraftUrl(hook?.url ?? "");
                            setSaveError(null);
                          }}
                          className="whitespace-nowrap text-[11px] text-muted underline hover:text-text"
                        >
                          {hook?.url ? "change URL" : "set URL"}
                        </button>
                      </div>
                      {editing === w.job && (
                        <div className="mt-2 w-64 space-y-1">
                          <input
                            value={draftUrl}
                            onChange={(e) => setDraftUrl(e.target.value)}
                            placeholder={`https://your-n8n/webhook/${hook?.path ?? ""}`}
                            className="w-full rounded border border-border bg-panel px-2 py-1 font-mono text-[11px]"
                          />
                          <div className="flex gap-1">
                            <button
                              onClick={() => saveUrl(w.job)}
                              disabled={saving}
                              className="rounded border border-border px-2 py-1 text-xs hover:bg-panel2 disabled:opacity-40"
                            >
                              {saving ? "saving…" : "Save"}
                            </button>
                            <button
                              onClick={() => setEditing(null)}
                              className="rounded border border-border px-2 py-1 text-xs text-muted hover:bg-panel2"
                            >
                              Cancel
                            </button>
                          </div>
                          <InlineError message={saveError} />
                        </div>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </DataState>

      <p className="max-w-3xl text-[11px] leading-relaxed text-muted">
        <b>Why a Run button can fail even when the workflow is fine:</b> an inactive n8n workflow
        only answers its <code>/webhook-test/</code> URL, and only for one call after you press
        &ldquo;Listen for test event&rdquo;. The <code>/webhook/</code> URL used here needs the
        workflow <b>Active</b>. The browser also needs the Webhook Trigger&rsquo;s{" "}
        <code>allowedOrigins</code> to include this site — the shipped workflows set it to{" "}
        <code>*</code>, which you can narrow to your own domain once it is settled.
      </p>
    </div>
  );
}
