"use client";

import { useId, useMemo, useRef, useState } from "react";

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
  // HOVER, because a line chart without it is a picture. Every other chart
  // here had a tooltip and this one had `<title>{id}</title>` - the React
  // id, on the whole SVG. Pointing at a line and being told nothing is what
  // "the tooltips show nothing" meant.
  const [hover, setHover] = useState<number | null>(null);
  const pts = series.flatMap((s) => s.points);
  const xsAll = useMemo(
    () => Array.from(new Set(pts.map((p) => p.x))).sort((a, b) => a - b),
    [pts]
  );
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

  // Snap to the nearest real x. Interpolating between points would invent a
  // value the series never had, which is the one thing a readout must not do.
  function nearestX(clientFrac: number): number | null {
    if (!xsAll.length) return null;
    const target = x0 + clientFrac * (x1 - x0);
    let best = xsAll[0];
    for (const v of xsAll) if (Math.abs(v - target) < Math.abs(best - target)) best = v;
    return best;
  }

  const hoverAt = hover === null ? null : hover;
  const readout =
    hoverAt === null
      ? null
      : series
          .map((s) => ({ s, p: s.points.find((q) => q.x === hoverAt) }))
          .filter((r) => r.p !== undefined);

  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      className="w-full"
      style={{ maxHeight: H * 1.6 }}
      role="img"
      onMouseLeave={() => setHover(null)}
      onMouseMove={(e) => {
        const box = (e.currentTarget as SVGSVGElement).getBoundingClientRect();
        const frac = ((e.clientX - box.left) / box.width) * W;
        const inPlot = (frac - PAD_L) / (W - PAD_L - PAD_R);
        setHover(nearestX(Math.min(1, Math.max(0, inPlot))));
      }}
    >
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
      {/* crosshair + readout */}
      {hoverAt !== null && readout && readout.length > 0 && (
        <g pointerEvents="none">
          <line x1={X(hoverAt)} x2={X(hoverAt)} y1={PAD_T} y2={H - PAD_B}
                stroke={AXIS} strokeOpacity={0.5} strokeDasharray="3 3" />
          {readout.map((r) => (
            <circle key={r.s.label} cx={X(hoverAt)} cy={Y(r.p!.y)} r={3.2}
                    fill={r.s.color} stroke="var(--panel, #10141c)" strokeWidth={1} />
          ))}
          {(() => {
            const lines = readout.length + 1;
            const bw = 132, bh = 14 + lines * 12;
            // Flip to the other side near the right edge rather than drawing
            // the box off the plot.
            const left = X(hoverAt) > W - bw - 20;
            const bx = left ? X(hoverAt) - bw - 8 : X(hoverAt) + 8;
            return (
              <g transform={`translate(${bx}, ${PAD_T + 4})`}>
                <rect width={bw} height={bh} rx={3}
                      fill="var(--panel, #10141c)" stroke={GRID} />
                <text x={7} y={13} fontSize="9" fill={AXIS}>
                  {xTickFormat ? xTickFormat(hoverAt) : String(Math.round(hoverAt))}
                </text>
                {readout.map((r, i) => (
                  <g key={r.s.label} transform={`translate(7, ${25 + i * 12})`}>
                    <rect width={7} height={7} y={-6} rx={1.5} fill={r.s.color} />
                    <text x={12} fontSize="9" fill="var(--text, #e6e9ef)">
                      {r.s.label}
                    </text>
                    <text x={bw - 14} fontSize="9" textAnchor="end"
                          fill="var(--text, #e6e9ef)"
                          style={{ fontVariantNumeric: "tabular-nums" }}>
                      {yTickFormat ? yTickFormat(r.p!.y) : r.p!.y.toFixed(2)}
                    </text>
                  </g>
                ))}
              </g>
            );
          })()}
        </g>
      )}
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

/**
 * Keep the first tick for each distinct LABEL. The values are correct; it is
 * the formatting that collides, so dropping a duplicate label is honest where
 * relabelling or re-spacing the axis would not be.
 */
function dedupeByLabel(values: number[], label: (v: number) => string): number[] {
  const seen = new Set<string>();
  const out: number[] = [];
  for (const v of values) {
    const l = label(v);
    if (seen.has(l)) continue;
    seen.add(l);
    out.push(v);
  }
  return out;
}

export function Scatter({
  points, height = 260, xLabel, yLabel, xTickFormat, yTickFormat, logX,
  maxLabels = 40, zoomable = false, diagonal = false,
}: {
  points: Array<{ x: number; y: number; label: string; color: string; r?: number; hint?: string }>;
  height?: number;
  xLabel: string;
  yLabel: string;
  xTickFormat?: (v: number) => string;
  yTickFormat?: (v: number) => string;
  logX?: boolean;
  /**
   * Above this many points, STOP LABELLING THEM.
   *
   * Actual-against-predicted draws one dot per settled city-day - 2,134 of
   * them on a desk with a few weeks of history - and every dot was carrying
   * its city name. The result is a solid mat of overlapping words with the
   * data somewhere underneath it: the chart got less readable with every day
   * the desk ran, which is exactly backwards. Past this count the labels come
   * off and the hovered point gets one instead.
   */
  maxLabels?: number;
  /** Wheel/drag/buttons to zoom and pan. Off by default - a chart of 37
   *  cities does not need it and the controls are clutter. */
  zoomable?: boolean;
  /** Draw y = x. On actual-against-predicted it is the whole reference and
   *  the chart was drawn without it. */
  diagonal?: boolean;
}) {
  // Hooks before the early return: React requires the same hook order every
  // render, and `points.length === 0` is a normal state here, not an error.
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const [hover, setHover] = useState<number | null>(null);
  const dragRef = useRef<{ x: number; y: number; px: number; py: number } | null>(null);

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
  // Padding must not invent an impossible domain. Book depth, traded volume
  // and every other count on this desk are non-negative, and a y-axis reading
  // "-$1" under a chart of dollars quoted is not a rounding quirk - it is the
  // chart claiming something that cannot happen. Only ever clamp toward zero,
  // so a series that genuinely goes negative keeps its full range.
  if (ys.every((v) => v >= 0) && ylo < 0) ylo = 0;
  if (!logX && xs.every((v) => v >= 0) && xlo < 0) xlo = 0;

  // Zoom narrows the DOMAIN around its centre rather than scaling the drawing,
  // so the axis labels keep telling the truth about what is on screen.
  if (zoomable && (zoom !== 1 || pan.x !== 0 || pan.y !== 0)) {
    const cx = (xlo + xhi) / 2 + pan.x * (xhi - xlo);
    const cy = (ylo + yhi) / 2 + pan.y * (yhi - ylo);
    const hw = (xhi - xlo) / (2 * zoom);
    const hh = (yhi - ylo) / (2 * zoom);
    xlo = cx - hw; xhi = cx + hw;
    ylo = cy - hh; yhi = cy + hh;
  }

  const X = (v: number) => PAD_L + ((tx(v) - xlo) / (xhi - xlo)) * (W - PAD_L - PAD_R);
  const Y = (v: number) => H - PAD_B - ((v - ylo) / (yhi - ylo)) * (H - PAD_T - PAD_B);
  const showLabels = points.length <= maxLabels;

  const svg = (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full" style={{ maxHeight: H * 1.5 }} role="img">
      {/* Ticks whose LABELS collide are dropped, not their values: on a
          degenerate axis (every point the same, or a range under one unit)
          niceTicks is right and the formatter is lossy, and two ticks reading
          "$1" at different heights is the chart contradicting itself. */}
      {dedupeByLabel(niceTicks(ylo, yhi), (v) => (yTickFormat ? yTickFormat(v) : v.toFixed(1))).map((v) => (
        <g key={`y${v}`}>
          <line x1={PAD_L} x2={W - PAD_R} y1={Y(v)} y2={Y(v)} stroke={GRID} />
          <text x={PAD_L - 5} y={Y(v) + 3} textAnchor="end" fontSize="9" fill={AXIS}>
            {yTickFormat ? yTickFormat(v) : v.toFixed(1)}
          </text>
        </g>
      ))}
      {dedupeByLabel(niceTicks(xlo, xhi, 5), (v) => {
        const r = logX ? Math.pow(10, v) : v;
        return xTickFormat ? xTickFormat(r) : r.toFixed(0);
      }).map((v) => {
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

      {/* y = x. On a chart of observed against forecast this line IS the
          answer - every dot's distance from it is the miss - and drawing the
          cloud without it left the reader to imagine where perfect was. */}
      {diagonal && (() => {
        const lo = Math.max(xlo, ylo), hi = Math.min(xhi, yhi);
        if (!(hi > lo)) return null;
        return (
          <line x1={X(logX ? Math.pow(10, lo) : lo)} y1={Y(lo)}
                x2={X(logX ? Math.pow(10, hi) : hi)} y2={Y(hi)}
                stroke={AXIS} strokeOpacity={0.55} strokeDasharray="4 3" />
        );
      })()}

      {points.map((p, i) => {
        const cx = X(p.x), cy = Y(p.y);
        if (cx < PAD_L - 8 || cx > W - PAD_R + 8 || cy < PAD_T - 8 || cy > H - PAD_B + 8) return null;
        const isHover = hover === i;
        return (
          // key is the INDEX, not the label. Two thousand settled city-days
          // contain the same city many times over, and duplicate React keys
          // silently drop points from the chart.
          <g key={i} className={showLabels ? "chart-pop" : undefined}
             style={showLabels ? { animationDelay: `${i * 18}ms` } : undefined}
             onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover((h) => (h === i ? null : h))}>
            <circle cx={cx} cy={cy} r={(p.r ?? 5) * (isHover ? 1.6 : 1)}
                    fill={p.color} opacity={isHover ? 1 : 0.7}
                    stroke={isHover ? "#ffffff" : p.color} strokeWidth={isHover ? 1.4 : 1} />
            {(showLabels || isHover) && (
              <text x={cx} y={cy - (p.r ?? 5) - 4} textAnchor="middle" fontSize="8.5"
                    fill={isHover ? "#e6edf6" : AXIS}
                    stroke={isHover ? "#080b12" : undefined} strokeWidth={isHover ? 2.4 : undefined}
                    paintOrder="stroke">
                {p.label}
              </text>
            )}
            <title>{p.hint ?? p.label}</title>
          </g>
        );
      })}
      {!showLabels && (
        <text x={W - PAD_R} y={PAD_T + 8} textAnchor="end" fontSize="8.5" fill={AXIS}>
          {points.length.toLocaleString()} points · hover one for its name
        </text>
      )}
    </svg>
  );

  return zoomable ? (
    <div>
      <div
        onWheel={(e) => setZoom((z) => Math.max(1, Math.min(20, z * (e.deltaY < 0 ? 1.15 : 1 / 1.15))))}
        onPointerDown={(e) => { dragRef.current = { x: e.clientX, y: e.clientY, px: pan.x, py: pan.y };
                                (e.target as Element).setPointerCapture?.(e.pointerId); }}
        onPointerMove={(e) => {
          const d = dragRef.current; if (!d) return;
          const r = (e.currentTarget as HTMLElement).getBoundingClientRect();
          setPan({ x: d.px - ((e.clientX - d.x) / r.width) / zoom,
                   y: d.py + ((e.clientY - d.y) / r.height) / zoom });
        }}
        onPointerUp={() => { dragRef.current = null; }}
        onPointerLeave={() => { dragRef.current = null; }}
        style={{ cursor: dragRef.current ? "grabbing" : zoom > 1 ? "grab" : "crosshair", touchAction: "none" }}
      >
        {svg}
      </div>
      <div className="mt-1 flex items-center gap-1 text-[10px] text-muted">
        <button onClick={() => setZoom((z) => Math.min(20, z * 1.5))}
                className="rounded border border-border bg-panel2 px-2 py-0.5 hover:text-text">+</button>
        <button onClick={() => setZoom((z) => Math.max(1, z / 1.5))}
                className="rounded border border-border bg-panel2 px-2 py-0.5 hover:text-text">−</button>
        <button onClick={() => { setZoom(1); setPan({ x: 0, y: 0 }); }}
                className="rounded border border-border bg-panel2 px-1.5 py-0.5 hover:text-text">reset</button>
        <span className="tabular-nums">{zoom.toFixed(1)}×</span>
        <span className="ml-1">scroll to zoom · drag to pan · hover a dot for its city and day</span>
      </div>
    </div>
  ) : svg;
}
