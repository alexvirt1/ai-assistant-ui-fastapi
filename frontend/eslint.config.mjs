// Flat config, required by ESLint 9+.
//
// Replaces .eslintrc.json: Next 16 removed the `next lint` command, and
// eslint-config-next 16 requires ESLint >= 9, which defaults to flat config.
// The two entries below are the flat equivalents of the previous
// extends: ["next/core-web-vitals", "next/typescript"].
import coreWebVitals from "eslint-config-next/core-web-vitals";
import typescript from "eslint-config-next/typescript";

const config = [
  ...coreWebVitals,
  ...typescript,
  {
    ignores: [".next/**", ".next-verify/**", "node_modules/**"],
  },
];

export default config;
