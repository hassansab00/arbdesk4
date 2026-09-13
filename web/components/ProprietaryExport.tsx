"use client";

import { useEffect, useMemo, useState } from "react";
import { PROPRIETARY_EXPORT_DATASETS } from "@/lib/proprietaryExport";

function dayOffset(days: number) {
  const date = new Date();
  date.setUTCDate(date.getUTCDate() + days);
  return date.toISOString().slice(0, 10);
}

export default function ProprietaryExport() {
  const [dataset, setDataset] = useState("research_bundle");
  const [from, setFrom] = useState(() => dayOffset(-6));
  const [through, setThrough] = useState(() => dayOffset(0));
  const [enabled, setEnabled] = useState<boolean | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const selected = useMemo(() => PROPRIETARY_EXPORT_DATASETS.find((item) => item.key === dataset), [dataset]);

  useEffect(() => {
    fetch("/api/proprietary-export", { cache: "no-store" })
      .then((response) => response.json())
      .then((payload) => setEnabled(payload.enabled === true))
      .catch(() => setEnabled(false));
  }, []);

  async function download() {
    setBusy(true);
    setMessage(null);
    try {
      const response = await fetch("/api/proprietary-export", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ dataset, from, through }),
      });
      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        throw new Error(typeof payload.error === "string" ? payload.error : "Export failed.");
      }
      const blob = await response.blob();
      const disposition = response.headers.get("content-disposition") || "";
      const fileName = disposition.match(/filename="([^"]+)"/)?.[1] || `arbdesk4-${dataset}.ndjson`;
      const href = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = href;
      anchor.download = fileName;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(href);
      setMessage(`Downloaded ${fileName}`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Export failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mt-2 rounded border border-border bg-panel p-3">
      <div className="grid gap-3 md:grid-cols-[minmax(220px,1fr)_145px_145px_auto] md:items-end">
        <label className="text-[11px] text-muted">
          Dataset
          <select className="mt-1 w-full rounded border border-border bg-panel2 px-2 py-1.5 text-xs text-text"
            value={dataset} onChange={(event) => setDataset(event.target.value)}>
            {PROPRIETARY_EXPORT_DATASETS.map((item) => <option key={item.key} value={item.key}>{item.label}</option>)}
          </select>
        </label>
        <label className="text-[11px] text-muted">
          From
          <input type="date" value={from} onChange={(event) => setFrom(event.target.value)}
            className="mt-1 w-full rounded border border-border bg-panel2 px-2 py-1.5 text-xs text-text" />
        </label>
        <label className="text-[11px] text-muted">
          Through
          <input type="date" value={through} onChange={(event) => setThrough(event.target.value)}
            className="mt-1 w-full rounded border border-border bg-panel2 px-2 py-1.5 text-xs text-text" />
        </label>
        <button type="button" disabled={busy || enabled !== true} onClick={download}
          className="rounded border border-accent/50 bg-accent/10 px-3 py-1.5 text-xs font-semibold text-accent disabled:cursor-not-allowed disabled:opacity-40">
          {busy ? "Preparing…" : "Export NDJSON"}
        </button>
      </div>
      <p className="mt-2 text-[11px] leading-relaxed text-muted">{selected?.description}</p>
      <p className="mt-1 text-[10px] leading-relaxed text-muted">
        Read-only export. Source rows are never changed. Each file ends with its row count and SHA-256 manifest.
        Exports are limited to 31 days and 50,000 rows so the app does not create an unbounded server job.
      </p>
      {enabled === false && (
        <p className="mt-2 text-[11px] text-warn">
          Export is safely disabled. Set the server-only <code>SUPABASE_SERVICE_KEY</code> and <code>ARBDESK_PRIVATE_EXPORT_ENABLED=true</code>, then redeploy.
        </p>
      )}
      {message && <p className={`mt-2 text-[11px] ${message.startsWith("Downloaded") ? "text-good" : "text-bad"}`}>{message}</p>}
    </div>
  );
}
