import { act, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import type { UploadedDocument } from "@/lib/attachments";
import {
  addDocument,
  clearDocuments,
  setDocumentStatus,
} from "@/lib/documentStore";

import { DocumentChips } from "./DocumentChips";

const uploaded = (overrides: Partial<UploadedDocument> = {}): UploadedDocument => ({
  id: "doc-1",
  name: "big.txt",
  sections: 87,
  tier: "consider_retrieval",
  message: "87 chunks, about 45 minutes.",
  ...overrides,
});

describe("DocumentChips", () => {
  beforeEach(() => {
    clearDocuments();
  });

  it("renders nothing when no document is attached", () => {
    const { container } = render(<DocumentChips />);
    expect(container).toBeEmptyDOMElement();
  });

  it("names the document and its section count", () => {
    // REGRESSION: attaching a 5 MB file produced no visible acknowledgement at
    // all - the text is never shown and the reference goes only to the model.
    addDocument(uploaded());
    render(<DocumentChips />);

    expect(screen.getByText("big.txt")).toBeInTheDocument();
    expect(screen.getByRole("listitem")).toHaveTextContent("87 sections");
  });

  it("says preparing while indexing runs", () => {
    addDocument(uploaded());
    render(<DocumentChips />);
    expect(screen.getByRole("listitem")).toHaveTextContent("preparing");
  });

  it("switches to ready when indexing finishes", () => {
    addDocument(uploaded());
    render(<DocumentChips />);

    act(() => setDocumentStatus("doc-1", "ready"));

    expect(screen.getByRole("listitem")).toHaveTextContent("ready");
    expect(screen.getByRole("listitem")).not.toHaveTextContent("preparing");
  });

  it("reports a failed index as a delay, not an error", () => {
    // search_document indexes on demand, so a failed background index costs a
    // slower first question rather than a lost document.
    addDocument(uploaded());
    render(<DocumentChips />);

    act(() => setDocumentStatus("doc-1", "failed"));

    expect(screen.getByRole("listitem")).toHaveTextContent("first question");
  });

  it("singularises a one-section document", () => {
    addDocument(uploaded({ sections: 1 }));
    render(<DocumentChips />);
    expect(screen.getByRole("listitem")).toHaveTextContent("1 section");
    expect(screen.getByRole("listitem")).not.toHaveTextContent("1 sections");
  });

  it("keeps the summarising estimate in the tooltip, not the chip", () => {
    // The scope message is the cost of summarising the document. Searching it
    // is far cheaper, so showing "about 45 minutes" inline would tell the user
    // their next question takes 45 minutes, which is false.
    addDocument(uploaded());
    render(<DocumentChips />);

    const chip = screen.getByRole("listitem");
    expect(chip).toHaveAttribute("title", expect.stringContaining("45 minutes"));
    expect(chip).not.toHaveTextContent("45 minutes");
  });

  it("shows one chip per attached document", () => {
    addDocument(uploaded());
    addDocument(uploaded({ id: "doc-2", name: "other.txt" }));
    render(<DocumentChips />);

    expect(screen.getAllByRole("listitem")).toHaveLength(2);
  });

  it("appears when a document is attached after mount", () => {
    render(<DocumentChips />);
    act(() => addDocument(uploaded()));
    expect(screen.getByText("big.txt")).toBeInTheDocument();
  });

  it("disappears when the conversation is cleared", () => {
    // New chat drops the documents; a chip left behind would claim the model
    // can still search a document it is no longer told about.
    addDocument(uploaded());
    const { container } = render(<DocumentChips />);

    act(() => clearDocuments());

    expect(container).toBeEmptyDOMElement();
  });
});
