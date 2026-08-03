import type { Config } from "tailwindcss";
import assistantUi from "@assistant-ui/react/tailwindcss";
import assistantUiMarkdown from "@assistant-ui/react-markdown/tailwindcss";
import animate from "tailwindcss-animate";

const config = {
  darkMode: ["class"],
  content: [
    "./pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  plugins: [animate, assistantUi, assistantUiMarkdown],
} satisfies Config;

export default config;
