import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ChatSummary } from "@/lib/chats";

import { ChatSidebar } from "./ChatSidebar";

const chat = (overrides: Partial<ChatSummary> = {}): ChatSummary => ({
  id: "t1",
  title: "What is the capital of France?",
  preview: "What is the capital of France?",
  turnCount: 1,
  archived: false,
  createdAt: new Date().toISOString(),
  updatedAt: new Date().toISOString(),
  ...overrides,
});

function mockChats(byQuery: (query: string | null) => ChatSummary[]) {
  const fetchMock = vi.fn(async (url: string) => {
    const query = new URL(url, "http://localhost").searchParams.get("q");
    return {
      ok: true,
      status: 200,
      json: async () => byQuery(query),
    };
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

/** Types character by character, as a debounce has to be measured against. */
async function typeSearch(text: string) {
  const input = screen.getByLabelText("Search chats");
  for (let i = 1; i <= text.length; i++) {
    await act(async () => {
      fireEvent.change(input, { target: { value: text.slice(0, i) } });
    });
  }
}

const noop = () => {};

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("ChatSidebar", () => {
  it("lists the chats it loads", async () => {
    mockChats(() => [chat(), chat({ id: "t2", title: "Kafka retention" })]);

    render(
      <ChatSidebar activeId={null} refreshKey={0} onSelect={noop} onNew={noop} />,
    );

    expect(
      await screen.findByText("What is the capital of France?"),
    ).toBeInTheDocument();
    expect(screen.getByText("Kafka retention")).toBeInTheDocument();
  });

  it("says so when there are no chats yet", async () => {
    mockChats(() => []);
    render(
      <ChatSidebar activeId={null} refreshKey={0} onSelect={noop} onNew={noop} />,
    );
    expect(await screen.findByText("No chats yet.")).toBeInTheDocument();
  });

  it("distinguishes an empty search from an empty history", async () => {
    mockChats((query) => (query ? [] : [chat()]));

    render(
      <ChatSidebar activeId={null} refreshKey={0} onSelect={noop} onNew={noop} />,
    );
    await screen.findByText("What is the capital of France?");

    await typeSearch("zzz");

    expect(await screen.findByText("No chats match.")).toBeInTheDocument();
  });

  it("searches server-side rather than filtering what it already has", async () => {
    // The list is paginated, so filtering client-side would silently search
    // only the most recent page.
    const fetchMock = mockChats(() => [chat()]);

    render(
      <ChatSidebar activeId={null} refreshKey={0} onSelect={noop} onNew={noop} />,
    );
    await screen.findByText("What is the capital of France?");

    await typeSearch("kafka");

    await waitFor(() => {
      const urls = fetchMock.mock.calls.map(([url]) => url as string);
      expect(urls.some((url) => url.includes("q=kafka"))).toBe(true);
    });
  });

  it("does not fire a request per keystroke", async () => {
    const fetchMock = mockChats(() => [chat()]);

    render(
      <ChatSidebar activeId={null} refreshKey={0} onSelect={noop} onNew={noop} />,
    );
    await screen.findByText("What is the capital of France?");
    const before = fetchMock.mock.calls.length;

    await typeSearch("retention");

    await waitFor(() => {
      const urls = fetchMock.mock.calls.map(([url]) => url as string);
      expect(urls.some((url) => url.includes("q=retention"))).toBe(true);
    });
    // Nine characters typed; debouncing should collapse them well below that.
    expect(fetchMock.mock.calls.length - before).toBeLessThan(9);
  });

  it("selects a chat when it is clicked", async () => {
    mockChats(() => [chat({ id: "t2", title: "Kafka retention" })]);
    const onSelect = vi.fn();

    render(
      <ChatSidebar activeId={null} refreshKey={0} onSelect={onSelect} onNew={noop} />,
    );
    const item = (await screen.findByText("Kafka retention")).closest("button")!;
    await act(async () => item.click());

    expect(onSelect).toHaveBeenCalledWith("t2");
  });

  it("marks the open chat and does not re-open it", async () => {
    mockChats(() => [chat()]);
    const onSelect = vi.fn();

    render(
      <ChatSidebar activeId="t1" refreshKey={0} onSelect={onSelect} onNew={noop} />,
    );

    const item = (
      await screen.findByText("What is the capital of France?")
    ).closest("button")!;
    expect(item).toHaveAttribute("aria-current", "true");

    await act(async () => item.click());
    expect(onSelect).not.toHaveBeenCalled();
  });

  it("blocks switching and starting a new chat while a response streams", async () => {
    // Switching mid-run would remount the runtime and abandon the answer being
    // written, with no indication that it had been thrown away.
    mockChats(() => [chat()]);
    const onSelect = vi.fn();
    const onNew = vi.fn();

    render(
      <ChatSidebar
        activeId={null}
        refreshKey={0}
        disabled
        onSelect={onSelect}
        onNew={onNew}
      />,
    );

    const item = (
      await screen.findByText("What is the capital of France?")
    ).closest("button")!;
    await act(async () => item.click());
    await act(async () =>
      screen.getByRole("button", { name: "New chat" }).click(),
    );

    expect(onSelect).not.toHaveBeenCalled();
    expect(onNew).not.toHaveBeenCalled();
  });

  it("starts a new chat when the button is clicked", async () => {
    mockChats(() => []);
    const onNew = vi.fn();

    render(
      <ChatSidebar activeId={null} refreshKey={0} onSelect={noop} onNew={onNew} />,
    );
    await act(async () =>
      screen.getByRole("button", { name: "New chat" }).click(),
    );

    expect(onNew).toHaveBeenCalled();
  });

  it("refetches when the caller bumps refreshKey", async () => {
    // This is how a finished turn gets its backend-derived title into the list.
    let title = "Untitled chat";
    const fetchMock = mockChats(() => [chat({ title })]);

    const { rerender } = render(
      <ChatSidebar activeId={null} refreshKey={0} onSelect={noop} onNew={noop} />,
    );
    await screen.findByText("Untitled chat");

    title = "What is the capital of France?";
    rerender(
      <ChatSidebar activeId={null} refreshKey={1} onSelect={noop} onNew={noop} />,
    );

    expect(
      await screen.findByText("What is the capital of France?"),
    ).toBeInTheDocument();
    expect(fetchMock.mock.calls.length).toBeGreaterThan(1);
  });

  it("reports a failure instead of looking empty", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({ ok: false, status: 500, json: async () => ({}) })),
    );

    render(
      <ChatSidebar activeId={null} refreshKey={0} onSelect={noop} onNew={noop} />,
    );

    expect(await screen.findByText(/Loading chats failed/)).toBeInTheDocument();
  });
});
