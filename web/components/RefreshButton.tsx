"use client";

import { useWorkflow } from "@/lib/useWorkflow";

/**
 * "This number is stale — go and get a new one."
 *
 * Disabled with an explanation rather than hidden when the workflow has no
 * URL yet: a missing button is indistinguishable from a broken one.
 */
export default function RefreshButton({
  job, label, body, onDone, className = "",
}: {
  job: string;
  label: string;
  body?: Record<string, unknown>;
  onDone?: () => void;
  className?: string;
}) {
  const wf = useWorkflow(job);
  const disabled = !wf.url || wf.busy;

  return (
    <span className={`inline-flex flex-col items-end gap-1 ${className}`}>
      <button
        onClick={() => wf.fire(body ?? {}, onDone)}
        disabled={disabled}
        title={
          wf.url
            ? `POSTs to ${job} in n8n, then re-reads this page.`
            : `No webhook URL for ${job}. Paste its n8n Production URL on the Workflows page.`
        }
        className={`rounded border px-2.5 py-1 text-xs transition ${
          disabled
            ? "cursor-not-allowed border-border text-muted"
            : "border-accent text-accent hover:bg-accent/10"
        }`}
      >
        {wf.busy ? "refreshing…" : label}
      </button>
      {wf.message && (
        <span className={`max-w-xs text-right text-[10px] leading-tight ${wf.ok ? "text-good" : "text-bad"}`}>
          {wf.message}
        </span>
      )}
      {wf.ready && !wf.url && (
        <span className="text-[10px] text-muted">set its URL on Workflows to enable</span>
      )}
    </span>
  );
}
