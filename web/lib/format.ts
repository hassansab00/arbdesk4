export function fmtUsd(v: number | null | undefined, opts: { signed?: boolean } = {}): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  const sign = opts.signed && v > 0 ? "+" : "";
  return `${sign}$${v.toLocaleString(undefined, { maximumFractionDigits: 2, minimumFractionDigits: 2 })}`;
}

export function fmtPct(v: number | null | undefined, digits = 1): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return `${(v * 100).toFixed(digits)}%`;
}

export function fmtPp(v: number | null | undefined, digits = 1): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  const sign = v > 0 ? "+" : "";
  return `${sign}${(v * 100).toFixed(digits)}pp`;
}

export function fmtPrice(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return `${Math.round(v * 100)}c`;
}

export function pnlColor(v: number | null | undefined): string {
  if (v === null || v === undefined) return "text-muted";
  if (v > 0) return "text-good";
  if (v < 0) return "text-bad";
  return "text-muted";
}

export function regimeColor(label: string | null | undefined): string {
  switch (label) {
    case "SHARP": return "text-good";
    case "NORMAL": return "text-accent";
    case "UNCERTAIN": return "text-warn";
    case "BLOCKED": return "text-bad";
    default: return "text-muted";
  }
}

export function severityColor(sev: string | null | undefined): string {
  switch (sev) {
    case "critical": return "border-bad text-bad";
    case "high": return "border-warn text-warn";
    case "medium": return "border-accent text-accent";
    default: return "border-border text-muted";
  }
}

/** Compact USD for volume/depth columns: $1.2k, $340, $18.4M. */
export function fmtCompactUsd(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  const abs = Math.abs(v);
  if (abs >= 1_000_000) return `$${(v / 1_000_000).toFixed(1)}M`;
  if (abs >= 1_000) return `$${(v / 1_000).toFixed(1)}k`;
  return `$${v.toFixed(0)}`;
}

export function fmtInt(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return Math.round(v).toLocaleString();
}

/** Colour a traded-volume cell: dim when nothing has printed at all. */
export function volumeColor(v: number | null | undefined, thin: boolean | null | undefined): string {
  if (v === null || v === undefined || v === 0) return "text-muted";
  return thin ? "text-warn" : "text-text";
}

export function fmtAge(iso: string | null | undefined, now: number = Date.now()): string {
  if (!iso) return "never";
  const ms = now - new Date(iso).getTime();
  if (!Number.isFinite(ms)) return "—";
  const min = Math.floor(ms / 60000);
  if (min < 1) return "just now";
  if (min < 60) return `${min}m ago`;
  const h = Math.floor(min / 60);
  if (h < 48) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}
