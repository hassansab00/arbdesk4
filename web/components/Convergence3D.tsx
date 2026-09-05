"use client";

import { useMemo, useState } from "react";
import { Empty } from "./charts";
import { fmtTemp, type Unit } from "@/lib/units";

/**
 * The forecast funnel, in three real dimensions.
 *
 * WHY 3D HERE AND NOWHERE ELSE. Almost nothing deserves a third axis, and a
 * chart that spends one for decoration is worse than a flat one. This has
 * three genuinely independent variables and the shape they make is the point:
 *
 *   depth   lead days, 7 at the back to 0 at the front
 *   height  temperature
 *   width   the resolution day
 *
 * One ribbon per day, each walking from what the model said a week out to
 * what it said the morning of. A good model's ribbons narrow toward the
 * actual as they come forward; a bad one's wander, and a BIASED one's run
 * parallel to the truth and never touch it. Those three failures look
 * identical in a table of mean absolute error, and completely different here.
 *
 * The bucket boundaries are drawn as horizontal planes because that is what
 * the market actually pays on: a forecast is not "22.4 C", it is "the 22-23
 * bucket", and a 0.6 C error that crosses a line loses while a 0.9 C error
 * inside one wins.
 *
 * Drawn back-to-front so nearer days occlude farther ones, which is the only
 * depth cue an SVG gets.
 */

export interface ConvergencePoint {
  for_date: string;
  lead_days: number;
  forecast_max_c: number;
  observed_max_c: number | null;
  model: string;
  is_past: boolean;
}

const MODEL_COLOR: Record<string, string> = {
  nws: "var(--accent, #4da3ff)",
  open_meteo_forecast: "#f0a35e",
  open_meteo_best_match: "#f0a35e",
  ad4: "#7ee081",
};
const FALLBACK = ["#4da3ff", "#f0a35e", "#7ee081", "#c792ea", "#ff6b8a"];

function colorFor(model: string, all: string[]): string {
  return MODEL_COLOR[model] ?? FALLBACK[all.indexOf(model) % FALLBACK.length];
}

export default function Convergence3D({
  points,
  bands,
  unit,
  height = 420,
}: {
  points: ConvergencePoint[];
  /** Bucket edges in Celsius, ascending. Drawn as planes. */
  bands?: number[];
  unit: Unit | null | undefined;
  height?: number;
}) {
  // Depth drifts RIGHT as days do, so a large yaw makes the two axes read as
  // one: day N at lead 7 lands under day N+4 at lead 0. Keep the horizontal
  // component small and spend the depth cue on the vertical instead.
  const [yaw, setYaw] = useState(0.3);
  const [tilt, setTilt] = useState(0.62);
  // A month of ribbons is not a chart, it is a hedge. Ten days is what fits
  // before the lines stop being separable, and the window is movable.
  const [daysBack, setDaysBack] = useState(10);

  const model = useMemo(() => {
    if (!points.length) return null;

    const allDays = Array.from(new Set(points.map((p) => p.for_date))).sort();
    const today = new Date().toISOString().slice(0, 10);
    const future = allDays.filter((d) => d >= today);
    const past = allDays.filter((d) => d < today).slice(-daysBack);
    const days = [...past, ...future];
    const keep = new Set(days);
    const shown = points.filter((p) => keep.has(p.for_date));
    const models = Array.from(new Set(shown.map((p) => p.model))).sort();
    const leads = Array.from(new Set(shown.map((p) => p.lead_days))).sort((a, b) => b - a);

    const temps = shown.flatMap((p) =>
      [p.forecast_max_c, p.observed_max_c].filter((v): v is number => v !== null)
    );
    let tLo = Math.min(...temps);
    let tHi = Math.max(...temps);
    if (!Number.isFinite(tLo) || tHi === tLo) { tLo -= 1; tHi += 1; }
    const pad = (tHi - tLo) * 0.12;
    tLo -= pad; tHi += pad;

    return { days, models, leads, tLo, tHi, maxLead: Math.max(...leads, 1),
             visible: shown, nPast: past.length, nFuture: future.length };
  }, [points, daysBack]);

  if (!points.length || !model) {
    return (
      <Empty height={height}>
        No forecast series yet. This draws what each model said at each lead against what
        happened — it needs weather_forecasts for a day and, for the actual, that day to have
        settled into the data bank.
      </Empty>
    );
  }

  const { days, models, leads, tLo, tHi, maxLead, visible, nPast, nFuture } = model;

  const W = 760;
  const H = height;
  const PAD_L = 58, PAD_B = 56, PAD_T = 20;

  // World -> screen. depth is lead (0 near, maxLead far), width is day index,
  // height is temperature. A cabinet projection: cheap, honest, and it keeps
  // vertical distances readable, which matters because the vertical axis is
  // the one carrying degrees.
  const spanX = W - PAD_L - 30;
  const spanY = H - PAD_T - PAD_B;
  const dayStep = days.length > 1 ? spanX / (days.length - 1 + maxLead * yaw * 0.9) : 0;
  const leadDX = dayStep * yaw * 0.9;
  const leadDY = spanY * tilt * 0.11;

  const px = (dayIdx: number, lead: number) => PAD_L + dayIdx * dayStep + lead * leadDX;
  const py = (temp: number, lead: number) =>
    H - PAD_B - ((temp - tLo) / (tHi - tLo)) * (spanY - maxLead * leadDY) - lead * leadDY;

  // Back to front: the largest lead is farthest away.
  const orderedLeads = [...leads].sort((a, b) => b - a);

  const byDayModel = new Map<string, ConvergencePoint[]>();
  for (const p of visible) {
    const k = `${p.for_date}|${p.model}`;
    if (!byDayModel.has(k)) byDayModel.set(k, []);
    byDayModel.get(k)!.push(p);
  }

  const observedByDay = new Map<string, number>();
  for (const p of visible) {
    if (p.observed_max_c !== null) observedByDay.set(p.for_date, p.observed_max_c);
  }

  const tempTicks: number[] = [];
  {
    const step = Math.max(1, Math.round((tHi - tLo) / 5));
    for (let v = Math.ceil(tLo / step) * step; v <= tHi; v += step) tempTicks.push(v);
  }

  return (
    <div>
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" style={{ maxHeight: H * 1.4 }} role="img"
           aria-label="Forecast convergence by lead day, temperature and resolution day">
        {/* bucket planes: what the market actually pays on */}
        {(bands ?? []).filter((b) => b > tLo && b < tHi).map((b) => (
          <polygon
            key={`band${b}`}
            points={[
              [px(0, maxLead), py(b, maxLead)],
              [px(days.length - 1, maxLead), py(b, maxLead)],
              [px(days.length - 1, 0), py(b, 0)],
              [px(0, 0), py(b, 0)],
            ].map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(" ")}
            fill="var(--chart-grid, #1a2030)"
            fillOpacity={0.5}
            stroke="var(--chart-axis, #8a93a6)"
            strokeOpacity={0.25}
            strokeDasharray="2 3"
          />
        ))}

        {/* temperature gridlines on the near face */}
        {tempTicks.map((v) => (
          <g key={`t${v}`}>
            <line x1={px(0, 0)} y1={py(v, 0)} x2={px(days.length - 1, 0)} y2={py(v, 0)}
                  stroke="var(--chart-grid, #1a2030)" />
            <text x={PAD_L - 8} y={py(v, 0) + 3} textAnchor="end" fontSize="9"
                  fill="var(--chart-axis, #8a93a6)">
              {fmtTemp(v, unit, 0)}
            </text>
          </g>
        ))}

        {/* the depth axis, so "back" is legible as a lead and not just far away */}
        {orderedLeads.map((lead) => (
          <text key={`l${lead}`} x={px(days.length - 1, lead) + 6} y={py(tLo, lead) - 2}
                fontSize="8" fill="var(--chart-axis, #8a93a6)">
            {lead === 0 ? "same day" : `−${lead}d`}
          </text>
        ))}

        {/* one ribbon per day per model, back to front */}
        {days.map((day, di) => {
          const actual = observedByDay.get(day);
          return (
            <g key={day}>
              {models.map((m) => {
                const series = (byDayModel.get(`${day}|${m}`) ?? [])
                  .slice()
                  .sort((a, b) => b.lead_days - a.lead_days);
                if (series.length < 1) return null;
                const path = series
                  .map((p, i) => `${i === 0 ? "M" : "L"}${px(di, p.lead_days).toFixed(1)},${py(p.forecast_max_c, p.lead_days).toFixed(1)}`)
                  .join(" ");
                const c = colorFor(m, models);
                return (
                  <g key={m}>
                    <path d={path} fill="none" stroke={c} strokeWidth={1.6}
                          strokeOpacity={0.9} strokeLinejoin="round" />
                    {series.map((p) => (
                      <circle key={`${p.lead_days}`} cx={px(di, p.lead_days)}
                              cy={py(p.forecast_max_c, p.lead_days)} r={p.lead_days === 0 ? 3 : 1.8}
                              fill={c} fillOpacity={0.95}>
                        <title>
                          {`${day} · ${m} · ${p.lead_days === 0 ? "same day" : `${p.lead_days}d out`}`}
                          {`\nforecast ${fmtTemp(p.forecast_max_c, unit, 1)}`}
                          {actual !== undefined ? `\nactual   ${fmtTemp(actual, unit, 1)}` : "\nnot settled yet"}
                          {actual !== undefined ? `\nerror    ${(actual - p.forecast_max_c >= 0 ? "+" : "")}${(actual - p.forecast_max_c).toFixed(1)} °C` : ""}
                        </title>
                      </circle>
                    ))}
                  </g>
                );
              })}

              {/* What actually happened, at the near face - and the GAP from
                  the last thing each model said to it. The bar alone leaves
                  the reader to measure the miss by eye across a projection,
                  which is exactly where a 0.4 C error and a 2 C error stop
                  looking different. */}
              {actual !== undefined && (
                <g>
                  {models.map((m) => {
                    const last = (byDayModel.get(`${day}|${m}`) ?? [])
                      .slice().sort((a, b) => a.lead_days - b.lead_days)[0];
                    if (!last) return null;
                    return (
                      <line key={`miss${m}`}
                            x1={px(di, 0)} x2={px(di, 0)}
                            y1={py(last.forecast_max_c, 0)} y2={py(actual, 0)}
                            stroke={colorFor(m, models)} strokeOpacity={0.45}
                            strokeWidth={1} strokeDasharray="1 2" />
                    );
                  })}
                  <line x1={px(di, 0) - 10} x2={px(di, 0) + 10} y1={py(actual, 0)} y2={py(actual, 0)}
                        stroke="var(--good, #7ee081)" strokeWidth={2.6} />
                  <title>{`${day} settled at ${fmtTemp(actual, unit, 1)}`}</title>
                </g>
              )}

              <text x={px(di, 0)} y={H - PAD_B + 14} textAnchor="middle" fontSize="8"
                    fill="var(--chart-axis, #8a93a6)"
                    transform={`rotate(-35 ${px(di, 0)} ${H - PAD_B + 14})`}>
                {day.slice(5)}
              </text>
            </g>
          );
        })}

        <text x={14} y={PAD_T + 2} fontSize="9" fill="var(--chart-axis, #8a93a6)"
              transform={`rotate(-90 14 ${PAD_T + 2})`} textAnchor="end">
          temperature
        </text>
        <text x={W - 30} y={H - 6} textAnchor="end" fontSize="9" fill="var(--chart-axis, #8a93a6)">
          resolution day →   (depth = days before it)
        </text>
      </svg>

      <div className="mt-2 flex flex-wrap items-center gap-4 text-[11px] text-muted">
        <label className="flex items-center gap-2">
          rotate
          <input type="range" min={0.05} max={1.4} step={0.01} value={yaw}
                 onChange={(e) => setYaw(Number(e.target.value))} className="w-28" />
        </label>
        <label className="flex items-center gap-2">
          days back
          <input type="range" min={3} max={30} step={1} value={daysBack}
                 onChange={(e) => setDaysBack(Number(e.target.value))} className="w-24" />
          <span className="tabular-nums">{nPast}</span>
        </label>
        <label className="flex items-center gap-2">
          tilt
          <input type="range" min={0} max={1} step={0.01} value={tilt}
                 onChange={(e) => setTilt(Number(e.target.value))} className="w-28" />
        </label>
        <span className="flex items-center gap-3">
          {models.map((m) => (
            <span key={m} className="flex items-center gap-1">
              <span className="inline-block h-2 w-3 rounded-sm"
                    style={{ background: colorFor(m, models) }} />
              {m}
            </span>
          ))}
          <span className="flex items-center gap-1">
            <span className="inline-block h-[3px] w-3" style={{ background: "var(--good, #7ee081)" }} />
            what happened
          </span>
          {nFuture > 0 && (
            <span className="text-muted">
              · {nFuture} day{nFuture === 1 ? "" : "s"} ahead, not settled
            </span>
          )}
        </span>
      </div>
    </div>
  );
}
