import { act, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ChatDocument, ChatSummary, RestoredMessage } from "@/lib/chats";
import { addDocument, clearDocuments, getDocuments } from "@/lib/documentStore";
import { clearThreadCookie, readThreadCookie, setThreadCookie } from "@/lib/thread";

import { ChatShell } from "./ChatShell";

// The real pane builds a chat runtime and would try to talk to /api/chat. What
// this test is about is which conversation it is handed, so it stands in for
// the pane and reports exactly that.
vi.mock("../MyAssistant", () => ({
  MyAssistant: ({
    threadId,
    initialMessages,
  }: {
    threadId?: string;
    initialMessages?: RestoredMessage[];
  }) => (
    <div data-testid="pane" data-thread={threadId}>
      {(initialMessages ?? []).map((message, index) => (
        <p key={index}>
          {message.content
            .map((part) => (part.type === "text" ? part.text : `[${part.toolName}]`))
            .join("")}
        </p>
      ))}
    </div>
  ),
}));

const chat = (overrides: Partial<ChatSummary> = {}): ChatSummary => ({
  id: "t1",
  title: "First chat",
  preview: "First chat",
  turnCount: 1,
  archived: false,
  createdAt: new Date().toISOString(),
  updatedAt: new Date().toISOString(),
  ...overrides,
});

const said = (role: "user" | "assistant", text: string): RestoredMessage =>
  ({ role, content: [{ type: "text", text }] }) as RestoredMessage;

/** Serves the chat list and per-thread transcripts, with failures per thread. */
function mockBackend({
  chats = [chat({ id: "t1", title: "First chat" }), chat({ id: "t2", title: "Second chat" })],
  transcripts = {} as Record<string, RestoredMessage[]>,
  documents = {} as Record<string, ChatDocument[]>,
  failFor = [] as string[],
} = {}) {
  const fetchMock = vi.fn(async (url: string) => {
    const perThread = url.match(/\/api\/chats\/([^/]+)\/(messages|documents)/);
    if (perThread) {
      const id = decodeURIComponent(perThread[1]!);
      if (failFor.includes(id)) {
        return { ok: false, status: 404, json: async () => ({}) };
      }
      const body =
        perThread[2] === "messages" ? (transcripts[id] ?? []) : (documents[id] ?? []);
      return { ok: true, status: 200, json: async () => body };
    }
    return { ok: true, status: 200, json: async () => chats };
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

const pane = () => screen.getByTestId("pane");

beforeEach(() => {
  clearThreadCookie();
  clearDocuments();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("ChatShell", () => {
  it("starts a new conversation when the browser has none", async () => {
    mockBackend();
    render(<ChatShell />);

    await waitFor(() => expect(pane()).toBeInTheDocument());
    const threadId = pane().getAttribute("data-thread");

    expect(threadId).toBeTruthy();
    // The cookie has to agree with the pane: it is what the proxy falls back
    // to, and what the next reload restores from.
    expect(readThreadCookie()).toBe(threadId);
    expect(screen.queryByText(/./, { selector: "[data-testid=pane] p" })).toBeNull();
  });

  it("restores the conversation the browser was last looking at", async () => {
    setThreadCookie("t1");
    mockBackend({ transcripts: { t1: [said("user", "hi"), said("assistant", "hello")] } });

    render(<ChatShell />);

    await waitFor(() => expect(pane()).toHaveAttribute("data-thread", "t1"));
    expect(screen.getByText("hi")).toBeInTheDocument();
    expect(screen.getByText("hello")).toBeInTheDocument();
  });

  it("loads a chat's history when it is picked from the sidebar", async () => {
    setThreadCookie("t1");
    mockBackend({
      transcripts: {
        t1: [said("user", "about france")],
        t2: [said("user", "about kafka"), said("assistant", "retention is 7d")],
      },
    });

    render(<ChatShell />);
    await waitFor(() => expect(pane()).toHaveAttribute("data-thread", "t1"));

    const second = (await screen.findByText("Second chat")).closest("button")!;
    await act(async () => second.click());

    await waitFor(() => expect(pane()).toHaveAttribute("data-thread", "t2"));
    expect(screen.getByText("retention is 7d")).toBeInTheDocument();
    expect(screen.queryByText("about france")).not.toBeInTheDocument();
    expect(readThreadCookie()).toBe("t2");
  });

  it("forgets the previous chat's documents when switching", async () => {
    // Otherwise the next conversation's system prompt announces a document it
    // never saw, and the model is told it can search something that is not
    // part of that thread.
    setThreadCookie("t1");
    mockBackend({ transcripts: { t1: [], t2: [] } });

    render(<ChatShell />);
    await waitFor(() => expect(pane()).toHaveAttribute("data-thread", "t1"));

    addDocument({
      id: "doc-1",
      name: "big.txt",
      sections: 87,
      tier: "consider_retrieval",
      message: "87 chunks",
    });
    expect(getDocuments()).toHaveLength(1);

    const second = (await screen.findByText("Second chat")).closest("button")!;
    await act(async () => second.click());

    await waitFor(() => expect(pane()).toHaveAttribute("data-thread", "t2"));
    expect(getDocuments()).toHaveLength(0);
  });

  it("keeps you where you are when a chat will not open", async () => {
    setThreadCookie("t1");
    mockBackend({ transcripts: { t1: [said("user", "about france")] }, failFor: ["t2"] });

    render(<ChatShell />);
    await waitFor(() => expect(pane()).toHaveAttribute("data-thread", "t1"));

    const second = (await screen.findByText("Second chat")).closest("button")!;
    await act(async () => second.click());

    expect(await screen.findByRole("alert")).toHaveTextContent("Could not open");
    // Still reading the conversation that was open, not an empty pane.
    expect(pane()).toHaveAttribute("data-thread", "t1");
    expect(screen.getByText("about france")).toBeInTheDocument();
  });

  it("falls back to a new chat when the remembered one is gone", async () => {
    // Deleted in another tab, or belonging to a different user after
    // SINGLE_USER_ID changed: an error page you cannot leave would be worse.
    setThreadCookie("deleted-thread");
    mockBackend({ failFor: ["deleted-thread"] });

    render(<ChatShell />);

    await waitFor(() => expect(pane()).toBeInTheDocument());
    expect(pane().getAttribute("data-thread")).not.toBe("deleted-thread");
    expect(readThreadCookie()).toBe(pane().getAttribute("data-thread"));
  });

  it("gives a new chat a fresh id rather than reusing the open one", async () => {
    setThreadCookie("t1");
    mockBackend({ transcripts: { t1: [said("user", "about france")] } });

    render(<ChatShell />);
    await waitFor(() => expect(pane()).toHaveAttribute("data-thread", "t1"));

    await act(async () =>
      screen.getByRole("button", { name: "New chat" }).click(),
    );

    await waitFor(() =>
      expect(pane().getAttribute("data-thread")).not.toBe("t1"),
    );
    // A new conversation must not open showing the old one's messages.
    expect(screen.queryByText("about france")).not.toBeInTheDocument();
  });
  const doc = (overrides: Partial<ChatDocument> = {}): ChatDocument => ({
    id: "doc-1",
    name: "big.txt",
    sections: 87,
    status: "ready",
    tier: "consider_retrieval",
    message: "87 chunks",
    ...overrides,
  });

  it("restores what a chat has attached when it is opened", async () => {
    setThreadCookie("t1");
    mockBackend({ documents: { t2: [doc({ name: "kafka-spec.txt" })] } });

    render(<ChatShell />);
    await waitFor(() => expect(pane()).toHaveAttribute("data-thread", "t1"));
    expect(getDocuments()).toHaveLength(0);

    const second = (await screen.findByText("Second chat")).closest("button")!;
    await act(async () => second.click());

    await waitFor(() => expect(getDocuments()).toHaveLength(1));
    expect(getDocuments()[0]!.name).toBe("kafka-spec.txt");
  });

  it("brings a chat's documents back when you return to it", async () => {
    // The point of the whole phase: leaving a conversation used to lose the
    // association, so coming back left an answer citing "[Section 148]" with
    // nothing to resolve it against.
    setThreadCookie("t1");
    mockBackend({ documents: { t1: [doc()], t2: [] } });

    render(<ChatShell />);
    await waitFor(() => expect(getDocuments()).toHaveLength(1));

    const second = (await screen.findByText("Second chat")).closest("button")!;
    await act(async () => second.click());
    await waitFor(() => expect(getDocuments()).toHaveLength(0));

    const first = screen.getByText("First chat").closest("button")!;
    await act(async () => first.click());

    await waitFor(() => expect(getDocuments()).toHaveLength(1));
    expect(getDocuments()[0]!.name).toBe("big.txt");
  });

  it("restores attachments on reload, without localStorage", async () => {
    // A fresh page load: nothing in memory, nothing mirrored in the browser -
    // the association comes back because the backend holds it.
    setThreadCookie("t1");
    mockBackend({ documents: { t1: [doc()] } });

    render(<ChatShell />);

    await waitFor(() => expect(getDocuments()).toHaveLength(1));
    expect(window.localStorage.length).toBe(0);
  });

  it("starts a new chat with nothing attached", async () => {
    setThreadCookie("t1");
    mockBackend({ documents: { t1: [doc()] } });

    render(<ChatShell />);
    await waitFor(() => expect(getDocuments()).toHaveLength(1));

    await act(async () =>
      screen.getByRole("button", { name: "New chat" }).click(),
    );

    expect(getDocuments()).toHaveLength(0);
  });
});
