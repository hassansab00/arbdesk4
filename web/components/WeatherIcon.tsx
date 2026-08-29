// Animated condition icons (Task 14 §8.4). CSS/SVG only, no library -
// keyframes defined in app/globals.css, respecting prefers-reduced-motion.
export default function WeatherIcon({ condition, size = 40 }: { condition: string | null; size?: number }) {
  const s = size;
  switch (condition) {
    case "CLEAR":
      return (
        <svg width={s} height={s} viewBox="0 0 40 40" className="wx-anim">
          <circle cx="20" cy="20" r="8" fill="#ffb020" />
          <g className="wx-sun" style={{ transformOrigin: "20px 20px" }} stroke="#ffb020" strokeWidth="2">
            {[0, 45, 90, 135, 180, 225, 270, 315].map((deg) => (
              <line key={deg} x1="20" y1="4" x2="20" y2="9" transform={`rotate(${deg} 20 20)`} />
            ))}
          </g>
        </svg>
      );
    case "PARTLY_CLOUDY":
      return (
        <svg width={s} height={s} viewBox="0 0 40 40" className="wx-anim">
          <circle cx="16" cy="16" r="7" fill="#ffb020" />
          <g className="wx-cloud-fast" style={{ transformOrigin: "20px 24px" }}>
            <ellipse cx="22" cy="26" rx="12" ry="7" fill="#c7cedd" />
          </g>
        </svg>
      );
    case "CLOUDY":
      return (
        <svg width={s} height={s} viewBox="0 0 40 40" className="wx-anim">
          <g className="wx-cloud-slow" style={{ transformOrigin: "16px 16px" }}><ellipse cx="16" cy="16" rx="10" ry="6" fill="#8a93a6" /></g>
          <g className="wx-cloud-fast" style={{ transformOrigin: "24px 24px" }}><ellipse cx="24" cy="24" rx="12" ry="7" fill="#aeb6c7" /></g>
        </svg>
      );
    case "OVERCAST":
      return (
        <svg width={s} height={s} viewBox="0 0 40 40" className="wx-anim">
          <g className="wx-cloud-slow" style={{ transformOrigin: "20px 20px" }}>
            <ellipse cx="14" cy="18" rx="10" ry="6" fill="#5b6478" />
            <ellipse cx="24" cy="22" rx="13" ry="7" fill="#6d7690" />
          </g>
        </svg>
      );
    case "RAIN":
      return (
        <svg width={s} height={s} viewBox="0 0 40 40" className="wx-anim">
          <ellipse cx="20" cy="14" rx="12" ry="7" fill="#6d7690" />
          {[10, 18, 26].map((x, i) => (
            <line key={x} className="wx-drop" style={{ animationDelay: `${i * 0.3}s` }} x1={x} y1="22" x2={x} y2="28" stroke="#4f8cff" strokeWidth="2" />
          ))}
        </svg>
      );
    case "SNOW":
      return (
        <svg width={s} height={s} viewBox="0 0 40 40" className="wx-anim">
          <ellipse cx="20" cy="14" rx="12" ry="7" fill="#8a93a6" />
          {[10, 18, 26].map((x, i) => (
            <circle key={x} className="wx-flake" style={{ animationDelay: `${i * 0.5}s` }} cx={x} cy="22" r="1.6" fill="#e6e9f0" />
          ))}
        </svg>
      );
    case "FOG":
      return (
        <svg width={s} height={s} viewBox="0 0 40 40" className="wx-anim">
          {[12, 18, 24].map((y, i) => (
            <rect key={y} className="wx-fog-band" style={{ animationDelay: `${i * 0.7}s` }} x="4" y={y} width="32" height="3" rx="1.5" fill="#8a93a6" opacity={0.6} />
          ))}
        </svg>
      );
    case "STORM":
      return (
        <svg width={s} height={s} viewBox="0 0 40 40" className="wx-anim">
          <ellipse cx="20" cy="14" rx="12" ry="7" fill="#454e63" />
          <polygon className="wx-bolt" points="18,20 24,20 19,28 23,28 16,36 18,26 14,26" fill="#ffb020" />
        </svg>
      );
    default:
      return <div style={{ width: s, height: s }} className="rounded bg-border" />;
  }
}
