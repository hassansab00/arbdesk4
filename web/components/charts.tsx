"use client";

import { useId } from "react";

/**
 * Inline-SVG chart primitives.
 *
 * No charting library: the desk already draws its live temperature chart this
 * way, nothing extra ships to the browser, and every colour comes from the
 * same CSS variables as the rest of the page, so the charts theme with it
 * instead of against it.
 *
 * The rule these all follow is that a chart must not lie by omission. An axis
 * label always names a value the plot actually reaches, an empty series says
 * so rather than drawing an empty box, and nothing is scaled to a maximum
 * that is not shown.
 */

const AXIS = "var(--chart-axis, #8a93a6)";
const GRID = "var(--chart-grid, #1a2030)";

function niceTicks(lo: number, hi: number, count = 4): number[] {
  if (!Number.isFinite(lo) || !Number.isFinite(hi) || hi <= lo) return [lo];
  const raw = (hi - lo) / count;
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) ?? mag * 10;
  const out: number[] = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(Number(v.toFixed(10)));
  return out.length ? out : [lo, hi];
}

export function Empty({ children, height = 120 }: { children: React.ReactNode; height?: number }) {
  return (
    <div
      className="flex items-center justify-center rounded border border-dashed border-border px-4 text-center text-[11px] leading-relaxed text-muted"
      style={{ height }}
    >
      {children}
    </div>
  );
}

/* ------------------------------------------------------------------ line -- */

export interface Series {
  label: string;
  color: string;
  points: Array<{ x: number; y: number }>;
  /** Draw a soft fill under the line - for one prominent series, not all. */
  fill?: boolean;
  dashed?: boolean;
}

export function LineChart({
  series, height = 200, yLabel, xTickFormat, yTickFormat, zeroLine,
}: {
  series: Series[];
  height?: number;
  yLabel?: string;
  xTickFormat?: (x: number) => string;
  yTickFormat?: (y: number) => string;
  zeroLine?: boolean;
}) {
  const id = useId();
  const pts = series.flatMap((s) => s.points);
  if (pts.length < 2) return <Empty height={height}>Not enough points to draw a line yet.</Empty>;

  const W = 720, PAD_L = 48, PAD_R = 12, PAD_T = 10, PAD_B = 24;
  const H = height;
  const xs = pts.map((p) => p.x), ys = pts.map((p) => p.y);
  let lo = Math.min(...ys), hi = Math.max(...ys);
  if (zeroLine) { lo = Math.min(lo, 0); hi = Math.max(hi, 0); }
  if (hi === lo) { hi += 1; lo -= 1; }
  const pad = (hi - lo) * 0.08;
  lo -= pad; hi += pad;
  const x0 = Math.min(...xs), x1 = Math.max(...xs);

  const X = (v: number) => PAD_L + ((v - x0) / (x1 - x0 || 1)) * (W - PAD_L - PAD_R);
  const Y = (v: number) => H - PAD_B - ((v - lo) / (hi - lo)) * (H - PAD_T - PAD_B);

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full" style={{ maxHeight: H * 1.6 }} role="img">
      {niceTicks(lo, hi).map((v) => (
        <g key={v}>
          <line x1={PAD_L} x2={W - PAD_R} y1={Y(v)} y2={Y(v)} stroke={GRID} />
          <text x={PAD_L - 5} y={Y(v) + 3} textAnchor="end" fontSize="9" fill={AXIS}>
            {yTickFormat ? yTickFormat(v) : v.toFixed(1)}
          </text>
        </g>
      ))}
      {zeroLine && lo < 0 && hi > 0 && (
        <line x1={PAD_L} x2={W - PAD_R} y1={Y(0)} y2={Y(0)} stroke={AXIS} strokeWidth={0.75} strokeDasharray="4 3" />
      )}
      {[x0, (x0 + x1) / 2, x1].map((v, i) => (
        <text key={i} x={X(v)} y={H - 6} textAnchor={i === 0 ? "start" : i === 2 ? "end" : "middle"} fontSize="9" fill={AXIS}>
          {xTickFormat ? xTickFormat(v) : String(Math.round(v))}
        </text>
      ))}
      {yLabel && (
        <text x={10} y={PAD_T + 8} fontSize="9" fill={AXIS}>{yLabel}</text>
      )}

      {series.map((s, si) => {
        const d = s.points
          .slice()
          .sort((a, b) => a.x - b.x)
          .map((p, i) => `${i === 0 ? "M" : "L"}${X(p.x).toFixed(1)},${Y(p.y).toFixed(1)}`)
          .join(" ");
        const base = Y(Math.max(lo, 0));
        return (
          <g key={s.label}>
            {s.fill && (
              <path
                d={`${d} L${X(s.points[s.points.length - 1].x).toFixed(1)},${base} L${X(s.points[0].x).toFixed(1)},${base} Z`}
                fill={s.color}
                opacity={0.12}
              />
            )}
            <path
              d={d}
              fill="none"
              stroke={s.color}
              strokeWidth={1.6}
              strokeDasharray={s.dashed ? "5 3" : undefined}
              className="chart-draw"
              style={{ animationDelay: `${si * 140}ms` }}
            />
            {/* the endpoint, emphasised: the value people actually read */}
            <circle cx={X(s.points[s.points.length - 1].x)} cy={Y(s.points[s.points.length - 1].y)} r={2.6} fill={s.color} />
          </g>
        );
      })}
      <title>{id}</title>
    </svg>
  );
}

/* ------------------------------------------------------------- histogram -- */

export function Histogram({
  bins, height = 180, highlight, xTickFormat, colorFor,
}: {
  bins: Array<{ label: string; value: number; hint?: string }>;
  height?: number;
  highlight?: string;
  xTickFormat?: (label: string) => string;
  colorFor?: (b: { label: string; value: number }, i: number) => string;
}) {
  if (!bins.length) return <Empty height={height}>Nothing to plot yet.</Empty>;
  const max = Math.max(...bins.map((b) => b.value), 1e-9);
  return (
    <div className="flex items-end gap-[3px]" style={{ height }}>
      {bins.map((b, i) => {
        const h = Math.max(1, (b.value / max) * (height - 26));
        const on = highlight === b.label;
        return (
          <div key={b.label} className="flex min-w-0 flex-1 flex-col items-center justify-end gap-1" title={b.hint ?? `${b.label}: ${b.value}`}>
            <span className="font-mono text-[8.5px] text-muted">{b.value > 0 ? fmtBar(b.value) : ""}</span>
            <div
              className="w-full rounded-t chart-grow"
              style={{
                height: h,
                background: colorFor ? colorFor(b, i) : on ? "var(--c-warn)" : "var(--c-accent)",
                opacity: on ? 1 : 0.75,
                animationDelay: `${i * 22}ms`,
              }}
            />
            <span className="truncate font-mono text-[8.5px] text-muted" style={{ maxWidth: "100%" }}>
              {xTickFormat ? xTickFormat(b.label) : b.label}
            </span>
          </div>
        );
      })}
    </div>
  );
}

function fmtBar(v: number): string {
  if (v >= 1000) return `${(v / 1000).toFixed(1)}k`;
  if (v >= 10) return v.toFixed(0);
  if (v >= 1) return v.toFixed(1);
  return v.toFixed(2);
}

/* --------------------------------------------------------------- scatter -- */

export function Scatter({
  points, height = 260, xLabel, yLabel, xTickFormat, yTickFormat, logX,
}: {
  points: Array<{ x: number; y: number; label: string; color: string; r?: number; hint?: string }>;
  height?: number;
  xLabel: string;
  yLabel: string;
  xTickFormat?: (v: number) => string;
  yTickFormat?: (v: number) => string;
  logX?: boolean;
}) {
  if (points.length === 0) return <Empty height={height}>No cities have both figures yet.</Empty>;

  const W = 720, PAD_L = 52, PAD_R = 14, PAD_T = 12, PAD_B = 34;
  const H = height;
  // A log x-axis is the honest choice for traded volume: one busy city
  // otherwise squashes every other city onto the origin.
  const tx = (v: number) => (logX ? Math.log10(Math.max(v, 1)) : v);
  const xs = points.map((p) => tx(p.x)), ys = points.map((p) => p.y);
  let xlo = Math.min(...xs), xhi = Math.max(...xs);
  let ylo = Math.min(...ys), yhi = Math.max(...ys);
  if (xhi === xlo) { xhi += 1; xlo -= 1; }
  if (yhi === ylo) { yhi += 1; ylo -= 1; }
  const yp = (yhi - ylo) * 0.1; ylo -= yp; yhi += yp;
  const xp = (xhi - xlo) * 0.06; xlo -= xp; xhi += xp;

  const X = (v: number) => PAD_L + ((tx(v) - xlo) / (xhi - xlo)) * (W - PAD_L - PAD_R);
  const Y = (v: number) => H - PAD_B - ((v - ylo) / (yhi - ylo)) * (H - PAD_T - PAD_B);

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full" style={{ maxHeight: H * 1.5 }} role="img">
      {niceTicks(ylo, yhi).map((v) => (
        <g key={`y${v}`}>
          <line x1={PAD_L} x2={W - PAD_R} y1={Y(v)} y2={Y(v)} stroke={GRID} />
          <text x={PAD_L - 5} y={Y(v) + 3} textAnchor="end" fontSize="9" fill={AXIS}>
            {yTickFormat ? yTickFormat(v) : v.toFixed(1)}
          </text>
        </g>
      ))}
      {niceTicks(xlo, xhi, 5).map((v) => {
        const real = logX ? Math.pow(10, v) : v;
        return (
          <g key={`x${v}`}>
            <line x1={PAD_L + ((v - xlo) / (xhi - xlo)) * (W - PAD_L - PAD_R)} x2={PAD_L + ((v - xlo) / (xhi - xlo)) * (W - PAD_L - PAD_R)} y1={PAD_T} y2={H - PAD_B} stroke={GRID} />
            <text x={PAD_L + ((v - xlo) / (xhi - xlo)) * (W - PAD_L - PAD_R)} y={H - PAD_B + 12} textAnchor="middle" fontSize="9" fill={AXIS}>
              {xTickFormat ? xTickFormat(real) : real.toFixed(0)}
            </text>
          </g>
        );
      })}
      <text x={W / 2} y={H - 4} textAnchor="middle" fontSize="9" fill={AXIS}>{xLabel}</text>
      <text x={10} y={PAD_T + 6} fontSize="9" fill={AXIS}>{yLabel}</text>

      {points.map((p, i) => (
        <g key={p.label} className="chart-pop" style={{ animationDelay: `${i * 18}ms` }}>
          <circle cx={X(p.x)} cy={Y(p.y)} r={p.r ?? 5} fill={p.color} opacity={0.75} stroke={p.color} />
          <text x={X(p.x)} y={Y(p.y) - (p.r ?? 5) - 3} textAnchor="middle" fontSize="8.5" fill={AXIS}>
            {p.label}
          </text>
          <title>{p.hint ?? p.label}</title>
        </g>
      ))}
    </svg>
  );
}
