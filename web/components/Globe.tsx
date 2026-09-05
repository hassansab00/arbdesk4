"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { heatColor } from "@/lib/heat";
import { REGION_COLOR } from "@/lib/heat";
import { regionFromCity } from "@/lib/region";
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

interface Placed {
  c: CityStats;
  x: number; y: number;      // screen px
  front: boolean;
  lat: number; lon: number;
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
      out.push({
        c, front: cosC > 0,
        x: cx + R * x, y: cy - R * y,
        lat: c.latitude, lon: c.longitude,
      });
    }
    // Far side first so the near side draws over it.
    return out.sort((a, b) => Number(a.front) - Number(b.front));
  }, [cities, geom, yaw, tilt]);

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

  const inWindow = placed.filter(
    (p) => p.front && (p.c.peak_window_state === "INSIDE" || (p.c.day_decided === false && solarHour(p.lon, sun.lon) >= 12 && solarHour(p.lon, sun.lon) <= 17))
  ).length;

  return (
    <div ref={wrap} className="relative select-none rounded border border-border bg-[#080b12]" style={{ height }}>
      <canvas
        ref={canvas}
        style={{ width: size.w, height: size.h, position: "absolute", inset: 0, cursor: drag.current ? "grabbing" : "grab" }}
      />
      <svg
        viewBox={`0 0 ${size.w} ${size.h}`}
        width={size.w}
        height={size.h}
        className="absolute inset-0 touch-none"
        style={{ cursor: "grab" }}
        onPointerDown={onDown}
        onPointerMove={onMove}
        onPointerUp={onUp}
        onPointerLeave={() => { onUp(); setHover(null); }}
      >
        {placed.map((p) => {
          const hot = p.c.hotness_sigma;
          const vol = p.c.volume_24h ?? 0;
          const r = 3.4 + Math.min(5, Math.log10(1 + vol) * 1.1);
          const open = p.c.peak_window_state === "INSIDE" && !p.c.day_decided;
          const region = regionFromCity(p.c);
          return (
            <g key={p.c.city_key} opacity={p.front ? 1 : 0.1}>
              {open && p.front && (
                <circle cx={p.x} cy={p.y} r={r + 5} fill="none" stroke={REGION_COLOR[region] ?? "#8ab4ff"} strokeWidth={1}>
                  <animate attributeName="r" values={`${r + 3};${r + 9};${r + 3}`} dur="2.4s" repeatCount="indefinite" />
                  <animate attributeName="opacity" values="0.9;0.05;0.9" dur="2.4s" repeatCount="indefinite" />
                </circle>
              )}
              <circle
                cx={p.x} cy={p.y} r={r}
                fill={hot == null ? "#5b6472" : heatColor(hot)}
                stroke={REGION_COLOR[region] ?? "#8ab4ff"}
                strokeWidth={1.2}
                onClick={() => p.front && onPick?.(p.c.city_key)}
                style={{ cursor: p.front ? "pointer" : "default" }}
              />
            </g>
          );
        })}
      </svg>

      {/* ---- the reading, docked, so the pointer never covers it -------- */}
      {hover && (
        <div className="pointer-events-none absolute bottom-2 left-2 max-w-[280px] rounded border border-border bg-panel/95 px-2.5 py-1.5 text-[11px] leading-relaxed">
          <div className="font-semibold">{hover.c.display_name ?? hover.c.city_key}</div>
          <div className="text-muted">
            local solar {solarHour(hover.lon, sun.lon).toFixed(1)}h ·{" "}
            {hover.c.peak_window_state ?? "window unknown"}
            {hover.c.day_decided ? " · day decided" : ""}
          </div>
          <div className="font-mono text-muted">
            {hover.c.now_c == null ? "no reading" : `${hover.c.now_c.toFixed(1)}°C now`}
            {hover.c.hotness_sigma != null && (
              <span className={hover.c.hotness_sigma > 0 ? " text-warn" : " text-accent"}>
                {" "}({hover.c.hotness_sigma > 0 ? "+" : ""}
                {hover.c.hotness_sigma.toFixed(1)}σ vs its own normal)
              </span>
            )}
          </div>
          {hover.c.best_edge_pp != null && (
            <div className="text-muted">best edge here {(hover.c.best_edge_pp * 100).toFixed(1)}pp</div>
          )}
        </div>
      )}

      {/* ---- controls --------------------------------------------------- */}
      <div className="absolute right-2 top-2 flex items-center gap-2 font-mono text-[10px] text-muted">
        <span title="Cities on the lit side that are inside their peak window - where today's maxima are being made right now.">
          {inWindow} in the window
        </span>
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
      <div className="absolute bottom-2 right-2 font-mono text-[10px] text-muted">
        drag to rotate · lit side is daylight now
      </div>
    </div>
  );
}
