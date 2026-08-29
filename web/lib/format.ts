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
