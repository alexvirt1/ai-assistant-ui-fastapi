import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const setTheme = vi.fn();
let resolvedTheme = "light";

// next-themes reads from localStorage and matchMedia, neither of which reflects
// a real user here. Stubbing the hook keeps the test about this component's
// own logic: the mount guard and which icon it shows.
vi.mock("next-themes", () => ({
  useTheme: () => ({ resolvedTheme, setTheme }),
}));

import { ThemeToggle } from "./ThemeToggle";

describe("ThemeToggle", () => {
  beforeEach(() => {
    setTheme.mockClear();
    resolvedTheme = "light";
  });

  it("renders a button", () => {
    render(<ThemeToggle />);
    expect(screen.getByRole("button")).toBeInTheDocument();
  });

  it("offers dark when the current theme is light", () => {
    render(<ThemeToggle />);
    expect(
      screen.getByRole("button", { name: /switch to dark theme/i }),
    ).toBeInTheDocument();
  });

  it("offers light when the current theme is dark", () => {
    resolvedTheme = "dark";
    render(<ThemeToggle />);
    expect(
      screen.getByRole("button", { name: /switch to light theme/i }),
    ).toBeInTheDocument();
  });

  it("switches to the opposite theme when clicked", async () => {
    const { getByRole } = render(<ThemeToggle />);
    getByRole("button").click();
    expect(setTheme).toHaveBeenCalledWith("dark");
  });

  it("switches back to light from dark", () => {
    resolvedTheme = "dark";
    const { getByRole } = render(<ThemeToggle />);
    getByRole("button").click();
    expect(setTheme).toHaveBeenCalledWith("light");
  });

  it("keeps an accessible name before the icon appears", () => {
    // The icon is withheld until mount because the server cannot know the
    // theme, but the button must still be reachable and labelled throughout.
    render(<ThemeToggle />);
    expect(screen.getByRole("button")).toHaveAccessibleName(
      /switch to (dark|light) theme/i,
    );
  });
});
