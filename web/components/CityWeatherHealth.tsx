"use client";

import { supabase } from "@/lib/supabase";
import { useQuery } from "@/lib/useQuery";
import { ErrorBox, Loading } from "@/components/DataState";

/**
 * WHICH CITIES CAN THE DESK ACTUALLY TIME?
 *
 * The strip above this one answers freshness per TABLE, and per table the
 * answer is green: weather_observations has rows from four minutes ago. That
 * is true and it is misleading. It takes max(valid_at) across all 54 cities,
 * and seventeen American stations report every five minutes, so a handful of
 * fresh cities keep the whole table green while forty are a day behind.
 *
 * Measured 2026-09-19:
 *
 *   Americas  17 cities, ~340 readings a day (5-minutely)
 *   Europe    11 cities,   24 readings a day (hourly, ~1 day late)
 *   Asia      23 cities,   24 readings a day (hourly, ~1 day late)
 *
 * The international history is COMPLETE - exactly 24 hourly rows a day, no
 * gaps. It simply arrives about a day after the day it describes, which is
 * what IEM serves and not something this repo can change. Training,
 * settlement and outcome scoring are unaffected. What is affected is
 * everything that needs TODAY: the slope, the peak countdown, and s7, which
 * needs a reading under ninety minutes old and has never fired once.
 *
 * So this panel reports the three states separately rather than as one colour:
 *
 *   series      two or more of today's readings. A real maximum, and a slope.
 *   floor only  one reading, or only the live thermometer. The day reached AT
 *               LEAST this and may have reached far more - a floor under the
 *               maximum, never the maximum. s5 is refused here.
 *   nothing     no reading belonging to this city's current day at all.
 */

interface CityHealth {
  city_key: string;
  display_name: string | null;
  timezone: string | null;
  local_date: string | null;
  local_hour: number | null;
  obs_feed_age_h: number | null;
  readings_today: number | null;
  live_feed_age_min: number | null;
  live_source_kind: string | null;
  latest_temp_c: number | null;
  latest_temp_today_c: number | null;
  running_max_basis: "series" | "floor_only" | "absent" | null;
  stored_max_below_latest: boolean | null;
  timing_trustworthy: boolean | null;
  note: string | null;
}

const BASIS_LABEL: Record<string, string> = {
  series: "series",
  floor_only: "floor only",
  absent: "nothing today",
};

const BASIS_STYLE: Record<string, { dot: string; text: string }> = {
  series: { dot: "bg-good", text: "text-good" },
  floor_only: { dot: "bg-warn", text: "text-warn" },
  absent: { dot: "bg-bad", text: "text-bad" },
};

function hours(h: number | null): string {
  if (h === null || h === undefined) return "never";
  if (h < 1) return `${Math.max(1, Math.round(h * 60))} min`;
  if (h < 48) return `${Math.round(h)} h`;
  return `${Math.round(h / 24)} d`;
}

export default function CityWeatherHealth() {
  const q = useQuery<CityHealth[]>(
    () => supabase.from("v_city_observation_health").select("*"),
    [],
    120000
  );

  if (q.loading && !q.data) return <Loading compact />;
  if (q.error) return <ErrorBox message={q.error} onRetry={q.refresh} compact />;

  const rows = q.data ?? [];
  if (!rows.length) return null;

  const timeable = rows.filter((r) => r.timing_trustworthy).length;
  const byBasis = {
    series: rows.filter((r) => r.running_max_basis === "series").length,
    floor_only: rows.filter((r) => r.running_max_basis === "floor_only").length,
    absent: rows.filter((r) => r.running_max_basis === "absent").length,
  };
  const impossible = rows.filter((r) => r.stored_max_below_latest).length;

  const rank = { absent: 0, floor_only: 1, series: 2 } as const;
  const sorted = rows.slice().sort((a, b) => {
    const ra = rank[(a.running_max_basis ?? "absent") as keyof typeof rank];
    const rb = rank[(b.running_max_basis ?? "absent") as keyof typeof rank];
    return ra - rb
      || (b.obs_feed_age_h ?? 0) - (a.obs_feed_age_h ?? 0)
      || (a.display_name ?? a.city_key).localeCompare(b.display_name ?? b.city_key);
  });

  return (
    <details className="group mb-3 rounded border border-border bg-panel/60">
      <summary className="flex cursor-pointer list-none flex-wrap items-center gap-2 px-3 py-2 text-xs">
        <span
          className={`h-2 w-2 rounded-full ${
            timeable === 0 ? "bg-bad" : timeable < rows.length / 2 ? "bg-warn" : "bg-good"
          }`}
        />
        <span className={timeable === 0 ? "text-bad" : "text-text"}>
          <b>
            {timeable} of {rows.length}
          </b>{" "}
          cities have weather fresh enough to time an entry
        </span>
        <span className="text-muted">
          · {byBasis.series} measured series · {byBasis.floor_only} floor only ·{" "}
          {byBasis.absent} nothing today
        </span>
        <span className="ml-auto text-[10px] text-muted group-open:hidden">show</span>
        <span className="ml-auto hidden text-[10px] text-muted group-open:inline">hide</span>
      </summary>

      <div className="border-t border-border px-3 py-2">
        <p className="mb-2 max-w-4xl text-[11px] leading-relaxed text-muted">
          Table-level freshness cannot see this: it takes the newest row across all cities, and the
          seventeen American stations that report every five minutes keep it green. The
          international feed is <b>complete but about a day late</b> — exactly 24 hourly rows a day,
          arriving after the day they describe. Nothing is broken and nothing is lost; training,
          settlement and outcome scoring all use it. What it cannot do is tell you where{" "}
          <i>today</i> is going, which is what an entry window needs.{" "}
          <b>A single reading is a floor, not a maximum.</b> If the only thing known about today is
          the current temperature, the day&rsquo;s maximum is at least that and possibly much more —
          so s5, whose whole premise is a locked maximum, is refused on those cities rather than
          buying the band that happens to hold the floor.
          {impossible > 0 && (
            <>
              {" "}
              <span className="text-bad">
                {impossible} {impossible === 1 ? "city has" : "cities have"} a stored maximum below
                the current reading, which is arithmetically impossible — run{" "}
                <code className="rounded bg-panel2 px-1">
                  select public.refresh_live_weather_timing()
                </code>
                .
              </span>
            </>
          )}
        </p>

        <table className="w-full text-left text-[11px]">
          <thead className="text-[10px] uppercase tracking-wide text-muted">
            <tr>
              <th className="py-1 pr-2">City</th>
              <th className="py-1 pr-2">Its day</th>
              <th className="py-1 pr-2 text-right">Readings today</th>
              <th className="py-1 pr-2 text-right">Archive age</th>
              <th className="py-1 pr-2 text-right">Live age</th>
              <th className="py-1 pr-2">Maximum rests on</th>
              <th className="py-1">What that means</th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((r) => {
              const basis = (r.running_max_basis ?? "absent") as keyof typeof BASIS_STYLE;
              const st = BASIS_STYLE[basis];
              return (
                <tr key={r.city_key} className="border-t border-border/40 align-top">
                  <td className="py-1 pr-2">
                    <span className="inline-flex items-center gap-1.5">
                      <span className={`h-1.5 w-1.5 rounded-full ${st.dot}`} />
                      {r.display_name ?? r.city_key}
                    </span>
                  </td>
                  <td className="py-1 pr-2 tabular-nums text-muted">
                    {r.local_date ?? "—"}
                    {r.local_hour !== null && r.local_hour !== undefined && (
                      <span className="opacity-70"> · {String(r.local_hour).padStart(2, "0")}h</span>
                    )}
                  </td>
                  <td className="py-1 pr-2 text-right tabular-nums">{r.readings_today ?? 0}</td>
                  <td className="py-1 pr-2 text-right tabular-nums text-muted">
                    {hours(r.obs_feed_age_h)}
                  </td>
                  <td className="py-1 pr-2 text-right tabular-nums text-muted">
                    {r.live_feed_age_min === null || r.live_feed_age_min === undefined
                      ? "never"
                      : `${Math.round(r.live_feed_age_min)} min`}
                    {r.live_source_kind === "model" && (
                      <span className="opacity-70"> · model</span>
                    )}
                  </td>
                  <td className={`py-1 pr-2 ${st.text}`}>{BASIS_LABEL[basis]}</td>
                  <td className="py-1 text-muted">{r.note ?? "—"}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </details>
  );
}
