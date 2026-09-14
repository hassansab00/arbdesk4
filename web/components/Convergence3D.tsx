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
 *   depth   lead days, counting DOWN left to right - a week out at the
 *           back-left, zero (the day itself) at the front-right
 *   height  temperature
 *   width   the resolution day
 *
 * THE COUNTDOWN RUNS THE WAY YOU READ. Depth used to recede down-LEFT, so a
 * ribbon walked right to left as its day approached, while the day axis under
 * it ran left to right. Two time axes pointing opposite ways in one picture is
 * a puzzle, not a chart. Both run left to right now: the lead counts 7, 6,
 * 5 ... 1, 0, and what actually happened is drawn AT zero, the end of the line.
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
 * Drawn latest day first, so that where two ribbons overlap the nearer one -
 * the lower lead - is painted on top. That is the only depth cue an SVG gets,
 * and it reverses with the axis.
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
  // one: day N at lead 0 lands over day N+4 at lead 7. Keep the horizontal
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

  // World -> screen. Depth is the lead, and it is spent as a COUNTDOWN: the
  // largest lead sits at the day's own slot, up and to the left, and each day
  // closer steps right and down until lead 0 - the day itself - is the
  // front-right end of the ribbon. Width is the day index, height is
  // temperature. A cabinet projection: cheap, honest, and it keeps vertical
  // distances readable, which matters because that axis carries the degrees.
  const spanX = W - PAD_L - 30;
  const spanY = H - PAD_T - PAD_B;
  const dayStep = days.length > 1 ? spanX / (days.length - 1 + maxLead * yaw * 0.9) : 0;
  const leadDX = dayStep * yaw * 0.9;
  const leadDY = spanY * tilt * 0.11;

  // (maxLead - lead), not lead: that one term is the whole axis flip. The
  // horizontal extent is unchanged - the drawing still ends at PAD_L + spanX -
  // because the shear is re-anchored, not added.
  const px = (dayIdx: number, lead: number) =>
    PAD_L + dayIdx * dayStep + (maxLead - lead) * leadDX;
  const py = (temp: number, lead: number) =>
    H - PAD_B - ((temp - tLo) / (tHi - tLo)) * (spanY - maxLead * leadDY) - lead * leadDY;

  // Largest lead first: it is both the farthest away and now the leftmost.
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

  /**
   * WHAT AM I LOOKING AT. The picture shows the shape of the error; these
   * say what the shape MEANS, per model, in the same window the chart draws.
   *
   * Three failures look different here and identical in a table of mean
   * error, so each gets its own word rather than one number:
   *   converging  the miss shrinks as the day approaches - what you want
   *   parallel    the miss is the same size a week out and the morning of,
   *               and always the same SIGN. That is a bias, and a bias is a
   *               correction you can apply rather than a model you distrust.
   *   wandering   the miss shrinks but the sign keeps flipping - real noise,
   *               and nothing to correct.
   */
  const bandOf = (t: number): number => {
    const edges = (bands ?? []).slice().sort((a, b) => a - b);
    let i = 0;
    while (i < edges.length && t >= edges[i]) i++;
    return i;
  };

  const verdicts = models.map((m) => {
    const rows = visible.filter((p) => p.model === m && p.observed_max_c !== null);
    const settledDays = Array.from(new Set(rows.map((r) => r.for_date)));
    let far = 0, near = 0, nFar = 0, nNear = 0, signSum = 0, hits = 0, calls = 0;
    for (const d of settledDays) {
      const series = rows.filter((r) => r.for_date === d).sort((a, b) => a.lead_days - b.lead_days);
      if (!series.length) continue;
      const actual = series[0].observed_max_c as number;
      const nearest = series[0];
      const farthest = series[series.length - 1];
      near += Math.abs(actual - nearest.forecast_max_c); nNear++;
      far  += Math.abs(actual - farthest.forecast_max_c); nFar++;
      signSum += Math.sign(actual - nearest.forecast_max_c);
      calls++;
      if (bands && bands.length && bandOf(nearest.forecast_max_c) === bandOf(actual)) hits++;
    }
    const nearMae = nNear ? near / nNear : null;
    const farMae  = nFar  ? far  / nFar  : null;
    const consistent = calls ? Math.abs(signSum) / calls : 0;
    let verdict = "not enough settled days";
    if (calls >= 3 && nearMae !== null && farMae !== null) {
      if (nearMae < farMae * 0.7) verdict = "converging";
      else if (consistent > 0.7) verdict = signSum > 0 ? "parallel — runs cold" : "parallel — runs hot";
      else verdict = "wandering";
    }
    return { model: m, nearMae, farMae, verdict, calls,
             bucketHitPct: bands && bands.length && calls ? (hits / calls) * 100 : null };
  });

  const tempTicks: number[] = [];
  {
    const step = Math.max(1, Math.round((tHi - tLo) / 5));
    for (let v = Math.ceil(tLo / step) * step; v <= tHi; v += step) tempTicks.push(v);
  }

  const VERDICT_TONE: Record<string, string> = {
    converging: "border-good/40 bg-good/10 text-good",
    wandering: "border-warn/40 bg-warn/10 text-warn",
  };
  const toneFor = (v: string) =>
    VERDICT_TONE[v] ?? (v.startsWith("parallel") ? "border-bad/40 bg-bad/10 text-bad"
                                                 : "border-border bg-panel2 text-muted");

  return (
    <div>
      {/* WHAT THE SHAPE MEANS, before the shape. A 3D chart that a reader has
          to decode is a 3D chart that gets ignored, so the reading is stated
          in words first and the picture is there to check it against. */}
      <div className="mb-2 flex flex-wrap gap-2">
        {verdicts.map((v) => (
          <div key={v.model}
               className={`flex items-center gap-2 rounded border px-2 py-1 text-[11px] ${toneFor(v.verdict)}`}>
            <span className="inline-block h-2 w-3 rounded-sm shrink-0"
                  style={{ background: colorFor(v.model, models) }} />
            <span className="font-semibold">{v.model}</span>
            <span className="opacity-90">{v.verdict}</span>
            {v.farMae !== null && v.nearMae !== null && (
              <span className="tabular-nums opacity-80">
                {v.farMae.toFixed(1)} → {v.nearMae.toFixed(1)} °C miss
              </span>
            )}
            {v.bucketHitPct !== null && (
              <span className="tabular-nums opacity-80">
                · right bucket {v.bucketHitPct.toFixed(0)}%
              </span>
            )}
            <span className="opacity-60">({v.calls}d)</span>
          </div>
        ))}
      </div>
      <p className="mb-2 max-w-3xl text-[11px] leading-relaxed text-muted">
        <strong className="text-text">How to read it.</strong> Follow one coloured line{" "}
        <strong className="text-text">left to right</strong>. It starts a week out and counts
        down — 7, 6, 5 … 1 — to <strong className="text-text">zero</strong>, the day itself, at
        the right-hand end of the line. It should walk toward the green bar, which is what the
        day actually did — that is <em>converging</em>. A line that stays the same distance from
        the bar the whole way is <em>parallel</em>: a bias, and a bias is a correction you can
        apply. A line that jumps around while getting closer is <em>wandering</em>: noise, and
        nothing to correct. The <strong className="text-text">ring at zero</strong> is the only
        thing that got paid — filled and green if that model&apos;s last call landed in the same
        bucket the day settled in, hollow and red if it missed by a line. The grey planes are
        those bucket lines.
      </p>
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

        {/* Temperature gridlines on the BACK face. The countdown ends at the
            front, so the front face has moved right and taken its left edge
            with it; the back face is the one whose edge is PAD_L now. The
            bucket planes carry the same scale forward to the near face, which
            is where the green bars are actually read. */}
        {tempTicks.map((v) => (
          <g key={`t${v}`}>
            <line x1={px(0, maxLead)} y1={py(v, maxLead)}
                  x2={px(days.length - 1, maxLead)} y2={py(v, maxLead)}
                  stroke="var(--chart-grid, #1a2030)" />
            <text x={PAD_L - 8} y={py(v, maxLead) + 3} textAnchor="end" fontSize="9"
                  fill="var(--chart-axis, #8a93a6)">
              {fmtTemp(v, unit, 0)}
            </text>
          </g>
        ))}

        {/* THE COUNTDOWN, drawn as an axis instead of left to be inferred from
            where the lines happen to start. It runs along the bottom edge of
            the FIRST day's ribbon - the one wedge of the picture nothing else
            occupies - from the farthest lead at the left down to zero at the
            right, where what actually happened is marked in the same green as
            the bars. Labels thin out as the rotate slider squeezes the axis;
            the far end and zero always survive, because those two are what
            make it a countdown rather than a smear. */}
        <g>
          <line x1={px(0, maxLead)} y1={py(tLo, maxLead)} x2={px(0, 0)} y2={py(tLo, 0)}
                stroke="var(--chart-axis, #8a93a6)" strokeOpacity={0.45} />
          {orderedLeads.filter((lead) => lead > 0).map((lead) => (
            <g key={`l${lead}`}>
              <line x1={px(0, lead)} y1={py(tLo, lead)}
                    x2={px(0, lead) - 3} y2={py(tLo, lead) + 3}
                    stroke="var(--chart-axis, #8a93a6)" strokeOpacity={0.45} />
              {(lead === maxLead || leadDX >= 14) && (
                <text x={px(0, lead) + 2} y={py(tLo, lead) + 11} fontSize="8"
                      fill="var(--chart-axis, #8a93a6)">
                  {lead}
                </text>
              )}
            </g>
          ))}
          {/* Zero. The only label on this axis naming a measurement rather
              than a lead: at zero the forecasting stops and the day is simply
              what it was. Drawn whether or not the data carries a lead-0 row,
              because the green bars are plotted at zero either way. */}
          <circle cx={px(0, 0)} cy={py(tLo, 0)} r={2.5} fill="var(--good, #7ee081)" />
          <text x={px(0, 0) + 5} y={py(tLo, 0) - 3} fontSize="8.5" fontWeight="600"
                fill="var(--good, #7ee081)">
            0 · what happened
          </text>
        </g>

        {/* One ribbon per day per model. Latest day FIRST, so the earliest -
            whose near end now reaches furthest right, over the later days' far
            ends - is painted last and therefore on top. Near occludes far. */}
        {days.map((day, di) => ({ day, di })).reverse().map(({ day, di }) => {
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
                  {/* THE ONLY THING THE MARKET PAID ON. Close is not a
                      result: a 0.6 C miss across a bucket line loses the
                      whole ticket and a 0.9 C miss inside one wins it. So
                      the last call of each model gets a ring - filled if it
                      landed in the bucket the day settled in, hollow if it
                      did not - and that ring, not the length of the dashed
                      line, is what to read. */}
                  {(bands ?? []).length > 0 && models.map((m) => {
                    const last = (byDayModel.get(`${day}|${m}`) ?? [])
                      .slice().sort((a, b) => a.lead_days - b.lead_days)[0];
                    if (!last) return null;
                    const hit = bandOf(last.forecast_max_c) === bandOf(actual);
                    return (
                      <circle key={`hit${m}`} cx={px(di, 0)} cy={py(last.forecast_max_c, 0)} r={5}
                              fill={hit ? colorFor(m, models) : "none"}
                              fillOpacity={hit ? 0.45 : 0}
                              stroke={hit ? "var(--good, #7ee081)" : "var(--bad, #ff6b8a)"}
                              strokeWidth={1.4}>
                        <title>{`${m} · last call ${fmtTemp(last.forecast_max_c, unit, 1)} · settled ${fmtTemp(actual, unit, 1)} · ${hit ? "SAME bucket — this one paid" : "DIFFERENT bucket — this one lost"}`}</title>
                      </circle>
                    );
                  })}
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
          → the day the market resolves      ↘ the countdown: days still to go, down to 0
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
