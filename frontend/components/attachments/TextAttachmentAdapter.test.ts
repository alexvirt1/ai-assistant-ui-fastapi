import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { MAX_ATTACHMENT_CHARS } from "@/lib/attachments";
import { clearDocuments, getDocuments } from "@/lib/documentStore";

import { TextAttachmentAdapter } from "./TextAttachmentAdapter";

function withInsecureContext<T>(run: () => T): T {
  const real = Object.getOwnPropertyDescriptor(globalThis, "crypto");
  Object.defineProperty(globalThis, "crypto", {
    value: {},
    configurable: true,
    writable: true,
  });
  try {
    return run();
  } finally {
    if (real) Object.defineProperty(globalThis, "crypto", real);
  }
}

const csv = () =>
  new File(["item,qty\nwidget,42\n"], "inventory.csv", { type: "text/csv" });

describe("TextAttachmentAdapter", () => {
  it("accepts extensions as well as MIME types", () => {
    // Browsers report inconsistent types for Markdown and YAML - Chrome gives
    // .md an empty type - so the dialog filter cannot rely on MIME alone.
    const { accept } = new TextAttachmentAdapter();
    expect(accept).toContain(".md");
    expect(accept).toContain("text/*");
  });

  it("adds a file and marks it pending until send", async () => {
    const pending = await new TextAttachmentAdapter().add({ file: csv() });
    expect(pending.name).toBe("inventory.csv");
    expect(pending.status).toEqual({
      type: "requires-action",
      reason: "composer-send",
    });
  });

  it("adds successfully in an insecure context", async () => {
    // REGRESSION: the original implementation called crypto.randomUUID() here,
    // which is undefined outside a secure context. It threw, the attachment was
    // never added, and the message went to the model with no file at all.
    const promise = withInsecureContext(() =>
      new TextAttachmentAdapter().add({ file: csv() }),
    );
    await expect(promise).resolves.toMatchObject({ name: "inventory.csv" });
  });

  it("sends file contents as a TEXT part", async () => {
    // The part type is load-bearing: the backend's convert_to_langchain_messages
    // handles text and image parts and silently drops file parts.
    const adapter = new TextAttachmentAdapter();
    const complete = await adapter.send(await adapter.add({ file: csv() }));

    expect(complete.status).toEqual({ type: "complete" });
    expect(complete.content).toHaveLength(1);
    expect(complete.content[0]!.type).toBe("text");
  });

  it("includes the file contents and name in the sent text", async () => {
    const adapter = new TextAttachmentAdapter();
    const complete = await adapter.send(await adapter.add({ file: csv() }));
    const part = complete.content[0] as { type: "text"; text: string };

    expect(part.text).toContain("widget,42");
    expect(part.text).toContain("<attachment name=inventory.csv>");
  });

  it("caps an oversized file when the document path is unavailable", async () => {
    // Oversized files now route to /api/documents. There is no backend here, so
    // this exercises the fallback: a truncated inline copy plus a note. The
    // bound allows for that note.
    const big = new File(["a".repeat(200_000)], "big.txt", { type: "text/plain" });
    const adapter = new TextAttachmentAdapter();
    const complete = await adapter.send(await adapter.add({ file: big }));
    const part = complete.content[0] as { type: "text"; text: string };

    expect(part.text.length).toBeLessThan(MAX_ATTACHMENT_CHARS + 400);
    expect(part.text).toContain("truncated");
    expect(part.text).toContain("could not be uploaded");
  });

  it("honours a configured character limit on the fallback path", async () => {
    const adapter = new TextAttachmentAdapter({ maxChars: 100 });
    const file = new File(["z".repeat(5000)], "n.txt", { type: "text/plain" });
    const complete = await adapter.send(await adapter.add({ file }));
    const part = complete.content[0] as { type: "text"; text: string };

    expect(part.text).toContain("truncated");
    expect(part.text.length).toBeLessThan(500);
  });

  it("honours a configured byte limit", async () => {
    const adapter = new TextAttachmentAdapter({ maxBytes: 10 });
    const file = new File(["more than ten bytes"], "n.txt", {
      type: "text/plain",
    });
    await expect(adapter.add({ file })).rejects.toThrow(/limited to/i);
  });

  it("allows a larger limit than the default, for experimentation", async () => {
    // The whole point of MAX_ATTACHMENT_CHARS being configurable.
    const adapter = new TextAttachmentAdapter({ maxChars: 200_000 });
    const file = new File(["w".repeat(150_000)], "big.txt", {
      type: "text/plain",
    });
    const complete = await adapter.send(await adapter.add({ file }));
    const part = complete.content[0] as { type: "text"; text: string };

    expect(part.text).not.toContain("truncated");
    expect(part.text.length).toBeGreaterThan(150_000);
  });

  it("refuses a file larger than the hard limit before reading it", async () => {
    const huge = new File(["x".repeat(1_500_000)], "huge.txt", {
      type: "text/plain",
    });
    await expect(
      new TextAttachmentAdapter().add({ file: huge }),
    ).rejects.toThrow(/limited to/i);
  });
});

describe("TextAttachmentAdapter routing", () => {
  const originalFetch = globalThis.fetch;

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  function mockUpload(response: unknown, ok = true) {
    const calls: string[] = [];
    globalThis.fetch = vi.fn(async (url: RequestInfo | URL) => {
      calls.push(String(url));
      return {
        ok,
        status: ok ? 200 : 500,
        json: async () => response,
        text: async () => JSON.stringify(response),
      } as Response;
    }) as typeof fetch;
    return calls;
  }

  const uploadResponse = {
    id: "doc-123",
    name: "big.txt",
    scope: { chunks: 87, tier: "consider_retrieval", message: "87 chunks" },
  };

  it("inlines a file that fits, without uploading", async () => {
    const calls = mockUpload(uploadResponse);
    const adapter = new TextAttachmentAdapter({ maxChars: 1000 });
    const file = new File(["short content"], "n.txt", { type: "text/plain" });

    const complete = await adapter.send(await adapter.add({ file }));
    const part = complete.content[0] as { text: string };

    expect(part.text).toContain("short content");
    expect(calls).toEqual([]);
  });

  it("uploads a file too large to inline and sends a reference", async () => {
    const calls = mockUpload(uploadResponse);
    const adapter = new TextAttachmentAdapter({ maxChars: 100 });
    const file = new File(["x".repeat(5000)], "big.txt", { type: "text/plain" });

    const complete = await adapter.send(await adapter.add({ file }));
    const part = complete.content[0] as { text: string };

    // The model must get a reference, never 5000 characters of text.
    expect(part.text).toContain('<attached-document id="doc-123"');
    expect(part.text).toContain("search_document");
    expect(part.text).not.toContain("xxxxxxxxxx");
    expect(calls.some((c) => c.includes("/api/documents"))).toBe(true);
  });

  it("kicks off indexing without waiting for it", async () => {
    const calls = mockUpload(uploadResponse);
    const adapter = new TextAttachmentAdapter({ maxChars: 100 });
    const file = new File(["y".repeat(5000)], "big.txt", { type: "text/plain" });

    await adapter.send(await adapter.add({ file }));
    // Embedding 5 MB takes ~1 min; send() must not block on it.
    expect(calls.some((c) => c.includes("/index"))).toBe(true);
  });

  it("falls back to truncated inline text when the upload fails", async () => {
    // Losing the attachment entirely would be worse than showing its opening.
    mockUpload({ detail: "backend down" }, false);
    const adapter = new TextAttachmentAdapter({ maxChars: 200 });
    const file = new File(["z".repeat(5000)], "big.txt", { type: "text/plain" });

    const complete = await adapter.send(await adapter.add({ file }));
    const part = complete.content[0] as { text: string };

    expect(part.text).toContain("zzz");
    expect(part.text).toContain("could not be uploaded");
    expect(part.text).toContain("truncated");
  });
});

describe("TextAttachmentAdapter document registration", () => {
  const originalFetch = globalThis.fetch;

  beforeEach(() => {
    clearDocuments();
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  const uploadResponse = {
    id: "doc-123",
    name: "big.txt",
    scope: { chunks: 87, tier: "consider_retrieval", message: "87 chunks" },
  };

  /** Upload succeeds; the index call is held open until the test releases it. */
  function mockDeferredIndex(indexOk = true) {
    let release!: () => void;
    const indexed = new Promise<void>((resolve) => {
      release = resolve;
    });
    globalThis.fetch = vi.fn(async (url: RequestInfo | URL) => {
      if (String(url).includes("/index")) {
        await indexed;
        return { ok: indexOk, status: indexOk ? 200 : 500 } as Response;
      }
      return {
        ok: true,
        status: 200,
        json: async () => uploadResponse,
        text: async () => JSON.stringify(uploadResponse),
      } as Response;
    }) as typeof fetch;
    // Two ticks: one for the awaited fetch, one for the .then that writes status.
    return async () => {
      release();
      await new Promise((resolve) => setTimeout(resolve, 0));
    };
  }

  const bigFile = () =>
    new File(["q".repeat(5000)], "big.txt", { type: "text/plain" });

  async function sendBig() {
    const adapter = new TextAttachmentAdapter({ maxChars: 100 });
    return adapter.send(await adapter.add({ file: bigFile() }));
  }

  it("registers the uploaded document with its scope detail", async () => {
    mockDeferredIndex();
    await sendBig();

    expect(getDocuments()).toMatchObject([
      { id: "doc-123", name: "big.txt", sections: 87, message: "87 chunks" },
    ]);
  });

  it("registers before indexing finishes, so the chip appears immediately", async () => {
    // send() returning only after a ~1 minute embed would freeze the composer.
    const finishIndexing = mockDeferredIndex();
    await sendBig();

    expect(getDocuments()[0]!.status).toBe("indexing");
    await finishIndexing();
  });

  it("marks the document ready once indexing succeeds", async () => {
    const finishIndexing = mockDeferredIndex(true);
    await sendBig();
    await finishIndexing();

    expect(getDocuments()[0]!.status).toBe("ready");
  });

  it("marks the document failed when indexing does not succeed", async () => {
    const finishIndexing = mockDeferredIndex(false);
    await sendBig();
    await finishIndexing();

    expect(getDocuments()[0]!.status).toBe("failed");
  });

  it("marks the document failed when the index request throws", async () => {
    globalThis.fetch = vi.fn(async (url: RequestInfo | URL) => {
      if (String(url).includes("/index")) throw new Error("network down");
      return {
        ok: true,
        status: 200,
        json: async () => uploadResponse,
        text: async () => JSON.stringify(uploadResponse),
      } as Response;
    }) as typeof fetch;

    // A rejection here must not surface as an unhandled promise or lose the
    // attachment: the document is still searchable, just not pre-indexed.
    await expect(sendBig()).resolves.toBeDefined();
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(getDocuments()[0]!.status).toBe("failed");
  });

  it("registers nothing when the upload itself fails", async () => {
    globalThis.fetch = vi.fn(async () => ({
      ok: false,
      status: 500,
      text: async () => "backend down",
    })) as unknown as typeof fetch;

    await sendBig();

    // Announcing a document the backend never stored would make the model
    // call search_document with an id that does not exist.
    expect(getDocuments()).toEqual([]);
  });

  it("registers nothing for a file small enough to inline", async () => {
    globalThis.fetch = vi.fn() as unknown as typeof fetch;
    const adapter = new TextAttachmentAdapter({ maxChars: 1000 });
    const file = new File(["short"], "n.txt", { type: "text/plain" });

    await adapter.send(await adapter.add({ file }));

    expect(getDocuments()).toEqual([]);
  });
});
