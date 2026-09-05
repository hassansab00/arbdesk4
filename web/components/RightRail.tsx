"use client";

import { useEffect, useRef, useState } from "react";
import SignalsPanel from "@/components/SignalsPanel";
import CityWatch from "@/components/CityWatch";

/**
 * The right-hand column: what fired, and what you are keeping an eye on.
 *
 * Three things were wrong with it and only the third was visible as a bug:
 *
 *   IT SCROLLED AWAY. The rail sat in normal document flow, so scrolling any
 *   page took the Signals panel off the top of the screen. The shell in
 *   layout.tsx now pins the row; this column is full height and scrolls
 *   internally.
 *
 *   THE TWO PANELS FOUGHT FOR HEIGHT. Signals and City Watch were stacked with
 *   no height rules, so a long signal list pushed City Watch off the bottom and
 *   a long watchlist did the same to Signals. Each now owns a region and
 *   scrolls inside it, and the split is draggable because which one matters
 *   depends on what you are doing.
 *
 *   IT WAS 384px OF A 1280px LAPTOP. Nearly a third of the screen, permanently,
 *   for a glance column - which made the Board's wide tables unreadable. The
 *   width is now yours, and remembered.
 */

const W_KEY = "ad4-rail-width";
const SPLIT_KEY = "ad4-rail-split";
const COLLAPSED_KEY = "ad4-rail-collapsed";

const MIN_W = 260;
const MAX_W = 560;

function readNum(key: string, fallback: number): number {
  try {
    const v = Number(localStorage.getItem(key));
    return Number.isFinite(v) && v > 0 ? v : fallback;
  } catch {
    return fallback;      // private window or blocked storage
  }
}

export default function RightRail() {
  const [collapsed, setCollapsed] = useState(false);
  const [width, setWidth] = useState(320);
  // Share of the column's height given to Signals. City Watch gets the rest.
  const [split, setSplit] = useState(0.6);
  const [drag, setDrag] = useState<null | "width" | "split">(null);
  const col = useRef<HTMLElement | null>(null);

  useEffect(() => {
    setWidth(readNum(W_KEY, 320));
    setSplit(Math.min(0.85, Math.max(0.2, readNum(SPLIT_KEY, 0.6))));
    try { setCollapsed(localStorage.getItem(COLLAPSED_KEY) === "1"); } catch { /* ignore */ }
  }, []);

  const persist = (k: string, v: string) => {
    try { localStorage.setItem(k, v); } catch { /* ignore */ }
  };

  // Dragging is on the window, not the handle: a fast drag leaves the handle
  // behind and a handle-bound listener drops the gesture halfway.
  useEffect(() => {
    if (!drag) return;
    const move = (e: MouseEvent) => {
      if (drag === "width") {
        const w = Math.min(MAX_W, Math.max(MIN_W, window.innerWidth - e.clientX));
        setWidth(w);
      } else if (col.current) {
        const box = col.current.getBoundingClientRect();
        const frac = (e.clientY - box.top) / box.height;
        setSplit(Math.min(0.85, Math.max(0.2, frac)));
      }
    };
    const up = () => {
      setDrag(null);
      persist(W_KEY, String(width));
      persist(SPLIT_KEY, String(split));
    };
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", up);
    // While dragging, stop the cursor turning into a text caret over the page.
    document.body.style.userSelect = "none";
    return () => {
      window.removeEventListener("mousemove", move);
      window.removeEventListener("mouseup", up);
      document.body.style.userSelect = "";
    };
  }, [drag, width, split]);

  function toggle() {
    setCollapsed((c) => {
      persist(COLLAPSED_KEY, c ? "0" : "1");
      return !c;
    });
  }

  if (collapsed) {
    return (
      <button
        onClick={toggle}
        title="Show Signals and City Watch"
        className="hidden shrink-0 border-l border-border bg-panel px-1.5 text-[10px] uppercase tracking-widest text-muted hover:text-text lg:block"
        style={{ writingMode: "vertical-rl" }}
      >
        Signals &amp; Watch
      </button>
    );
  }

  return (
    <>
      {/* the width handle: a real target, not a 1px line */}
      <div
        onMouseDown={() => setDrag("width")}
        title="Drag to resize"
        className={`hidden w-1.5 shrink-0 cursor-col-resize bg-border/40 hover:bg-accent/60 lg:block ${
          drag === "width" ? "bg-accent" : ""
        }`}
      />
      <aside
        ref={col}
        className="hidden min-h-0 shrink-0 flex-col bg-panel lg:flex"
        style={{ width }}
      >
        <div className="flex min-h-0 flex-col" style={{ flex: `${split} 1 0%` }}>
          <SignalsPanel onHide={toggle} />
        </div>

        <div
          onMouseDown={() => setDrag("split")}
          title="Drag to give one panel more room"
          className={`h-1.5 shrink-0 cursor-row-resize bg-border/40 hover:bg-accent/60 ${
            drag === "split" ? "bg-accent" : ""
          }`}
        />

        <div className="flex min-h-0 flex-col" style={{ flex: `${1 - split} 1 0%` }}>
          <CityWatch />
        </div>
      </aside>
    </>
  );
}
