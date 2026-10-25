"use client";

import { useEffect, useRef, useState } from "react";

const SECTIONS = [
  ["overview", "Overview"], ["filters", "Filters"], ["settlements", "Settlements"],
  ["cities", "City forecasts"], ["outlook", "Forward outlook"], ["clocks", "City clocks"],
  ["accuracy", "City accuracy"], ["results", "Was it right?"], ["predictors", "Predictors"],
  ["convergence", "Convergence"], ["history", "Hit & miss"], ["errors", "Forecast errors"],
  ["decay", "Forecast decay"], ["scorecard", "Model scorecard"], ["returns", "Bankroll & scaling"],
] as const;

/** The app's main panel scrolls independently of the viewport and right rail. */
export default function PredictiveSectionNav() {
  const nav = useRef<HTMLElement>(null);
  const strip = useRef<HTMLDivElement>(null);
  const [active, setActive] = useState<string>(SECTIONS[0][0]);

  useEffect(() => {
    const root = nav.current?.closest("main");
    if (!root) return;
    const targets = SECTIONS.map(([id]) => document.getElementById(`predictive-${id}`));
    let frame = 0;
    const update = () => {
      frame = 0;
      const edge = (nav.current?.getBoundingClientRect().bottom ?? 0) + 20;
      let current: string = SECTIONS[0][0];
      for (let i = 0; i < targets.length; i++) {
        if (targets[i] && targets[i]!.getBoundingClientRect().top <= edge) current = SECTIONS[i][0];
      }
      if (root.scrollTop + root.clientHeight >= root.scrollHeight - 2) current = SECTIONS[SECTIONS.length - 1][0];
      setActive(current);
    };
    const schedule = () => { if (!frame) frame = requestAnimationFrame(update); };
    root.addEventListener("scroll", schedule, { passive: true });
    window.addEventListener("resize", schedule);
    // Loading tables and charts can move a section without a scroll event.
    const observer = new ResizeObserver(schedule);
    observer.observe(root);
    if (nav.current) observer.observe(nav.current);
    for (const target of targets) if (target) observer.observe(target);
    schedule();
    return () => {
      root.removeEventListener("scroll", schedule);
      window.removeEventListener("resize", schedule);
      observer.disconnect();
      cancelAnimationFrame(frame);
    };
  }, []);

  useEffect(() => {
    const container = strip.current;
    const link = container?.querySelector<HTMLElement>(`[href="#predictive-${active}"]`);
    if (!container || !link) return;
    const box = container.getBoundingClientRect(), item = link.getBoundingClientRect();
    if (item.left < box.left) container.scrollLeft -= box.left - item.left + 8;
    else if (item.right > box.right) container.scrollLeft += item.right - box.right + 8;
  }, [active]);

  return <nav ref={nav} aria-label="Predictive sections"
    className="sticky top-0 z-20 flex items-center gap-1 rounded-lg border border-border bg-ground/95 p-1.5 shadow-lg backdrop-blur">
    <button type="button" aria-label="Previous predictive sections" title="Scroll sections left"
      className="shrink-0 rounded px-2 py-2 text-muted hover:bg-panel2 hover:text-text focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent"
      onClick={() => strip.current?.scrollBy({ left: -(strip.current.clientWidth * .75) })}>‹</button>
    <div ref={strip} className="flex min-w-0 flex-1 gap-1 overflow-x-auto py-0.5">
      {SECTIONS.map(([id, label]) => <a key={id} href={`#predictive-${id}`}
        aria-current={active === id ? "location" : undefined}
        className={`shrink-0 whitespace-nowrap rounded-md border px-3 py-2 text-xs transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent ${active === id
          ? "border-accent/50 bg-accent/10 text-accent" : "border-transparent text-muted hover:bg-panel2 hover:text-text"}`}>
        {label}
      </a>)}
    </div>
    <button type="button" aria-label="More predictive sections" title="Scroll sections right"
      className="shrink-0 rounded px-2 py-2 text-muted hover:bg-panel2 hover:text-text focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent"
      onClick={() => strip.current?.scrollBy({ left: strip.current.clientWidth * .75 })}>›</button>
  </nav>;
}
