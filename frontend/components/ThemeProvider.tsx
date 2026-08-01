"use client";

import { ThemeProvider as NextThemesProvider } from "next-themes";
import type { ComponentProps } from "react";

/**
 * Client wrapper so the provider can be mounted from the server-rendered root
 * layout.
 *
 * `attribute="class"` puts a `dark` class on <html>, which is what both
 * tailwind.config.ts (`darkMode: ["class"]`) and assistant-ui's tailwind plugin
 * key off — the plugin already ships a full `.dark` palette for its --aui-*
 * variables, so the whole chat surface follows automatically.
 */
export function ThemeProvider({
  children,
  ...props
}: ComponentProps<typeof NextThemesProvider>) {
  return <NextThemesProvider {...props}>{children}</NextThemesProvider>;
}
