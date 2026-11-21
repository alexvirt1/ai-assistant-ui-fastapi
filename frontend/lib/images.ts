/**
 * Generated images: what the generate_image tool returns, and the calls the
 * image card makes for it (fetch for download, delete).
 *
 * The tool's result is a JSON string, e.g.
 *   {"image_id": "…", "model": "z-image-turbo", "width": 1024, "height": 1024,
 *    "seed": 123, "prompt": "…"}
 * or plain text starting "Error:" when generation failed.
 */

export type GeneratedImageInfo = {
  imageId: string;
  model: string;
  width: number;
  height: number;
  seed: number;
  prompt: string;
};

export type ParsedImageResult =
  | { kind: "image"; image: GeneratedImageInfo }
  | { kind: "error"; message: string };

export function parseImageResult(result: unknown): ParsedImageResult {
  const text = typeof result === "string" ? result : JSON.stringify(result ?? "");
  try {
    const data = typeof result === "object" && result !== null ? result : JSON.parse(text);
    const record = data as Record<string, unknown>;
    if (typeof record.image_id === "string" && record.image_id) {
      return {
        kind: "image",
        image: {
          imageId: record.image_id,
          model: String(record.model ?? ""),
          width: Number(record.width) || 1024,
          height: Number(record.height) || 1024,
          seed: Number(record.seed) || 0,
          prompt: String(record.prompt ?? ""),
        },
      };
    }
  } catch {
    // Not JSON: an error message from the tool.
  }
  return {
    kind: "error",
    message: text.replace(/^"|"$/g, "").replace(/^Error:\s*/, "") || "Image generation failed",
  };
}

export function imageUrl(imageId: string): string {
  return `/api/images/${encodeURIComponent(imageId)}`;
}

/** The same file name the backend suggests: the prompt's first words, ASCII. */
export function imageFilename(prompt: string): string {
  const slug = prompt
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 60)
    .replace(/-+$/, "");
  return `${slug || "image"}.png`;
}

export class ImageGoneError extends Error {
  constructor() {
    super("Image deleted");
  }
}

/**
 * The PNG as a blob. Fetched rather than linked, so a failure is reported
 * instead of being saved as a file (seen with Export PDF: a plain link saved
 * the backend's 404 JSON as "export.json").
 */
export async function fetchImage(imageId: string): Promise<Blob> {
  const response = await fetch(imageUrl(imageId));
  if (response.status === 410 || response.status === 404) throw new ImageGoneError();
  if (!response.ok) throw new Error(`Download failed (${response.status})`);
  return response.blob();
}

/** 410 or 404: the image is gone, as opposed to failing to load just now. */
export async function imageIsGone(imageId: string): Promise<boolean> {
  try {
    const response = await fetch(imageUrl(imageId), { method: "GET" });
    return response.status === 410 || response.status === 404;
  } catch {
    return false;
  }
}

export async function deleteImage(imageId: string): Promise<void> {
  const response = await fetch(imageUrl(imageId), { method: "DELETE" });
  // 404: already gone, which is what the user wanted.
  if (!response.ok && response.status !== 404) {
    throw new Error(`Delete failed (${response.status})`);
  }
}

/** Hand a blob to the browser's own download. */
export function saveBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  // Revoked on the next tick: revoking synchronously can cancel the download.
  setTimeout(() => URL.revokeObjectURL(url), 0);
}
