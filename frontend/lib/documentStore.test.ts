import { beforeEach, describe, expect, it, vi } from "vitest";

import type { UploadedDocument } from "./attachments";
import {
  addDocument,
  clearDocuments,
  clearDocumentsInMemoryOnly,
  hydrateDocuments,
  getDocuments,
  setDocumentStatus,
  subscribe,
} from "./documentStore";

const uploaded = (overrides: Partial<UploadedDocument> = {}): UploadedDocument => ({
  id: "doc-1",
  name: "big.txt",
  sections: 87,
  tier: "consider_retrieval",
  message: "87 chunks, about 45 minutes.",
  ...overrides,
});

describe("documentStore", () => {
  beforeEach(() => {
    clearDocuments();
  });

  it("starts empty", () => {
    expect(getDocuments()).toEqual([]);
  });

  it("records a document as indexing, with its scope detail", () => {
    addDocument(uploaded());
    expect(getDocuments()).toEqual([
      {
        id: "doc-1",
        name: "big.txt",
        sections: 87,
        status: "indexing",
        tier: "consider_retrieval",
        message: "87 chunks, about 45 minutes.",
      },
    ]);
  });

  it("ignores a document it already has", () => {
    addDocument(uploaded());
    addDocument(uploaded());
    expect(getDocuments()).toHaveLength(1);
  });

  it("keeps a settled status when the same document is re-added", () => {
    // Re-attaching the same file must not knock a ready document back to
    // "indexing" and make the UI claim work that is not happening.
    addDocument(uploaded());
    setDocumentStatus("doc-1", "ready");
    addDocument(uploaded());
    expect(getDocuments()[0]!.status).toBe("ready");
  });

  it("returns a stable reference between changes", () => {
    // useSyncExternalStore compares by reference and throws an infinite-loop
    // error if the snapshot is a fresh array every call.
    addDocument(uploaded());
    expect(getDocuments()).toBe(getDocuments());
  });

  describe("status", () => {
    it("moves a document to ready", () => {
      addDocument(uploaded());
      setDocumentStatus("doc-1", "ready");
      expect(getDocuments()[0]!.status).toBe("ready");
    });

    it("moves a document to failed", () => {
      addDocument(uploaded());
      setDocumentStatus("doc-1", "failed");
      expect(getDocuments()[0]!.status).toBe("failed");
    });

    it("leaves other documents alone", () => {
      addDocument(uploaded());
      addDocument(uploaded({ id: "doc-2", name: "other.txt" }));
      setDocumentStatus("doc-2", "ready");

      expect(getDocuments().map((d) => d.status)).toEqual(["indexing", "ready"]);
    });

    it("ignores an unknown id rather than throwing", () => {
      // A stale index response can land after the conversation was cleared.
      expect(() => setDocumentStatus("gone", "ready")).not.toThrow();
      expect(getDocuments()).toEqual([]);
    });

    it("preserves the other fields", () => {
      addDocument(uploaded());
      setDocumentStatus("doc-1", "ready");
      expect(getDocuments()[0]).toMatchObject({
        name: "big.txt",
        sections: 87,
        message: "87 chunks, about 45 minutes.",
      });
    });
  });

  describe("notifications", () => {
    it("notifies subscribers when a document is added", () => {
      const listener = vi.fn();
      subscribe(listener);
      addDocument(uploaded());
      expect(listener).toHaveBeenCalledTimes(1);
    });

    it("notifies subscribers when a status changes", () => {
      // Without this the chip would sit on "preparing…" forever even though
      // indexing had finished.
      addDocument(uploaded());
      const listener = vi.fn();
      const unsubscribe = subscribe(listener);
      setDocumentStatus("doc-1", "ready");
      unsubscribe();

      expect(listener).toHaveBeenCalledTimes(1);
      expect(getDocuments()[0]!.status).toBe("ready");
    });

    it("does not notify when the status is unchanged", () => {
      // Every emit re-renders the thread; a no-op write should cost nothing.
      addDocument(uploaded());
      setDocumentStatus("doc-1", "ready");
      const listener = vi.fn();
      subscribe(listener);
      setDocumentStatus("doc-1", "ready");

      expect(listener).not.toHaveBeenCalled();
    });

    it("changes the snapshot reference when a status changes", () => {
      // A mutation in place would leave useSyncExternalStore convinced nothing
      // had happened, so the notification above would render stale data.
      addDocument(uploaded());
      const before = getDocuments();
      setDocumentStatus("doc-1", "ready");
      expect(getDocuments()).not.toBe(before);
    });

    it("stops notifying after unsubscribe", () => {
      const listener = vi.fn();
      subscribe(listener)();
      addDocument(uploaded());
      expect(listener).not.toHaveBeenCalled();
    });
  });

  describe("clearing", () => {
    it("drops every document", () => {
      addDocument(uploaded());
      clearDocuments();
      expect(getDocuments()).toEqual([]);
    });

    it("notifies so the chips disappear with the conversation", () => {
      addDocument(uploaded());
      const listener = vi.fn();
      subscribe(listener);
      clearDocuments();
      expect(listener).toHaveBeenCalledTimes(1);
    });

    it("does not notify when already empty", () => {
      const listener = vi.fn();
      subscribe(listener);
      clearDocuments();
      expect(listener).not.toHaveBeenCalled();
    });
  });
});

describe("surviving a page reload", () => {
  /**
   * REGRESSION: the store was memory-only. The conversation comes back from
   * Postgres on reload but the attachments did not, so an answer citing
   * "[Section 148]" rendered as a dead link with nothing to resolve it against
   * — and the backend stopped being told a document was attached at all.
   */
  const KEY = "assistant_attached_documents";

  beforeEach(() => {
    clearDocuments();
    window.localStorage.clear();
    document.cookie = "assistant_thread_id=thread-1; Path=/";
  });

  it("writes attached documents to storage", () => {
    addDocument(uploaded());
    expect(window.localStorage.getItem(KEY)).toContain("doc-1");
  });

  it("restores them on the next load", () => {
    addDocument(uploaded());
    clearDocumentsInMemoryOnly();
    hydrateDocuments();
    expect(getDocuments()).toHaveLength(1);
    expect(getDocuments()[0]!.name).toBe("big.txt");
  });

  it("restores the indexing status too", () => {
    addDocument(uploaded());
    setDocumentStatus("doc-1", "ready");
    clearDocumentsInMemoryOnly();
    hydrateDocuments();
    expect(getDocuments()[0]!.status).toBe("ready");
  });

  it("ignores documents saved under a different conversation", () => {
    // Otherwise a new thread would announce a document it never saw.
    addDocument(uploaded());
    clearDocumentsInMemoryOnly();
    document.cookie = "assistant_thread_id=thread-2; Path=/";
    hydrateDocuments();
    expect(getDocuments()).toEqual([]);
  });

  it("does not overwrite documents already in memory", () => {
    // Storage must hold something *different*, or replacing memory with it is
    // indistinguishable from leaving memory alone.
    addDocument(uploaded({ id: "doc-live", name: "live.txt" }));
    window.localStorage.setItem(
      KEY,
      JSON.stringify({
        thread: "thread-1",
        documents: [{ id: "doc-stale", name: "stale.txt", sections: 1, status: "ready" }],
      }),
    );

    hydrateDocuments();

    expect(getDocuments()).toHaveLength(1);
    expect(getDocuments()[0]!.id).toBe("doc-live");
  });

  it("clearing removes them from storage", () => {
    // "New chat" must not leave a document behind for the next conversation.
    addDocument(uploaded());
    clearDocuments();
    expect(window.localStorage.getItem(KEY)).toBeNull();
  });

  it("survives corrupt storage", () => {
    window.localStorage.setItem(KEY, "{not json");
    expect(() => hydrateDocuments()).not.toThrow();
    expect(getDocuments()).toEqual([]);
  });

  it("survives storage holding the wrong shape", () => {
    window.localStorage.setItem(KEY, JSON.stringify({ thread: "thread-1", documents: "nope" }));
    expect(() => hydrateDocuments()).not.toThrow();
    expect(getDocuments()).toEqual([]);
  });

  it("notifies subscribers so the chips reappear", () => {
    addDocument(uploaded());
    clearDocumentsInMemoryOnly();
    const listener = vi.fn();
    subscribe(listener);
    hydrateDocuments();
    expect(listener).toHaveBeenCalled();
  });

  it("does not throw when storage is unavailable", () => {
    // Private browsing modes throw from setItem rather than returning.
    const setItem = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("QuotaExceededError");
    });
    expect(() => addDocument(uploaded())).not.toThrow();
    setItem.mockRestore();
  });
});
