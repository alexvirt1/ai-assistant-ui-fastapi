import { CompositeAttachmentAdapter } from "@assistant-ui/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ImageReferenceAdapter } from "./ImageReferenceAdapter";
import { TextAttachmentAdapter } from "./TextAttachmentAdapter";

afterEach(() => vi.unstubAllGlobals());

const photo = () => new File([new Uint8Array(8)], "dog.jpg", { type: "image/jpeg" });

describe("ImageReferenceAdapter", () => {
  it("holds the photo in the browser until send", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const pending = await new ImageReferenceAdapter(() => "t1").add({ file: photo() });
    expect(pending.type).toBe("image");
    expect(pending.status).toEqual({ type: "requires-action", reason: "composer-send" });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("refuses files it can't use", async () => {
    const bmp = new File(["x"], "a.bmp", { type: "image/bmp" });
    await expect(new ImageReferenceAdapter(() => "t1").add({ file: bmp })).rejects.toThrow(/not a PNG/);
  });

  it("uploads on send and gives the model a marker, not the picture", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({ ok: true, json: async () => ({ id: "r1", name: "dog.jpg", width: 3, height: 4 }) }) as Response),
    );
    const adapter = new ImageReferenceAdapter(() => "t1");
    const done = await adapter.send(await adapter.add({ file: photo() }));
    expect(done.status).toEqual({ type: "complete" });
    expect(done.content).toEqual([{ type: "text", text: '\n<reference-image id="r1" name="dog.jpg" />\n' }]);
  });

  it("needs a chat to upload to", async () => {
    const adapter = new ImageReferenceAdapter(() => undefined);
    await expect(adapter.send(await adapter.add({ file: photo() }))).rejects.toThrow(/isn't ready/);
  });

  it("shares the paperclip with text files", async () => {
    const composite = new CompositeAttachmentAdapter([
      new TextAttachmentAdapter(),
      new ImageReferenceAdapter(() => "t1"),
    ]);
    expect(composite.accept).toContain("image/webp");
    expect((await composite.add({ file: photo() })).type).toBe("image");
    const notes = new File(["hi"], "notes.txt", { type: "text/plain" });
    expect((await composite.add({ file: notes })).type).toBe("document");
  });
});
