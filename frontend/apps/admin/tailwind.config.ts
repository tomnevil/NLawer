import type { Config } from "tailwindcss";
import preset from "../../tailwind.preset";

const config: Config = {
  presets: [preset],
  darkMode: "class",
  content: [
    "./app/**/*.{ts,tsx}",
    "../../packages/ui/src/**/*.{ts,tsx}",
    "../../packages/sdk/src/**/*.{ts,tsx}",
  ],
  theme: { extend: {} },
};

export default config;
