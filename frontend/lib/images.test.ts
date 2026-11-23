import { afterEach, describe, expect, it, vi } from "vitest";

import {
  deleteImage,
  fetchImage,
  imageFilename,
  ImageGoneError,
  parseImageResult,
} from "./images";

afterEach(() => vi.unstubAllGlobals());

describe("parseImageResult", () => {
  it("reads the tool's JSON", () => {
    const parsed = parseImageResult(
      JSON.stringify({ image_id: "abc", model: "z-image-turbo", width: 832, height: 1216, seed: 5, prompt: "a fox" }),
    );
    expect(parsed).toEqual({
      kind: "image",
      image: { imageId: "abc", model: "z-image-turbo", width: 832, height: 1216, seed: 5, prompt: "a fox", references: [] },
    });
  });

  it("reads the reference photos an image was made from", () => {
    const parsed = parseImageResult(
      JSON.stringify({
        image_id: "abc",
        prompt: "dog astronaut",
        references: [{ id: "r1", name: "dog.jpg" }, { name: "no id" }, "junk"],
      }),
    );
    expect(parsed.kind === "image" && parsed.image.references).toEqual([{ id: "r1", name: "dog.jpg" }]);
  });

  it("turns an error string into a message", () => {
    expect(parseImageResult("Error: The image server on AI-1 is busy")).toEqual({
      kind: "error",
      message: "The image server on AI-1 is busy",
    });
  });

  it("treats JSON without an image id as an error", () => {
    expect(parseImageResult('{"foo": 1}').kind).toBe("error");
  });
});

describe("imageFilename", () => {
  it("matches the backend's names", () => {
    expect(imageFilename("A Lighthouse at Dusk, watercolour!")).toBe("a-lighthouse-at-dusk-watercolour.png");
    expect(imageFilename("Кот")).toBe("image.png");
  });
});

describe("fetchImage / deleteImage", () => {
  it("reports a deleted image as gone", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: false, status: 410 }) as Response));
    await expect(fetchImage("abc")).rejects.toBeInstanceOf(ImageGoneError);
  });

  it("deletes through the proxy", async () => {
    const fetchMock = vi.fn(async () => ({ ok: true, status: 204 }) as Response);
    vi.stubGlobal("fetch", fetchMock);
    await deleteImage("abc");
    expect(fetchMock).toHaveBeenCalledWith("/api/images/abc", { method: "DELETE" });
  });

  it("raises when a delete fails", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: false, status: 500 }) as Response));
    await expect(deleteImage("abc")).rejects.toThrow("Delete failed (500)");
  });
});
