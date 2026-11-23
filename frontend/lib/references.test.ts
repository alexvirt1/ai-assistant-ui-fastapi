import { afterEach, describe, expect, it, vi } from "vitest";

import {
  REFERENCE_ACCEPT,
  deleteReference,
  formatReferenceMarker,
  referenceIsGone,
  referenceProblem,
  splitReferenceMarkers,
  uploadReference,
} from "./references";

afterEach(() => vi.unstubAllGlobals());

const photo = (name = "dog.jpg", type = "image/jpeg", size = 10) =>
  new File([new Uint8Array(size)], name, { type });

describe("referenceProblem", () => {
  it("accepts the four formats, by type or extension", () => {
    expect(referenceProblem(photo())).toBeNull();
    expect(referenceProblem(photo("a.webp", "image/webp"))).toBeNull();
    expect(referenceProblem(photo("a.gif", ""))).toBeNull();
    expect(REFERENCE_ACCEPT).toContain("image/png");
  });

  it("refuses other files and oversize photos", () => {
    expect(referenceProblem(photo("a.bmp", "image/bmp"))).toMatch(/not a PNG, JPEG, GIF or WebP/);
    expect(referenceProblem(photo("big.png", "image/png", 20_000_001))).toMatch(/limited to 20 MB/);
  });
});

describe("markers", () => {
  it("round-trip through the message text", () => {
    const text = "Make this dog an astronaut" + formatReferenceMarker({ id: "r1", name: 'my "dog".jpg' });
    expect(splitReferenceMarkers(text)).toEqual([
      { kind: "text", text: "Make this dog an astronaut" },
      { kind: "references", refs: [{ id: "r1", name: "my 'dog'.jpg" }] },
    ]);
  });

  it("group adjacent photos into one row", () => {
    const text =
      "Both" + formatReferenceMarker({ id: "a", name: "a.png" }) + formatReferenceMarker({ id: "b", name: "b.png" });
    const segments = splitReferenceMarkers(text);
    expect(segments).toHaveLength(2);
    expect(segments[1]).toEqual({
      kind: "references",
      refs: [
        { id: "a", name: "a.png" },
        { id: "b", name: "b.png" },
      ],
    });
  });

  it("leave plain text alone", () => {
    expect(splitReferenceMarkers("hello <b>")).toEqual([{ kind: "text", text: "hello <b>" }]);
  });
});

describe("uploadReference", () => {
  it("posts the file with the chat id", async () => {
    const fetchMock = vi.fn(
      async () =>
        ({ ok: true, json: async () => ({ id: "r1", name: "dog.jpg", width: 300, height: 400 }) }) as Response,
    );
    vi.stubGlobal("fetch", fetchMock);
    const ref = await uploadReference(photo(), "t1");
    expect(ref).toEqual({ id: "r1", name: "dog.jpg", width: 300, height: 400 });
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/api/references");
    const form = init.body as FormData;
    expect(form.get("threadId")).toBe("t1");
    expect((form.get("file") as File).name).toBe("dog.jpg");
  });

  it("reports the backend's reason", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({ ok: false, status: 415, json: async () => ({ detail: "That is not an image." }) }) as Response),
    );
    await expect(uploadReference(photo(), "t1")).rejects.toThrow('Couldn\'t upload "dog.jpg": That is not an image.');
  });
});

describe("delete and gone", () => {
  it("treats 404 as already deleted", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: false, status: 404 }) as Response));
    await expect(deleteReference("r1")).resolves.toBeUndefined();
    expect(await referenceIsGone("r1")).toBe(true);
  });

  it("reports other failures", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: false, status: 500 }) as Response));
    await expect(deleteReference("r1")).rejects.toThrow("Delete failed (500)");
    expect(await referenceIsGone("r1")).toBe(false);
  });
});
