import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@assistant-ui/react", () => ({
  useThread: (select: (t: { isRunning: boolean; messages: unknown[] }) => unknown) =>
    select({ isRunning: false, messages: [] }),
}));

import { getHybrid, resetHybrid } from "@/lib/hybrid";

import { HybridControls } from "./HybridControls";

const configured = {
  configured: true,
  model: { name: "claude-sonnet", label: "Claude Sonnet 5.5", model: "claude-sonnet-5-5" },
  budget_usd: 10,
  month_spent_usd: 0.42,
};

function backend(status: unknown, chat: unknown = { mode: "auto", allowCloudDocuments: false }) {
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if (url === "/api/cloud") return { ok: true, json: async () => status } as Response;
    if (init?.method === "PATCH") return { ok: true, json: async () => ({}) } as Response;
    return { ok: true, json: async () => chat } as Response;
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

beforeEach(() => resetHybrid());
afterEach(() => vi.unstubAllGlobals());

describe("HybridControls", () => {
  it("is hidden without a cloud model", async () => {
    backend({ configured: false, model: null, budget_usd: 10, month_spent_usd: 0 });
    const { container } = render(<HybridControls threadId="t1" />);
    await act(async () => {});
    expect(container.querySelector("select")).toBeNull();
  });

  it("loads the chat's mode and saves a change", async () => {
    const fetchMock = backend(configured);
    render(<HybridControls threadId="t1" />);
    const select = await screen.findByRole("combobox", { name: "Who answers in this chat" });
    await waitFor(() => expect((select as HTMLSelectElement).value).toBe("auto"));
    expect(select.getAttribute("title")).toBe("Cloud spend: $0.42 of $10.00 this month");

    await act(async () => fireEvent.change(select, { target: { value: "cloud" } }));
    expect(getHybrid().mode).toBe("cloud");
    const patch = fetchMock.mock.calls.find(([, init]) => (init as RequestInit)?.method === "PATCH");
    expect(JSON.parse((patch![1] as RequestInit).body as string)).toEqual({ mode: "cloud" });

    await act(async () => fireEvent.click(screen.getByRole("checkbox")));
    expect(getHybrid().allowDocuments).toBe(true);
  });
});
