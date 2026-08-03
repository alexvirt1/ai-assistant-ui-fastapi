import { afterEach, describe, expect, it } from "vitest";

import {
  MAX_ATTACHMENT_CHARS,
  attachmentId,
  formatAttachment,
  truncateForContext,
} from "./attachments";

/**
 * Replace `globalThis.crypto` wholesale — in both Node and jsdom it is a
 * non-configurable getter, so `delete crypto.randomUUID` silently does nothing
 * and the test would pass against broken code.
 */
function withInsecureContext(run: () => void) {
  const real = Object.getOwnPropertyDescriptor(globalThis, "crypto");
  Object.defineProperty(globalThis, "crypto", {
    value: {},
    configurable: true,
    writable: true,
  });
  try {
    run();
  } finally {
    if (real) Object.defineProperty(globalThis, "crypto", real);
  }
}

describe("truncateForContext", () => {
  it("leaves text within the budget untouched", () => {
    expect(truncateForContext("hello")).toBe("hello");
  });

  it("leaves text exactly at the budget untouched", () => {
    const exact = "x".repeat(MAX_ATTACHMENT_CHARS);
    expect(truncateForContext(exact)).toBe(exact);
  });

  it("keeps exactly the budget when truncating", () => {
    const over = "y".repeat(MAX_ATTACHMENT_CHARS + 5000);
    expect(truncateForContext(over).startsWith("y".repeat(MAX_ATTACHMENT_CHARS))).toBe(
      true,
    );
  });

  it("says how much it dropped, so the model is not misled", () => {
    // Silent truncation is the dangerous failure: the model answers
    // confidently about a document it only partly saw.
    const out = truncateForContext("z".repeat(MAX_ATTACHMENT_CHARS + 5000));
    expect(out).toMatch(/\[\.\.\.truncated: [\d,]+ of [\d,]+ characters omitted\]/);
    expect(out).toContain("5,000");
  });

  it("bounds a large document to roughly the budget", () => {
    const doc = "The quick brown fox. ".repeat(20_000); // ~400 KB
    expect(truncateForContext(doc).length).toBeLessThan(MAX_ATTACHMENT_CHARS + 200);
  });
});

describe("formatAttachment", () => {
  it("wraps content with the filename the model can refer to", () => {
    const out = formatAttachment("notes.md", "line1\nline2");
    expect(out).toContain("<attachment name=notes.md>");
    expect(out.trimEnd().endsWith("</attachment>")).toBe(true);
    expect(out).toContain("line1\nline2");
  });

  it("applies the budget to the wrapped content", () => {
    const out = formatAttachment("big.txt", "q".repeat(MAX_ATTACHMENT_CHARS + 1000));
    expect(out).toContain("truncated");
  });
});

describe("attachmentId", () => {
  it("produces an id when crypto.randomUUID is available", () => {
    expect(attachmentId("a.txt")).toBeTruthy();
  });

  it("still produces an id in an insecure context", () => {
    // REGRESSION: crypto.randomUUID exists only on HTTPS/localhost. This app is
    // served over plain HTTP, so reaching it by LAN address made the original
    // implementation throw inside the adapter, and attachments silently never
    // attached while tsc, eslint and the build all stayed green.
    withInsecureContext(() => {
      expect(() => attachmentId("a.txt")).not.toThrow();
      expect(attachmentId("a.txt")).toContain("a.txt");
    });
  });

  it("produces unique ids in an insecure context", () => {
    withInsecureContext(() => {
      const ids = new Set(
        Array.from({ length: 50 }, () => attachmentId("same-name.txt")),
      );
      expect(ids.size).toBe(50);
    });
  });
});

afterEach(() => {
  // Guard against a failed test leaving the stub installed.
  expect(typeof globalThis.crypto).toBe("object");
});
