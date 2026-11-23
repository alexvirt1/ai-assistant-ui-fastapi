import { afterEach, describe, expect, it, vi } from "vitest";

import { clearAttachmentNotice, getAttachmentNotice } from "@/lib/attachmentNotice";

import { ChatAttachmentAdapter } from "./ChatAttachmentAdapter";
import { ImageReferenceAdapter } from "./ImageReferenceAdapter";
import { TextAttachmentAdapter } from "./TextAttachmentAdapter";

afterEach(() => {
  vi.unstubAllGlobals();
  clearAttachmentNotice();
});

const adapter = () =>
  new ChatAttachmentAdapter([new TextAttachmentAdapter(), new ImageReferenceAdapter(() => "t1")]);

describe("ChatAttachmentAdapter", () => {
  it("takes photos from a phone's picker as photos, not documents", async () => {
    // Android names: digits, the real extension and type.
    const webp = new File([new Uint8Array(8)], "1000040484.webp", { type: "image/webp" });
    const jpg = new File([new Uint8Array(8)], "1000040485.jpg", { type: "image/jpeg" });
    expect((await adapter().add({ file: webp })).type).toBe("image");
    expect((await adapter().add({ file: jpg })).type).toBe("image");
    expect(getAttachmentNotice()).toBeNull();
  });

  it("refuses other files and says why", async () => {
    const pdf = new File(["%PDF"], "scan.pdf", { type: "application/pdf" });
    await expect(adapter().add({ file: pdf })).rejects.toThrow(/can't be attached/);
    expect(getAttachmentNotice()).toMatch(/"scan.pdf" can't be attached.*PNG, JPEG, GIF or WebP/);
  });

  it("passes an adapter's own reason through", async () => {
    const huge = new File([new Uint8Array(10)], "big.png", { type: "image/png" });
    Object.defineProperty(huge, "size", { value: 30_000_000 });
    await expect(adapter().add({ file: huge })).rejects.toThrow(/limited to 20 MB/);
    expect(getAttachmentNotice()).toMatch(/limited to 20 MB/);
  });

  it("reports a failed upload", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({ ok: false, status: 415, json: async () => ({ detail: "Not an image." }) }) as Response),
    );
    const a = adapter();
    const pending = await a.add({ file: new File(["x"], "fake.png", { type: "image/png" }) });
    await expect(a.send(pending)).rejects.toThrow(/Not an image/);
    expect(getAttachmentNotice()).toMatch(/Couldn't upload "fake.png"/);
  });
});
