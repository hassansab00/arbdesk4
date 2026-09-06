"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { forecastProvenance } from "@/components/ForecastValue";
import { fmtTemp, type Unit } from "@/lib/units";
import { heatColor } from "@/lib/heat";
import { REGION_COLOR } from "@/lib/heat";
import { regionFromCity } from "@/lib/region";
import { LAND } from "@/lib/coastline";
import type { CityStats } from "@/lib/types";

/**
 * The desk's 37 cities on a globe, shaded by daylight.
 *
 * WHY A GLOBE AND NOT A MAP. This desk trades the daily MAXIMUM, which is made
 * in the few hours around each city's solar afternoon. Those hours sweep west
 * around the planet, and the single most useful geographic fact at any moment
 * is which cities are in that window right now, which are still climbing
 * toward it, and which are dark and already settled. On a flat map that is a
 * column of numbers. On a lit sphere it is the picture.
 *
 * It also makes correlation visible without computing it: a band of cities
 * under the same weather system sits together and lights up together, and
 * "ten positions across correlated cities is not ten bets" stops being a
 * sentence in a risk doc.
 *
 * IT OPENS ON THE ACTION. The default rotation is not Greenwich or the
 * Atlantic - it is the meridian where local solar time is about 15:00, which
 * is where today's maxima are being made as you look at it. Drag to rotate;
 * the auto-rotation stops the moment you do.
 *
 * THE SOLAR MODEL is the standard low-precision one: declination from the day
 * of year, hour angle from UTC, no equation of time. That is worth a few
 * minutes of terminator position and nothing at all for the purpose here -
 * but the desk's own peak windows come from derived_weather_peak, which is
 * MEASURED per city and month, not from this. The lighting is orientation,
 * not evidence, and nothing on the page is computed from it.
 */

const R_FRAC = 0.44;          // sphere radius as a fraction of the smaller side
const AUTO_DEG_PER_S = 4;

/**
 * What the marker colour means. One globe, three questions - and they are
 * genuinely different maps: the hottest city is often not the one with an edge
 * on the board, and neither is the one whose feed has stopped.
 */
export type GlobeMode = "hotness" | "edge" | "health";

const MODES: Array<{ key: GlobeMode; label: string; hint: string; reading: string }> = [
  { key: "hotness", label: "Hotness", hint: "How far today is from this city's own normal, in standard deviations. The only form comparable between Chicago and Beirut.",
    reading: "Red = far above this city's own normal. A whole continent turning red at once is one weather system, not ten independent bets." },
  { key: "edge",    label: "Best edge", hint: "The largest net edge currently tradeable in this city, after fees. Grey means nothing priced.",
    reading: "Orange = the biggest net edge after fees. Grey = nothing priced there, so there is nothing to take." },
  { key: "health",  label: "Data health", hint: "Is this city's own feed current: a reading in the last three hours, and a forward forecast to price against.",
    reading: "Red = this city's feed has stopped. Its colour in the other two modes is then meaningless, not neutral." },
];

/** Green through amber to red as an edge grows - the same ramp everywhere. */
function edgeColor(pp: number | null | undefined): string {
  if (pp == null || pp <= 0) return "#5b6472";
  if (pp < 0.02) return "#4f7d63";
  if (pp < 0.05) return "#6fa86a";
  if (pp < 0.10) return "#c7b04a";
  return "#e0803a";
}

interface Placed {
  c: CityStats;
  x: number; y: number;      // screen px
  front: boolean;
  lat: number; lon: number;
  /** Local solar hour, for the readout. */
  solar: number;
  /** Inside the measured peak window and the day is not over. */
  live: boolean;
  /** How old this city's own reading is, in hours. */
  ageH: number | null;
  /** Worth a permanent label rather than only a hover. */
  important: boolean;
}

function solarPosition(now: Date) {
  // Day of year, UTC.
  const start = Date.UTC(now.getUTCFullYear(), 0, 0);
  const doy = (now.getTime() - start) / 86400000;
  // Low-precision declination: good to a fraction of a degree.
  const dec = -23.44 * Math.cos(((2 * Math.PI) / 365.24) * (doy + 10));
  const utcHours = now.getUTCHours() + now.getUTCMinutes() / 60;
  // Subsolar longitude: noon UTC puts the sun on the prime meridian.
  const lon = -15 * (utcHours - 12);
  return { dec, lon };
}

/** Local solar time at a longitude, in hours. */
function solarHour(lon: number, sunLon: number) {
  return ((((lon - sunLon) / 15 + 12) % 24) + 24) % 24;
}

export default function Globe({
  cities,
  onPick,
  height = 460,
}: {
  cities: CityStats[];
  onPick?: (cityKey: string) => void;
  height?: number;
}) {
  const canvas = useRef<HTMLCanvasElement | null>(null);
  const wrap = useRef<HTMLDivElement | null>(null);
  const [now, setNow] = useState(() => new Date());
  const sun = useMemo(() => solarPosition(now), [now]);

  // Open on the meridian where it is roughly 15:00 local - where today's
  // maxima are actually being made.
  const [yaw, setYaw] = useState(() => {
    const s = solarPosition(new Date());
    return s.lon + 45; // 15:00 local is 3h = 45 degrees west of the subsolar point
  });
  const [tilt, setTilt] = useState(18);
  const [auto, setAuto] = useState(true);
  const [hover, setHover] = useState<Placed | null>(null);
  const [mode, setMode] = useState<GlobeMode>("hotness");
  const [size, setSize] = useState({ w: 720, h: height });
  const drag = useRef<{ x: number; y: number; yaw: number; tilt: number } | null>(null);

  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 60000);
    return () => clearInterval(t);
  }, []);

  useEffect(() => {
    const el = wrap.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setSize({ w: el.clientWidth, h: height }));
    ro.observe(el);
    setSize({ w: el.clientWidth, h: height });
    return () => ro.disconnect();
  }, [height]);

  useEffect(() => {
    if (!auto) return;
    let raf = 0;
    let last = performance.now();
    const step = (t: number) => {
      const dt = (t - last) / 1000;
      last = t;
      setYaw((y) => y - AUTO_DEG_PER_S * dt);
      raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [auto]);

  const geom = useMemo(() => {
    const cx = size.w / 2;
    const cy = size.h / 2;
    const R = Math.min(size.w, size.h) * R_FRAC;
    return { cx, cy, R };
  }, [size]);

  /** Cities projected to screen, far side flagged rather than dropped. */
  const placed: Placed[] = useMemo(() => {
    const { cx, cy, R } = geom;
    const p0 = (tilt * Math.PI) / 180;
    const l0 = (yaw * Math.PI) / 180;
    const out: Placed[] = [];
    for (const c of cities) {
      if (c.latitude == null || c.longitude == null) continue;
      const phi = (c.latitude * Math.PI) / 180;
      const lam = (c.longitude * Math.PI) / 180;
      const cosC = Math.sin(p0) * Math.sin(phi) + Math.cos(p0) * Math.cos(phi) * Math.cos(lam - l0);
      const x = Math.cos(phi) * Math.sin(lam - l0);
      const y = Math.cos(p0) * Math.sin(phi) - Math.sin(p0) * Math.cos(phi) * Math.cos(lam - l0);
      const ageH = c.observed_at
        ? (Date.now() - new Date(c.observed_at).getTime()) / 3600000
        : null;
      out.push({
        c, front: cosC > 0,
        x: cx + R * x, y: cy - R * y,
        lat: c.latitude, lon: c.longitude,
        solar: solarHour(c.longitude, sun.lon),
        live: c.peak_window_state === "INSIDE" && !c.day_decided,
        ageH,
        important: false,
      });
    }
    // A LABEL ON EVERY CITY IS A LABEL ON NONE. Only the rows a trader would
    // actually look at get a permanent one: the cities making their maximum
    // right now, and the three largest tradeable edges on the board.
    const byEdge = [...out]
      .filter((p) => p.front && (p.c.best_edge_pp ?? 0) > 0)
      .sort((a, b) => (b.c.best_edge_pp ?? 0) - (a.c.best_edge_pp ?? 0))
      .slice(0, 3);
    for (const p of out) {
      p.important = p.front && (p.live || byEdge.includes(p));
    }
    // Far side first so the near side draws over it.
    return out.sort((a, b) => Number(a.front) - Number(b.front));
  }, [cities, geom, yaw, tilt, sun.lon]);

  /** How the desk's day is distributed right now - a headline the table cannot give. */
  const tally = useMemo(() => {
    // Every city lands in exactly one bucket, so the counts add up to the
    // roster. A tally that silently drops a third of the desk is worse than
    // no tally - it reads as a smaller desk rather than a missing case.
    let live = 0, afternoon = 0, climbing = 0, decided = 0, dark = 0, stale = 0;
    for (const p of placed) {
      if (p.ageH != null && p.ageH > 3) stale++;
      if (p.c.day_decided) decided++;
      else if (p.live) live++;
      else if (p.solar >= 12 && p.solar < 19) afternoon++;
      else if (p.solar >= 6) climbing++;
      else dark++;
    }
    return { live, afternoon, climbing, decided, dark, stale, total: placed.length };
  }, [placed]);

  /* ---- the lit sphere ------------------------------------------------- */
  useEffect(() => {
    const el = canvas.current;
    if (!el) return;
    const { cx, cy, R } = geom;
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    el.width = size.w * dpr;
    el.height = size.h * dpr;
    const g = el.getContext("2d");
    if (!g) return;
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, size.w, size.h);

    const p0 = (tilt * Math.PI) / 180;
    const l0 = (yaw * Math.PI) / 180;
    const dec = (sun.dec * Math.PI) / 180;
    const sunLon = (sun.lon * Math.PI) / 180;

    // Shade per pixel by solar elevation: the terminator falls out of the
    // maths rather than being drawn as a curve, so it is correct at every
    // rotation and tilt including the poles.
    const box = Math.ceil(R) * 2 + 2;
    const x0 = Math.floor(cx - R) - 1;
    const y0 = Math.floor(cy - R) - 1;
    const img = g.createImageData(box, box);
    const d = img.data;
    for (let py = 0; py < box; py++) {
      for (let px = 0; px < box; px++) {
        const sx = (x0 + px - cx) / R;
        const sy = (cy - (y0 + py)) / R;
        const rho = Math.hypot(sx, sy);
        const i = (py * box + px) * 4;
        if (rho > 1) { d[i + 3] = 0; continue; }
        const c = Math.asin(Math.min(1, rho));
        const sinc = Math.sin(c), cosc = Math.cos(c);
        const phi = Math.asin(cosc * Math.sin(p0) + (rho === 0 ? 0 : (sy * sinc * Math.cos(p0)) / rho));
        const lam = l0 + Math.atan2(sx * sinc, rho * cosc * Math.cos(p0) - sy * sinc * Math.sin(p0));
        const elev = Math.asin(
          Math.sin(phi) * Math.sin(dec) + Math.cos(phi) * Math.cos(dec) * Math.cos(lam - sunLon)
        );
        // Civil twilight softens the edge; without it the terminator is a
        // hard line, which is both wrong and ugly.
        const lit = Math.max(0, Math.min(1, (elev + 0.105) / 0.21));

        // THE PEAK BAND. Local solar 12:00-17:00 is where today's maximum is
        // actually being made, and it is the only strip on this planet the
        // desk can trade right now. It gets its own warmth so the eye finds it
        // before it finds anything else - that is the whole reason the page
        // is a globe and not a table.
        let h = (((lam - sunLon) * 12) / Math.PI + 12) % 24;
        if (h < 0) h += 24;
        const inPeak = h >= 12 && h <= 17 ? 1 - Math.abs(h - 14.5) / 2.5 : 0;

        const limb = Math.min(1, (1 - rho) * 16);      // a crisp edge, not a vignette
        const rim = Math.max(0, 1 - Math.abs(rho - 0.97) / 0.06) * 0.5; // thin atmosphere
        // Night is genuinely dark - the contrast IS the information.
        const base = 7 + lit * 62;
        d[i]     = (base * 0.62 + inPeak * lit * 58) * limb + rim * 70;
        d[i + 1] = (base * 0.80 + inPeak * lit * 30) * limb + rim * 110;
        d[i + 2] = (base * 1.05 + lit * 14)          * limb + rim * 170;
        d[i + 3] = 255;
      }
    }
    g.putImageData(img, x0, y0);

    // ---- land ---------------------------------------------------------
    // Drawn OVER the shading and translucent, so the terminator still shows
    // through it: a continent at 03:00 local has to look like a continent at
    // 03:00, not like a lit one with a line across it.
    //
    // A ring that crosses the limb is closed by clamping its hidden vertices
    // onto the rim rather than dropping them. Dropping them leaves Africa with
    // a bite out of it every time the planet turns; clamping is the standard
    // approximation and is correct to within the line width at this scale.
    g.save();
    g.beginPath();
    g.arc(cx, cy, R, 0, Math.PI * 2);
    g.clip();
    for (const ring of LAND) {
      g.beginPath();
      let started = false;
      let anyVisible = false;
      for (let i = 0; i < ring.length; i++) {
        const phi = (ring[i][1] * Math.PI) / 180;
        const lam = (ring[i][0] * Math.PI) / 180;
        const cosC = Math.sin(p0) * Math.sin(phi) + Math.cos(p0) * Math.cos(phi) * Math.cos(lam - l0);
        let ux = Math.cos(phi) * Math.sin(lam - l0);
        let uy = Math.cos(p0) * Math.sin(phi) - Math.sin(p0) * Math.cos(phi) * Math.cos(lam - l0);
        if (cosC > 0) {
          anyVisible = true;
        } else {
          const m = Math.hypot(ux, uy) || 1;
          ux /= m; uy /= m;                       // clamp to the rim
        }
        const sx = cx + R * ux, sy = cy - R * uy;
        if (!started) { g.moveTo(sx, sy); started = true; } else g.lineTo(sx, sy);
      }
      if (!anyVisible) continue;
      g.closePath();
      g.fillStyle = "rgba(126,148,116,0.26)";
      g.fill();
      g.strokeStyle = "rgba(196,222,236,0.42)";
      g.lineWidth = 0.7;
      g.stroke();
    }
    g.restore();

    // Graticule, faint, over the shading.
    g.save();
    g.beginPath();
    g.arc(cx, cy, R, 0, Math.PI * 2);
    g.clip();
    g.strokeStyle = "rgba(255,255,255,0.10)";
    g.lineWidth = 0.6;
    const project = (phi: number, lam: number) => {
      const cosC = Math.sin(p0) * Math.sin(phi) + Math.cos(p0) * Math.cos(phi) * Math.cos(lam - l0);
      if (cosC <= 0) return null;
      return [
        cx + R * Math.cos(phi) * Math.sin(lam - l0),
        cy - R * (Math.cos(p0) * Math.sin(phi) - Math.sin(p0) * Math.cos(phi) * Math.cos(lam - l0)),
      ] as const;
    };
    for (let latD = -60; latD <= 60; latD += 30) {
      g.beginPath();
      let pen = false;
      for (let lonD = -180; lonD <= 180; lonD += 3) {
        const p = project((latD * Math.PI) / 180, (lonD * Math.PI) / 180);
        if (!p) { pen = false; continue; }
        if (!pen) { g.moveTo(p[0], p[1]); pen = true; } else g.lineTo(p[0], p[1]);
      }
      g.stroke();
    }
    for (let lonD = -180; lonD < 180; lonD += 30) {
      g.beginPath();
      let pen = false;
      for (let latD = -90; latD <= 90; latD += 3) {
        const p = project((latD * Math.PI) / 180, (lonD * Math.PI) / 180);
        if (!p) { pen = false; continue; }
        if (!pen) { g.moveTo(p[0], p[1]); pen = true; } else g.lineTo(p[0], p[1]);
      }
      g.stroke();
    }
    // The equator, slightly stronger, so the tilt is readable.
    g.strokeStyle = "rgba(255,255,255,0.18)";
    g.beginPath();
    let pen = false;
    for (let lonD = -180; lonD <= 180; lonD += 2) {
      const p = project(0, (lonD * Math.PI) / 180);
      if (!p) { pen = false; continue; }
      if (!pen) { g.moveTo(p[0], p[1]); pen = true; } else g.lineTo(p[0], p[1]);
    }
    g.stroke();
    g.restore();
  }, [geom, yaw, tilt, sun, size]);

  /* ---- pointer -------------------------------------------------------- */
  function onDown(e: React.PointerEvent) {
    setAuto(false);
    drag.current = { x: e.clientX, y: e.clientY, yaw, tilt };
    (e.target as Element).setPointerCapture?.(e.pointerId);
  }
  function onMove(e: React.PointerEvent) {
    const d = drag.current;
    if (d) {
      setYaw(d.yaw + (e.clientX - d.x) * 0.4);
      setTilt(Math.max(-80, Math.min(80, d.tilt - (e.clientY - d.y) * 0.3)));
      return;
    }
    const r = (e.currentTarget as HTMLElement).getBoundingClientRect();
    const mx = e.clientX - r.left, my = e.clientY - r.top;
    let best: Placed | null = null;
    let bestD = 14;
    for (const p of placed) {
      if (!p.front) continue;
      const dd = Math.hypot(p.x - mx, p.y - my);
      if (dd < bestD) { bestD = dd; best = p; }
    }
    setHover(best);
  }
  function onUp() { drag.current = null; }

  /** The colour channel, per mode. Grey always means "no data", never zero. */
  function markerColor(p: Placed): string {
    if (mode === "edge") return edgeColor(p.c.best_edge_pp);
    if (mode === "health") {
      if (p.ageH == null) return "#7a4b4b";
      if (p.ageH > 6) return "#c05050";
      if (p.ageH > 3) return "#c7a04a";
      if ((p.c.forecast_max_c ?? null) === null) return "#c7a04a";
      return "#4f9d6a";
    }
    return p.c.hotness_sigma == null ? "#5b6472" : heatColor(p.c.hotness_sigma);
  }

  // The subsolar point, so the lighting is legible rather than merely present.
  const sunMark = (() => {
    const p0 = (tilt * Math.PI) / 180, l0 = (yaw * Math.PI) / 180;
    const phi = (sun.dec * Math.PI) / 180, lam = (sun.lon * Math.PI) / 180;
    const cosC = Math.sin(p0) * Math.sin(phi) + Math.cos(p0) * Math.cos(phi) * Math.cos(lam - l0);
    if (cosC <= 0) return null;
    const { cx, cy, R } = geom;
    return {
      x: cx + R * Math.cos(phi) * Math.sin(lam - l0),
      y: cy - R * (Math.cos(p0) * Math.sin(phi) - Math.sin(p0) * Math.cos(phi) * Math.cos(lam - l0)),
    };
  })();

  return (
    <div ref={wrap} className="relative select-none rounded border border-border bg-[#080b12]" style={{ height }}>
      <canvas
        ref={canvas}
        style={{ width: size.w, height: size.h, position: "absolute", inset: 0 }}
      />
      <svg
        viewBox={`0 0 ${size.w} ${size.h}`}
        width={size.w}
        height={size.h}
        className="absolute inset-0 touch-none"
        style={{ cursor: drag.current ? "grabbing" : "grab" }}
        onPointerDown={onDown}
        onPointerMove={onMove}
        onPointerUp={onUp}
        onPointerLeave={() => { onUp(); setHover(null); }}
      >
        {sunMark && (
          <g opacity={0.8} pointerEvents="none">
            <circle cx={sunMark.x} cy={sunMark.y} r={9} fill="none" stroke="#ffd98a" strokeWidth={0.8} opacity={0.5} />
            <circle cx={sunMark.x} cy={sunMark.y} r={2.5} fill="#ffd98a" />
            <title>The sun is directly overhead here right now.</title>
          </g>
        )}

        {placed.map((p) => {
          const vol = p.c.volume_24h ?? 0;
          const r = 4 + Math.min(4.5, Math.log10(1 + vol) * 1.0);
          const ring = REGION_COLOR[regionFromCity(p.c)] ?? "#8ab4ff";
          const isHover = hover?.c.city_key === p.c.city_key;
          const label = p.c.display_name ?? p.c.city_key;
          return (
            <g key={p.c.city_key} opacity={p.front ? 1 : 0.09}>
              {p.live && p.front && (
                <circle cx={p.x} cy={p.y} r={r + 5} fill="none" stroke={ring} strokeWidth={1.2}>
                  <animate attributeName="r" values={`${r + 3};${r + 11};${r + 3}`} dur="2.4s" repeatCount="indefinite" />
                  <animate attributeName="opacity" values="0.95;0.05;0.95" dur="2.4s" repeatCount="indefinite" />
                </circle>
              )}
              {/* A DARK HALO FIRST. The markers sit on ocean, on land and on
                  night, and a coloured dot with no separation from its ground
                  disappears against at least one of them. */}
              <circle cx={p.x} cy={p.y} r={r + 2} fill="#080b12" opacity={0.75} />
              <circle
                cx={p.x} cy={p.y} r={r}
                fill={markerColor(p)}
                stroke={isHover ? "#ffffff" : ring}
                strokeWidth={isHover ? 2 : 1.6}
                onClick={() => p.front && onPick?.(p.c.city_key)}
                style={{ cursor: p.front ? "pointer" : "default" }}
              />
              {/* An inner pip when this city has something tradeable on the
                  board. Position on the globe says where; the pip says whether
                  it is worth going there. */}
              {(p.c.n_tradeable ?? 0) > 0 && p.front && (
                <circle cx={p.x} cy={p.y} r={1.6} fill="#080b12" opacity={0.85} pointerEvents="none" />
              )}
              {(p.important || isHover) && p.front && (
                <text
                  x={p.x + r + 4}
                  y={p.y + 3}
                  fontSize={10}
                  fill="#e6edf6"
                  stroke="#080b12"
                  strokeWidth={2.6}
                  paintOrder="stroke"
                  pointerEvents="none"
                >
                  {label}
                </text>
              )}
            </g>
          );
        })}
      </svg>

      {/* ---- the reading, docked BOTTOM-RIGHT ---------------------------
          Controls left, result right. It used to be the other way round -
          you read the numbers bottom-left and reached top-right to change
          what they meant, so every mode change was a diagonal trip across
          the globe and the buttons sat over the limb on a narrow screen.
          Left to right is now cause then effect: pick the mode, read the
          result under your other hand. --------------------------------- */}
      {hover ? (
        <div className="pointer-events-none absolute bottom-2 right-2 w-[290px] rounded border border-border bg-panel/95 px-2.5 py-1.5 text-[11px] leading-relaxed">
          <div className="flex items-baseline gap-2">
            <span className="font-semibold">{hover.c.display_name ?? hover.c.city_key}</span>
            <span className="font-mono text-[10px] text-muted">
              {hover.c.icao ?? ""} · solar {hover.solar.toFixed(1)}h
            </span>
          </div>
          <div className="mt-1 grid grid-cols-2 gap-x-3 gap-y-0.5 font-mono text-[10px]">
            <Row k="now" v={hover.c.now_c == null ? "—" : `${hover.c.now_c.toFixed(1)}°C`}
                 sub={hover.ageH == null ? "no reading" : `${hover.ageH.toFixed(1)}h old`}
                 warn={hover.ageH != null && hover.ageH > 3} />
            <Row k="max so far" v={hover.c.running_max_c == null ? "—" : `${hover.c.running_max_c.toFixed(1)}°C`}
                 sub={hover.c.day_decided ? "day decided" : hover.c.peak_window_state ?? ""} />
            <Row k="vs normal"
                 v={hover.c.hotness_sigma == null ? "—" : `${hover.c.hotness_sigma > 0 ? "+" : ""}${hover.c.hotness_sigma.toFixed(1)}σ`}
                 sub={hover.c.normal_max_c == null ? "no baseline" : `normal ${hover.c.normal_max_c.toFixed(1)}°C`}
                 warn={(hover.c.hotness_sigma ?? 0) > 1.5} />
            {/* This printed °C for every city, whatever unit that city's
                market is quoted in, and said nothing when the desk's own check
                had already flagged the number. Both now come from one place. */}
            <Row k="forecast"
                 v={fmtTemp(hover.c.forecast_max_c, (hover.c.unit ?? "C") as Unit)}
                 sub={forecastProvenance(hover.c, (hover.c.unit ?? "C") as Unit).slice(0, 46)}
                 warn={hover.c.forecast_max_c == null || Boolean(hover.c.forecast_suspect)} />
            <Row k="best edge" v={hover.c.best_edge_pp == null ? "—" : `${(hover.c.best_edge_pp * 100).toFixed(1)}pp`}
                 sub={`${hover.c.n_tradeable ?? 0} of ${hover.c.live_bands ?? 0} bands tradeable`} />
            <Row k="model error" v={hover.c.mae_c == null ? "—" : `${hover.c.mae_c.toFixed(2)}°C`}
                 sub={hover.c.skill_days ? `${hover.c.skill_days}d measured` : "never measured"}
                 warn={hover.c.mae_c == null || (hover.c.skill_days ?? 0) < 200} />
          </div>
          {/* WHAT IT MEANS, not just what it is. Six numbers above, and the
              reader still had to know the desk's rules to turn them into a
              decision. This is that sentence, from the same fields. */}
          <div className="mt-1 border-t border-border/60 pt-1 text-[10px] leading-snug">
            <span className={implication(hover.c, hover.ageH).tone}>{implication(hover.c, hover.ageH).text}</span>
          </div>
          <div className="mt-0.5 text-[10px] text-accent">click to open its monitor</div>
        </div>
      ) : (
        /* ---- with nothing hovered, say what the desk's day looks like --- */
        <div className="pointer-events-none absolute bottom-2 right-2 max-w-[290px] rounded border border-border bg-panel/90 px-2.5 py-1.5 font-mono text-[10px] leading-relaxed">
          <span className={tally.live ? "text-good" : "text-muted"}>
            {tally.live} in the measured peak window
          </span>
          <span className="text-muted"> · {tally.afternoon} in the afternoon</span>
          <span className="text-muted"> · {tally.climbing} still climbing</span>
          <span className="text-muted"> · {tally.decided} decided</span>
          <span className="text-muted"> · {tally.dark} overnight</span>
          {tally.stale > 0 && <span className="text-warn"> · {tally.stale} with a stale reading</span>}
        </div>
      )}

      {/* ---- controls, TOP-LEFT ----------------------------------------- */}
      <div className="absolute left-2 top-2 flex w-[220px] flex-col items-start gap-1.5 font-mono text-[10px] text-muted">
        <div className="flex items-center gap-1">
          {MODES.map((m) => (
            <button
              key={m.key}
              onClick={() => setMode(m.key)}
              title={m.hint}
              className={`rounded border px-1.5 py-0.5 ${
                mode === m.key ? "border-accent bg-accent/15 text-accent" : "border-border bg-panel/80 hover:text-text"
              }`}
            >
              {m.label}
            </button>
          ))}
        </div>
        {/* What the colour you are looking at actually means. It was only in a
            title attribute, which is invisible on touch and unread on desktop. */}
        <div className="rounded border border-border bg-panel/85 px-1.5 py-1 text-[10px] leading-snug text-muted">
          {MODES.find((m) => m.key === mode)?.reading}
        </div>
        <div className="flex items-center gap-1">
          <button
            onClick={() => setAuto((a) => !a)}
            className="rounded border border-border bg-panel/80 px-1.5 py-0.5 hover:text-text"
          >
            {auto ? "pause" : "spin"}
          </button>
          <button
            onClick={() => { setYaw(sun.lon + 45); setTilt(18); setAuto(false); }}
            className="rounded border border-border bg-panel/80 px-1.5 py-0.5 hover:text-text"
            title="Rotate to the meridian where local solar time is about 15:00 - where the day's maximum is being made."
          >
            to the peak
          </button>
        </div>
      </div>
      <div className="absolute bottom-2 left-2 font-mono text-[10px] text-muted">
        drag to rotate · lit side is daylight now · click a city for its monitor
      </div>
    </div>
  );
}

/**
 * The one sentence the six numbers add up to.
 *
 * Ordered by what disqualifies a city first: a dead feed makes every other
 * number meaningless, a decided day makes the forecast irrelevant, and no
 * priced band makes an edge unavailable however good the read is. Only a city
 * that survives all three is worth a look, and only then does hotness matter.
 */
function implication(c: CityStats, ageH: number | null): { text: string; tone: string } {
  const stale = ageH != null && ageH > 3;
  if (c.now_c == null || stale) {
    return { text: "The feed has stopped. Every other number here is out of date - fix the feed before reading the colour.", tone: "text-bad" };
  }
  if (c.day_decided) {
    return { text: "The day is decided - the maximum is in. Nothing forward-looking left to trade here today.", tone: "text-muted" };
  }
  if (c.forecast_max_c == null) {
    return { text: "No forward forecast, so there is nothing to price against. The colour says how today feels, not what to do.", tone: "text-warn" };
  }
  if ((c.live_bands ?? 0) === 0) {
    return { text: "No live buckets in this city - a read with nowhere to express it.", tone: "text-muted" };
  }
  if ((c.n_tradeable ?? 0) === 0) {
    return { text: `${c.live_bands} bucket(s) priced, none tradeable after fees. The edge is there and the book is not.`, tone: "text-warn" };
  }
  const edgePp = (c.best_edge_pp ?? 0) * 100;
  const inWindow = c.peak_window_state === "INSIDE";
  const where = inWindow ? "inside its peak window now" : "outside its peak window";
  if (edgePp >= 5) {
    return { text: `${edgePp.toFixed(1)}pp on the best bucket, ${where}. This is the size of thing the desk is looking for.`, tone: "text-good" };
  }
  if (edgePp > 0) {
    return { text: `${edgePp.toFixed(1)}pp on the best bucket, ${where}. Thin - worth watching rather than taking.`, tone: "text-muted" };
  }
  return { text: `Priced with no edge, ${where}. The market and the desk agree here.`, tone: "text-muted" };
}

function Row({ k, v, sub, warn }: { k: string; v: string; sub?: string; warn?: boolean }) {
  return (
    <div>
      <span className="text-muted">{k} </span>
      <span className={warn ? "text-warn" : ""}>{v}</span>
      {sub && <div className="text-[9px] text-muted">{sub}</div>}
    </div>
  );
}
