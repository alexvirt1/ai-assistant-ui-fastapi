import { afterEach, describe, expect, it, vi } from "vitest";

import {
  deleteChat,
  fetchChatDocuments,
  fetchChatMessages,
  listChats,
  renameChat,
} from "./chats";

function mockFetch(response: Partial<Response> & { json?: () => unknown }) {
  // Typed via the generic rather than by declaring unused parameters: without
  // a signature the mock's recorded calls are empty tuples, and the assertions
  // below cannot index them.
  const fetchMock = vi.fn<(url: string, init?: RequestInit) => Promise<Response>>(
    async () =>
      ({
        ok: true,
        status: 200,
        json: async () => [],
        ...response,
      }) as Response,
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("listChats", () => {
  it("asks for everything when no query is given", async () => {
    const fetchMock = mockFetch({});
    await listChats();
    expect(fetchMock.mock.calls[0]![0]).toBe("/api/chats");
  });

  it("passes the search term as ?q=", async () => {
    const fetchMock = mockFetch({});
    await listChats("postgres");
    expect(fetchMock.mock.calls[0]![0]).toBe("/api/chats?q=postgres");
  });

  it("escapes a query that would otherwise alter the url", async () => {
    const fetchMock = mockFetch({});
    await listChats("a&limit=999");
    expect(fetchMock.mock.calls[0]![0]).toBe("/api/chats?q=a%26limit%3D999");
  });

  it("throws with the status when the backend rejects", async () => {
    mockFetch({ ok: false, status: 500 });
    await expect(listChats()).rejects.toThrow("Loading chats failed (500)");
  });
});

describe("fetchChatMessages", () => {
  it("encodes the thread id into the path", async () => {
    const fetchMock = mockFetch({ json: async () => [] });
    await fetchChatMessages("a/b?c");
    expect(fetchMock.mock.calls[0]![0]).toBe("/api/chats/a%2Fb%3Fc/messages");
  });

  it("returns the restored transcript", async () => {
    const transcript = [{ role: "user", content: [{ type: "text", text: "hi" }] }];
    mockFetch({ json: async () => transcript });
    await expect(fetchChatMessages("t1")).resolves.toEqual(transcript);
  });
});

describe("renameChat", () => {
  it("sends the new title as a patch", async () => {
    const fetchMock = mockFetch({ json: async () => ({ id: "t1", title: "New" }) });
    await renameChat("t1", "New");

    const [url, init] = fetchMock.mock.calls[0]! as [string, RequestInit];
    expect(url).toBe("/api/chats/t1");
    expect(init.method).toBe("PATCH");
    expect(JSON.parse(init.body as string)).toEqual({ title: "New" });
  });
});

describe("deleteChat", () => {
  it("treats a missing chat as already deleted", async () => {
    // The sidebar's own refresh can race a delete from another tab; surfacing
    // that as an error would report a failure for the state the user wanted.
    mockFetch({ ok: false, status: 404 });
    await expect(deleteChat("t1")).resolves.toBeUndefined();
  });

  it("still reports a real failure", async () => {
    mockFetch({ ok: false, status: 500 });
    await expect(deleteChat("t1")).rejects.toThrow("Deleting chat failed (500)");
  });
});

describe("fetchChatDocuments", () => {
  it("asks the thread's own documents endpoint", async () => {
    const fetchMock = mockFetch({ json: async () => [] });
    await fetchChatDocuments("t1");
    expect(fetchMock.mock.calls[0]![0]).toBe("/api/chats/t1/documents");
  });

  it("throws rather than reporting nothing attached", async () => {
    // Returning [] on failure would tell the user this conversation has no
    // documents, which is a different and wrong statement.
    mockFetch({ ok: false, status: 500 });
    await expect(fetchChatDocuments("t1")).rejects.toThrow(
      "Loading documents failed (500)",
    );
  });
});
