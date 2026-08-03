"use client";

import { useTheme } from "next-themes";
import { useEffect, useState } from "react";

/**
 * Switches between the light and dark palettes.
 *
 * The server cannot know which theme the browser will resolve to, so rendering
 * the icon before mount would produce a hydration mismatch. Until mounted the
 * button renders empty at its final size, which also avoids a layout shift when
 * the icon appears.
 *
 * `resolvedTheme` rather than `theme`: with the default "system" preference,
 * `theme` is the string "system" while `resolvedTheme` is the light/dark it
 * actually resolved to, which is what the icon and the next click depend on.
 */
export function ThemeToggle() {
  const { resolvedTheme, setTheme } = useTheme();
  const [mounted, setMounted] = useState(false);

  // Intentional: this is the documented next-themes mount guard. The theme is
  // only knowable in the browser, so the first client render must match the
  // server's (icon-less) output and the icon appears on the commit after mount.
  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => setMounted(true), []);

  const isDark = resolvedTheme === "dark";
  const label = isDark ? "Switch to light theme" : "Switch to dark theme";

  return (
    <button
      type="button"
      onClick={() => setTheme(isDark ? "light" : "dark")}
      aria-label={label}
      title={mounted ? label : undefined}
      className="flex size-8 items-center justify-center rounded-md border border-gray-200 text-base leading-none transition-colors hover:bg-gray-100 dark:border-gray-700 dark:hover:bg-gray-800"
    >
      <span aria-hidden>{mounted ? (isDark ? "☀️" : "🌙") : null}</span>
    </button>
  );
}
