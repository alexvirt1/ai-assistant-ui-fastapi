import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

let thread = { isRunning: false, messages: [] as unknown[] };

// useThread only works inside a runtime provider. Stubbed so these tests are
// about the button, with the thread state driven directly.
vi.mock("@assistant-ui/react", () => ({
  useThread: <T,>(select: (t: typeof thread) => T) => select(thread),
}));

import { ExportPdfButton } from "./ExportPdfButton";

function respond(response: Partial<Response>) {
  const fetchMock = vi.fn(async () => response as Response);
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

/** Records what the button hands to the browser's download, if anything. */
function captureDownloads() {
  const saved: { href: string; download: string }[] = [];
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (
    this: HTMLAnchorElement,
  ) {
    saved.push({ href: this.href, download: this.download });
  });
  return saved;
}

const button = () => screen.getByRole("button");

beforeEach(() => {
  thread = { isRunning: false, messages: [{}] };
  URL.createObjectURL = vi.fn(() => "blob:pdf");
  URL.revokeObjectURL = vi.fn();
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("ExportPdfButton", () => {
  it("saves the PDF under the name the server gives it", async () => {
    const fetchMock = respond({
      ok: true,
      status: 200,
      headers: new Headers({
        "content-disposition":
          "attachment; filename=\"chat.pdf\"; filename*=UTF-8''%D0%97%D0%B0%D0%B4%D0%B0%D1%87%D0%B0.pdf",
      }),
      blob: async () => new Blob(["%PDF-"], { type: "application/pdf" }),
    });
    const saved = captureDownloads();

    render(<ExportPdfButton threadId="t 1/x" />);
    await act(async () => button().click());

    // Encoded: a thread id is opaque and must not be able to change the path.
    expect(fetchMock).toHaveBeenCalledWith("/api/chats/t%201%2Fx/export.pdf");
    expect(saved).toEqual([{ href: "blob:pdf", download: "Задача.pdf" }]);
    expect(button()).toHaveTextContent("Export PDF");
  });

  it("says so when the export fails instead of saving the error", async () => {
    // REGRESSION: a plain download link saved the backend's 404 JSON as
    // "export.json" and showed nothing else.
    respond({ ok: false, status: 404 });
    const saved = captureDownloads();

    render(<ExportPdfButton threadId="t1" />);
    await act(async () => button().click());

    expect(saved).toEqual([]);
    expect(button()).toHaveTextContent("Export failed");
    expect(button()).toHaveAttribute("title", "Export failed (404)");
  });

  it("clears the failure after a few seconds", async () => {
    vi.useFakeTimers();
    try {
      respond({ ok: false, status: 500 });
      render(<ExportPdfButton threadId="t1" />);
      await act(async () => button().click());
      expect(button()).toHaveTextContent("Export failed");

      await act(async () => vi.advanceTimersByTime(5000));
      expect(button()).toHaveTextContent("Export PDF");
    } finally {
      vi.useRealTimers();
    }
  });

  it("is unavailable while an answer is streaming", () => {
    // The PDF comes from the stored transcript, which would not yet include
    // the answer being written.
    thread = { isRunning: true, messages: [{}] };
    render(<ExportPdfButton threadId="t1" />);

    expect(button()).toBeDisabled();
    expect(button()).toHaveAttribute("title", "Wait for the current response to finish");
  });

  it("is unavailable for a chat with nothing in it", () => {
    // A new chat is not registered until its first turn; the export would 404.
    thread = { isRunning: false, messages: [] };
    render(<ExportPdfButton threadId="t1" />);

    expect(button()).toBeDisabled();
  });
});
