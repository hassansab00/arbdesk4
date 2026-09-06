import type { Config } from "tailwindcss";

const config: Config = {
  darkMode: "class",
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}", "./lib/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // NOT "base". A palette colour called `base` shadows Tailwind's
        // `text-base` FONT SIZE utility - `text-base` then paints text in the
        // page's own background colour and every heading using it becomes
        // invisible. That is exactly what happened to all four Analytics
        // group headings and all four Data Bank section headings, and it is
        // invisible in a diff, in a typecheck, and in a build.
        ground: "#0b0e14",
        panel: "#12161f",
        panel2: "#171c27",
        border: "#232a38",
        text: "#e6e9f0",
        muted: "#8a93a6",
        accent: "#4f8cff",
        good: "#2ecc71",
        bad: "#ff5c5c",
        warn: "#ffb020",
      },
      fontFamily: {
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
    },
  },
  plugins: [],
};
export default config;
