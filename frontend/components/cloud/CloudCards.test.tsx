import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const append = vi.fn();
let running = false;
vi.mock("@assistant-ui/react", () => ({
  makeAssistantToolUI: (config: unknown) => config,
  useThread: (select: (t: { isRunning: boolean; messages: unknown[] }) => unknown) =>
    select({ isRunning: running, messages: [] }),
  useThreadRuntime: () => ({ append }),
}));

import { getHybrid, resetHybrid } from "@/lib/hybrid";

import { ASK_CLOUD_TEXT, CloudBadge, EscalationCard, KEEP_LOCAL_TEXT } from "./CloudCards";

beforeEach(() => {
  append.mockReset();
  running = false;
  resetHybrid("auto");
});

describe("EscalationCard", () => {
  it("sends the next turn to the cloud only when asked", async () => {
    render(<EscalationCard reason="exact arithmetic" model="Claude Sonnet 5.5" isLatest />);
    expect(screen.getByText(/suggests asking/).textContent).toContain("exact arithmetic");
    expect(getHybrid().escalateNext).toBe(false);
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Ask Claude Sonnet 5.5" })));
    expect(getHybrid().escalateNext).toBe(true);
    expect(append).toHaveBeenCalledWith(ASK_CLOUD_TEXT);
    expect(screen.getByRole("button", { name: "Sent to Claude Sonnet 5.5" })).toBeDisabled();
  });

  it("keeps the turn local", async () => {
    render(<EscalationCard reason="" model="Claude Sonnet 5.5" isLatest />);
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Keep local" })));
    expect(append).toHaveBeenCalledWith(KEEP_LOCAL_TEXT);
    expect(getHybrid().escalateNext).toBe(false);
  });

  it("cannot be used once the conversation has moved on", () => {
    render(<EscalationCard reason="" model="Claude Sonnet 5.5" isLatest={false} />);
    expect(screen.getByRole("button", { name: "Ask Claude Sonnet 5.5" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Keep local" })).toBeDisabled();
  });
});

describe("CloudBadge", () => {
  it("names the model, tokens and cost", () => {
    render(<CloudBadge model="Claude Sonnet 5.5" inputTokens={2500} outputTokens={50} cost={0.0055} />);
    expect(screen.getByText("☁ Claude Sonnet 5.5 · 2.5k tokens · $0.0055")).toBeTruthy();
  });
});
