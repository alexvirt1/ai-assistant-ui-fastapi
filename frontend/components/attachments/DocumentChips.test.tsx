import { act, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import type { UploadedDocument } from "@/lib/attachments";
import {
  addDocument,
  clearDocuments,
  finishUpload,
  setDocumentStatus,
  startUpload,
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

  it("shows the file while it is still uploading", () => {
    // REGRESSION: a large attachment showed nothing at all between pressing
    // send and the upload answering - the composer holds the message until
    // every attachment has been sent, so there was no chip, no message and no
    // running indicator for the seconds the backend spent chunking the file.
    startUpload("att-1", "big.txt");
    render(<DocumentChips />);

    const chip = screen.getByRole("listitem");
    expect(chip).toHaveTextContent("big.txt");
    expect(chip).toHaveTextContent("uploading");
  });

  it("appears when an upload starts after mount", () => {
    render(<DocumentChips />);
    act(() => startUpload("att-1", "big.txt"));
    expect(screen.getByText("big.txt")).toBeInTheDocument();
  });

  it("claims no section count before the upload answers", () => {
    // Sections come back with the upload response; printing "0 sections" would
    // describe the document wrongly for as long as the upload runs.
    startUpload("att-1", "big.txt");
    render(<DocumentChips />);
    expect(screen.getByRole("listitem")).not.toHaveTextContent("section");
  });

  it("replaces the upload chip with the document chip", () => {
    startUpload("att-1", "big.txt");
    render(<DocumentChips />);

    act(() => {
      addDocument(uploaded());
      finishUpload("att-1");
    });

    const chips = screen.getAllByRole("listitem");
    expect(chips).toHaveLength(1);
    expect(chips[0]).toHaveTextContent("87 sections");
    expect(chips[0]).not.toHaveTextContent("uploading");
  });

  it("takes the chip away when an upload fails", () => {
    // The fallback path sends a truncated inline copy instead; leaving a chip
    // up would promise a searchable document that does not exist.
    startUpload("att-1", "big.txt");
    const { container } = render(<DocumentChips />);

    act(() => finishUpload("att-1"));

    expect(container).toBeEmptyDOMElement();
  });

  it("shows an upload alongside a document already attached", () => {
    addDocument(uploaded());
    startUpload("att-2", "second.txt");
    render(<DocumentChips />);

    expect(screen.getAllByRole("listitem")).toHaveLength(2);
  });

  it("announces changes to assistive technology", () => {
    startUpload("att-1", "big.txt");
    render(<DocumentChips />);
    expect(screen.getByRole("list")).toHaveAttribute("aria-live", "polite");
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
