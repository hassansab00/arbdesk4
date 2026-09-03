"use client";

/**
 * The ArbDesk mark: a thermometer with a sun and a snowflake in orbit.
 *
 * It is the desk's whole thesis in one glyph - the trade is the distance
 * between hot and cold, and the thermometer is what settles it. The orbit
 * runs slowly (18s) because a masthead animation that demands attention is
 * a masthead animation you turn off after a day.
 *
 * Pure SVG + CSS: no image request, no layout shift, and it inherits
 * currentColor so it works on either theme. `prefers-reduced-motion` stops
 * the orbit and leaves the mark composed rather than frozen mid-sweep.
 */
export default function Brand() {
  return (
    <div className="flex items-center gap-2.5 select-none">
      <svg
        viewBox="0 0 40 40"
        className="ad4-mark h-8 w-8 shrink-0"
        role="img"
        aria-label="ArbDesk"
      >
        {/* orbit path, barely there */}
        <circle cx="20" cy="20" r="15" className="ad4-orbit" fill="none" strokeWidth="0.75" />

        {/* thermometer: stem, bulb, mercury, graduations */}
        <g className="ad4-thermo">
          <rect x="17.6" y="7" width="4.8" height="19" rx="2.4"
                fill="none" stroke="currentColor" strokeWidth="1.5" />
          <circle cx="20" cy="29" r="4.6" fill="none" stroke="currentColor" strokeWidth="1.5" />
          <rect className="ad4-mercury" x="18.9" y="14" width="2.2" height="13" rx="1.1" />
          <circle className="ad4-mercury" cx="20" cy="29" r="3" />
          <g stroke="currentColor" strokeWidth="0.9" opacity="0.55" strokeLinecap="round">
            <line x1="23.4" y1="11.5" x2="25.6" y2="11.5" />
            <line x1="23.4" y1="15.5" x2="25.2" y2="15.5" />
            <line x1="23.4" y1="19.5" x2="25.6" y2="19.5" />
          </g>
        </g>

        {/* sun and snowflake, half an orbit apart */}
        <g className="ad4-spin">
          <g className="ad4-sun" transform="translate(20 5)">
            <circle r="2.6" />
            <g strokeWidth="1.1" strokeLinecap="round">
              <line x1="0" y1="-4.6" x2="0" y2="-3.6" />
              <line x1="0" y1="3.6" x2="0" y2="4.6" />
              <line x1="-4.6" y1="0" x2="-3.6" y2="0" />
              <line x1="3.6" y1="0" x2="4.6" y2="0" />
              <line x1="-3.3" y1="-3.3" x2="-2.5" y2="-2.5" />
              <line x1="2.5" y1="2.5" x2="3.3" y2="3.3" />
              <line x1="2.5" y1="-2.5" x2="3.3" y2="-3.3" />
              <line x1="-3.3" y1="3.3" x2="-2.5" y2="2.5" />
            </g>
          </g>
          <g className="ad4-flake" transform="translate(20 35)" strokeWidth="1.1" strokeLinecap="round">
            <line x1="0" y1="-4" x2="0" y2="4" />
            <line x1="-3.5" y1="-2" x2="3.5" y2="2" />
            <line x1="-3.5" y1="2" x2="3.5" y2="-2" />
            <g strokeWidth="0.9">
              <line x1="0" y1="-4" x2="-1.4" y2="-2.6" /><line x1="0" y1="-4" x2="1.4" y2="-2.6" />
              <line x1="0" y1="4" x2="-1.4" y2="2.6" /><line x1="0" y1="4" x2="1.4" y2="2.6" />
            </g>
          </g>
        </g>
      </svg>

      <div className="leading-none">
        <div className="text-[15px] font-bold tracking-tight">
          Arb<span className="text-accent">Desk</span>
        </div>
        <div className="mt-0.5 font-mono text-[9.5px] uppercase tracking-[0.18em] text-muted">
          temperature markets
        </div>
      </div>
    </div>
  );
}
