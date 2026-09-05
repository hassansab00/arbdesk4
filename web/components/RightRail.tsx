"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import SignalsPanel from "@/components/SignalsPanel";
import CityWatch from "@/components/CityWatch";
import { supabase } from "@/lib/supabase";

/**
 * Signals and the watchlist, as a DRAWER rather than a column.
 *
 * WHY THIS IS THE SIXTH VERSION. The previous five all improved a permanent
 * 320px column, and every one of them missed the same two things:
 *
 *   IT COST WIDTH TO SAY NOTHING. Every strategy ships disabled, so on a
 *   working desk the column held one sentence - "no trade signals, every
 *   strategy is off" - on every page, forever, taking a quarter of a 1280px
 *   laptop away from the Board's ladder and the Analytics tables. A glance
 *   panel that is empty most of the time must not be paid for by the content
 *   you are actually reading.
 *
 *   IT DID NOT EXIST BELOW 1024px. `hidden lg:flex`. Narrow the window and
 *   Signals was simply gone, with nothing to click and no indication it was
 *   ever there. There is no width at which "you cannot see your signals" is
 *   the right answer.
 *
 * So: closed by default, opens over the page, and the trigger is a button with
 * a live count that is present at every width. Pinning docks it as a column
 * for anyone who wants the old behaviour on a wide screen - the difference is
 * that it is now a choice rather than a tax.
 *
 * The count polls whether the drawer is open or not, because the whole job of
 * this thing is to tell you something fired while you were looking elsewhere.
 */

const PIN_KEY = "ad4-rail-pinned";
const W_KEY = "ad4-rail-width";
const TAB_KEY = "ad4-rail-tab";

const MIN_W = 280;
const MAX_W = 620;

function readNum(key: string, fallback: number): number {
  try {
    const v = Number(localStorage.getItem(key));
    return Number.isFinite(v) && v > 0 ? v : fallback;
  } catch {
    return fallback; // private window or blocked storage
  }
}

export default function RightRail() {
  const [open, setOpen] = useState(false);
  const [pinned, setPinned] = useState(false);
  const [width, setWidth] = useState(340);
  const [drag, setDrag] = useState<null | "width">(null);
  const [tab, setTab] = useState<"signals" | "watch">("signals");
  const [pending, setPending] = useState<number | null>(null);
  const col = useRef<HTMLElement | null>(null);

  useEffect(() => {
    setWidth(readNum(W_KEY, 340));
    try {
      const p = localStorage.getItem(PIN_KEY) === "1";
      setPinned(p);
      setOpen(p);
      const t = localStorage.getItem(TAB_KEY);
      if (t === "watch" || t === "signals") setTab(t);
    } catch {
      /* ignore */
    }
  }, []);

  const persist = (k: string, v: string) => {
    try { localStorage.setItem(k, v); } catch { /* ignore */ }
  };

  // THE COUNT IS THE POINT. It runs whether the drawer is open or shut - a
  // notification you only see after opening the thing is not a notification.
  const refreshCount = useCallback(async () => {
    try {
      const { count, error } = await supabase
        .from("signals")
        .select("signal_id", { count: "exact", head: true })
        .eq("status", "pending_approval");
      setPending(error ? null : count ?? 0);
    } catch {
      setPending(null); // unconfigured or offline: show no badge, never a zero
    }
  }, []);

  useEffect(() => {
    refreshCount();
    const t = setInterval(refreshCount, 60000);
    return () => clearInterval(t);
  }, [refreshCount]);

  // Escape closes it, unless it is pinned - a docked column is not a dialog.
  useEffect(() => {
    if (!open || pinned) return;
    const k = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [open, pinned]);

  useEffect(() => {
    if (!drag) return;
    const move = (e: MouseEvent) => {
      if (drag === "width") {
        setWidth(Math.min(MAX_W, Math.max(MIN_W, window.innerWidth - e.clientX)));
      }
    };
    const up = () => {
      setDrag(null);
      persist(W_KEY, String(width));
    };
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", up);
    document.body.style.userSelect = "none";
    return () => {
      window.removeEventListener("mousemove", move);
      window.removeEventListener("mouseup", up);
      document.body.style.userSelect = "";
    };
  }, [drag, width]);

  function togglePin() {
    setPinned((p) => {
      persist(PIN_KEY, p ? "0" : "1");
      if (!p) setOpen(true);
      return !p;
    });
  }

  function pick(t: "signals" | "watch") {
    setTab(t);
    persist(TAB_KEY, t);
  }

  const panel = (
    <aside
      ref={col}
      className="flex min-h-0 min-w-0 flex-col border-l border-border bg-panel"
      style={{ width }}
    >
      <div className="flex shrink-0 items-center gap-1 border-b border-border px-2 py-1.5 text-xs">
        <button
          onClick={() => pick("signals")}
          className={`rounded px-2 py-0.5 ${tab === "signals" ? "bg-panel2 text-text" : "text-muted hover:text-text"}`}
        >
          Signals
          {pending ? (
            <span className="ml-1.5 rounded-full bg-accent px-1.5 text-[10px] font-semibold text-base">
              {pending}
            </span>
          ) : null}
        </button>
        <button
          onClick={() => pick("watch")}
          className={`rounded px-2 py-0.5 ${tab === "watch" ? "bg-panel2 text-text" : "text-muted hover:text-text"}`}
        >
          Watchlist
        </button>
        <span className="flex-1" />
        <button
          onClick={togglePin}
          title={pinned ? "Unpin — let it close and give the page its width back" : "Pin it open as a column"}
          className={`rounded px-1.5 py-0.5 ${pinned ? "text-accent" : "text-muted hover:text-text"}`}
        >
          {pinned ? "unpin" : "pin"}
        </button>
        <button
          onClick={() => { setOpen(false); if (pinned) togglePin(); }}
          title="Close"
          className="rounded px-1.5 py-0.5 text-muted hover:text-text"
        >
          ✕
        </button>
      </div>

      {/* Both panels stay MOUNTED and the hidden one is display:none, so
          switching tabs does not refetch and does not lose scroll position.
          A tab that reloads is a tab people stop using. */}
      <div className="flex min-h-0 flex-1 flex-col">
        {/* display comes from the STYLE, not the `hidden` attribute: a
            `flex` utility class beats the user-agent rule that `hidden`
            relies on, so the inactive panel stayed visible. */}
        <div
          className="min-h-0 flex-col"
          style={{ display: tab === "signals" ? "flex" : "none", flex: "1 1 0%" }}
        >
          <SignalsPanel onHide={() => setOpen(false)} onChanged={refreshCount} />
        </div>
        <div
          className="min-h-0 flex-col"
          style={{ display: tab === "watch" ? "flex" : "none", flex: "1 1 0%" }}
        >
          <CityWatch />
        </div>
      </div>
    </aside>
  );

  return (
    <>
      {/* The trigger. Always present, every width - which the old column was
          not. Vertical so it costs 28px instead of 320. */}
      {!(open && pinned) && (
        <button
          onClick={() => setOpen((o) => !o)}
          title="Signals and watchlist"
          className="flex w-7 shrink-0 flex-col items-center gap-2 border-l border-border bg-panel py-2 text-[10px] uppercase tracking-widest text-muted hover:text-text"
        >
          {pending ? (
            <span className="rounded-full bg-accent px-1 text-[10px] font-semibold leading-4 text-base">
              {pending}
            </span>
          ) : (
            <span className="text-accent">•</span>
          )}
          <span style={{ writingMode: "vertical-rl" }}>Signals</span>
        </button>
      )}

      {/* Pinned: a real column, and the page reflows around it. */}
      {open && pinned && (
        <>
          <div
            onMouseDown={() => setDrag("width")}
            title="Drag to resize"
            className={`w-1.5 shrink-0 cursor-col-resize bg-border/40 hover:bg-accent/60 ${drag === "width" ? "bg-accent" : ""}`}
          />
          {panel}
        </>
      )}

      {/* Unpinned: an overlay. It costs the page nothing when shut, and it
          works at 700px wide as well as at 1600. */}
      {open && !pinned && (
        <div className="fixed inset-0 z-40 flex justify-end" role="dialog" aria-label="Signals and watchlist">
          <button
            className="flex-1 bg-base/60"
            aria-label="Close"
            onClick={() => setOpen(false)}
          />
          <div className="flex max-w-full shadow-2xl">{panel}</div>
        </div>
      )}
    </>
  );
}
