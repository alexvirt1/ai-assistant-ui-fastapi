import { beforeEach, describe, expect, it, vi } from "vitest";

import type { UploadedDocument } from "./attachments";
import {
  addDocument,
  clearDocuments,
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
