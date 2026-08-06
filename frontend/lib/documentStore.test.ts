import { beforeEach, describe, expect, it, vi } from "vitest";

import type { UploadedDocument } from "./attachments";
import {
  addDocument,
  type AttachedDocument,
  clearDocuments,
  finishUpload,
  getDocuments,
  getPendingUploads,
  setDocumentStatus,
  setDocuments,
  startUpload,
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

  describe("uploads in flight", () => {
    it("starts with nothing uploading", () => {
      expect(getPendingUploads()).toEqual([]);
    });

    it("records a file while it uploads", () => {
      startUpload("att-1", "big.txt");
      expect(getPendingUploads()).toEqual([{ id: "att-1", name: "big.txt" }]);
    });

    it("keeps an upload out of the list sent to the backend", () => {
      // The whole reason uploads are a separate slice: getDocuments() tells the
      // model what it may search, and a file still in flight has no document id
      // to search by.
      startUpload("att-1", "big.txt");
      expect(getDocuments()).toEqual([]);
    });

    it("notifies so the chip appears while the upload runs", () => {
      const listener = vi.fn();
      subscribe(listener);
      startUpload("att-1", "big.txt");
      expect(listener).toHaveBeenCalledTimes(1);
    });

    it("drops the upload once it settles", () => {
      startUpload("att-1", "big.txt");
      finishUpload("att-1");
      expect(getPendingUploads()).toEqual([]);
    });

    it("notifies so the chip disappears when the upload settles", () => {
      startUpload("att-1", "big.txt");
      const listener = vi.fn();
      subscribe(listener);
      finishUpload("att-1");
      expect(listener).toHaveBeenCalledTimes(1);
    });

    it("tracks concurrent uploads separately", () => {
      startUpload("att-1", "big.txt");
      startUpload("att-2", "other.txt");
      finishUpload("att-1");
      expect(getPendingUploads()).toEqual([{ id: "att-2", name: "other.txt" }]);
    });

    it("ignores a duplicate start", () => {
      startUpload("att-1", "big.txt");
      startUpload("att-1", "big.txt");
      expect(getPendingUploads()).toHaveLength(1);
    });

    it("stays quiet when finishing an upload it never had", () => {
      // send() clears in a `finally`, which also runs on the inline path where
      // no upload was ever started.
      const listener = vi.fn();
      subscribe(listener);
      finishUpload("att-1");
      expect(listener).not.toHaveBeenCalled();
    });

    it("returns a stable reference between changes", () => {
      const before = getPendingUploads();
      expect(getPendingUploads()).toBe(before);
    });
  });

  describe("clearing", () => {
    it("drops every document", () => {
      addDocument(uploaded());
      clearDocuments();
      expect(getDocuments()).toEqual([]);
    });

    it("drops uploads in flight too", () => {
      startUpload("att-1", "big.txt");
      clearDocuments();
      expect(getPendingUploads()).toEqual([]);
    });

    it("notifies when only an upload was in flight", () => {
      startUpload("att-1", "big.txt");
      const listener = vi.fn();
      subscribe(listener);
      clearDocuments();
      expect(listener).toHaveBeenCalledTimes(1);
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

describe("replacing the list on a chat switch", () => {
  const attached = (overrides: Partial<AttachedDocument> = {}): AttachedDocument => ({
    id: "doc-9",
    name: "restored.txt",
    sections: 12,
    status: "ready",
    tier: "consider_retrieval",
    message: "12 chunks",
    ...overrides,
  });

  beforeEach(() => {
    clearDocuments();
  });

  it("shows what the backend says this conversation has attached", () => {
    setDocuments([attached()]);
    expect(getDocuments()).toEqual([attached()]);
  });

  it("drops the previous conversation's documents", () => {
    // REGRESSION GUARD: left in place, they would be announced in the next
    // chat's system prompt and the model told it can search a document that
    // conversation never saw.
    addDocument(uploaded());
    setDocuments([]);
    expect(getDocuments()).toEqual([]);
  });

  it("notifies so the chips repaint", () => {
    const listener = vi.fn();
    subscribe(listener);
    setDocuments([attached()]);
    expect(listener).toHaveBeenCalled();
  });

  it("stays quiet when empty replaces empty", () => {
    // The common case - switching between two chats with no attachments -
    // must not re-render the whole thread.
    const listener = vi.fn();
    subscribe(listener);
    setDocuments([]);
    expect(listener).not.toHaveBeenCalled();
  });
});
