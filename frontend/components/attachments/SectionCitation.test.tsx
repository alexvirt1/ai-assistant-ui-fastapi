import { act, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { UploadedDocument } from "@/lib/attachments";
import { addDocument, clearDocuments } from "@/lib/documentStore";
import { SECTION_HREF_PREFIX } from "@/lib/remarkSections";

import { SectionCitation } from "./SectionCitation";

const uploaded = (overrides: Partial<UploadedDocument> = {}): UploadedDocument => ({
  id: "doc-1",
  name: "voyna-i-mir.txt",
  sections: 50,
  tier: "consider_retrieval",
  message: "",
  ...overrides,
});

const originalFetch = globalThis.fetch;

function mockSection(text: string, ok = true) {
  const calls: string[] = [];
  globalThis.fetch = vi.fn(async (url: RequestInfo | URL) => {
    calls.push(String(url));
    return {
      ok,
      status: ok ? 200 : 404,
      json: async () => ({ text, section: 148 }),
    } as Response;
  }) as typeof fetch;
  return calls;
}

const citation = (section = 148) => (
  <SectionCitation href={`${SECTION_HREF_PREFIX}${section}`}>
    [Section {section}]
  </SectionCitation>
);

describe("SectionCitation", () => {
  beforeEach(() => {
    clearDocuments();
    addDocument(uploaded());
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("renders the citation as a button", () => {
    mockSection("passage");
    render(citation());
    expect(screen.getByRole("button")).toHaveTextContent("[Section 148]");
  });

  it("does not fetch until opened", () => {
    // An answer can cite a dozen sections; most are never opened.
    const calls = mockSection("passage");
    render(citation());
    expect(calls).toEqual([]);
  });

  it("shows the passage when opened", async () => {
    mockSection("Графиня с красивой старшею дочерью и гостями");
    render(citation());

    await act(async () => screen.getByRole("button").click());

    await waitFor(() =>
      expect(screen.getByText(/Графиня с красивой/)).toBeInTheDocument(),
    );
  });

  it("asks the backend for that document and section", async () => {
    const calls = mockSection("passage");
    render(citation(149));

    await act(async () => screen.getByRole("button").click());

    await waitFor(() =>
      expect(calls[0]).toBe("/api/documents/doc-1/sections/149"),
    );
  });

  it("closes again when clicked twice", async () => {
    mockSection("the passage text");
    render(citation());
    const button = screen.getByRole("button");

    await act(async () => button.click());
    await waitFor(() => expect(screen.getByText("the passage text")).toBeInTheDocument());
    await act(async () => button.click());

    expect(screen.queryByText("the passage text")).not.toBeInTheDocument();
  });

  it("fetches only once across repeated opens", async () => {
    const calls = mockSection("passage");
    render(citation());
    const button = screen.getByRole("button");

    await act(async () => button.click());
    await waitFor(() => expect(calls).toHaveLength(1));
    await act(async () => button.click());
    await act(async () => button.click());

    expect(calls).toHaveLength(1);
  });

  it("reports a section the backend cannot find", async () => {
    // Silently showing nothing would look like an empty passage.
    mockSection("", false);
    render(citation());

    await act(async () => screen.getByRole("button").click());

    await waitFor(() =>
      expect(screen.getByText(/could not be loaded/)).toBeInTheDocument(),
    );
  });

  it("survives a network failure", async () => {
    globalThis.fetch = vi.fn(async () => {
      throw new Error("offline");
    }) as unknown as typeof fetch;
    render(citation());

    await act(async () => screen.getByRole("button").click());

    await waitFor(() =>
      expect(screen.getByText(/could not be loaded/)).toBeInTheDocument(),
    );
  });

  it("stays plain text when no document is attached", () => {
    // Nothing to resolve the section against.
    clearDocuments();
    render(citation());
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("stays plain text when several documents are attached", () => {
    // The model does not say which document it cited, so opening one would be
    // a guess - and a passage from the wrong document is worse than none.
    addDocument(uploaded({ id: "doc-2", name: "other.txt" }));
    render(citation());
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("renders an ordinary link unchanged", () => {
    render(<SectionCitation href="https://example.com">example</SectionCitation>);
    expect(screen.getByRole("link")).toHaveAttribute("href", "https://example.com");
  });

  it("keeps the styling the default link component applies", () => {
    // This replaces the library's own `a`, which carries aui-md-a. Dropping the
    // props it is given would silently unstyle every real link in an answer.
    render(
      <SectionCitation href="https://example.com" className="aui-md-a">
        example
      </SectionCitation>,
    );
    expect(screen.getByRole("link")).toHaveClass("aui-md-a");
  });

  it("does not leak react-markdown's mdast node onto the DOM", () => {
    // `node` is not a DOM attribute; forwarding it makes React warn.
    const warn = vi.spyOn(console, "error").mockImplementation(() => {});
    render(
      <SectionCitation href="https://example.com" node={{ type: "link" }}>
        example
      </SectionCitation>,
    );
    expect(screen.getByRole("link")).not.toHaveAttribute("node");
    expect(warn).not.toHaveBeenCalled();
  });
});
