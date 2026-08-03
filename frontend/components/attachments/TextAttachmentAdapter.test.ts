import { describe, expect, it } from "vitest";

import { MAX_ATTACHMENT_CHARS } from "@/lib/attachments";

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

  it("caps an oversized file's contents", async () => {
    const big = new File(["a".repeat(200_000)], "big.txt", { type: "text/plain" });
    const adapter = new TextAttachmentAdapter();
    const complete = await adapter.send(await adapter.add({ file: big }));
    const part = complete.content[0] as { type: "text"; text: string };

    expect(part.text.length).toBeLessThan(MAX_ATTACHMENT_CHARS + 200);
    expect(part.text).toContain("truncated");
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
