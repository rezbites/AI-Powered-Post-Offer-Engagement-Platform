import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // Risk colours are defined once here so a band never renders in two
        // different shades across the dashboard and the detail page.
        risk: {
          high: "#dc2626",
          medium: "#d97706",
          low: "#059669",
        },
      },
    },
  },
  plugins: [],
};

export default config;
