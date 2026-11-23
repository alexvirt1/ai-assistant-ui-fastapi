/**
 * Reference photos: pictures attached to a message so a generated image can
 * look like them (FLUX.2 Klein on AI-1; see docs/reference-images-plan.md).
 *
 * The photo is uploaded to the backend when the message is sent, and the
 * message carries a marker in place of the picture:
 *
 *   <reference-image id="…" name="dog.jpg" />
 *
 * The local model can't see images, so the marker is what tells it which
 * photos this message is about; it passes their ids to generate_image. The
 * same marker comes back when the chat is reloaded, and the user's message
 * shows it as a thumbnail (components/references/ReferenceThumbs.tsx). The
 * backend's PDF export reads it too (app/chats/export.py), so keep them in step.
 */

/** What the backend accepts (app/references/normalize.py). */
export const REFERENCE_TYPES = ["image/png", "image/jpeg", "image/gif", "image/webp"];

/** File dialog filter: types and extensions, as browsers differ in what they report. */
export const REFERENCE_ACCEPT = [
  ...REFERENCE_TYPES,
  ".png",
  ".jpg",
  ".jpeg",
  ".gif",
  ".webp",
].join(",");

/** The backend's limit for one upload (MAX_UPLOAD_BYTES). */
export const MAX_REFERENCE_BYTES = 20_000_000;

/** How many references one image can use (the backend's MAX_REFERENCES). */
export const MAX_REFERENCES_PER_IMAGE = 4;

export type UploadedReference = {
  id: string;
  name: string;
  width: number;
  height: number;
};

export function referenceUrl(id: string): string {
  return `/api/references/${encodeURIComponent(id)}`;
}

/** Why a file can't be a reference, or null if it can. Checked before upload. */
export function referenceProblem(file: File): string | null {
  const extension = file.name.toLowerCase().split(".").pop() ?? "";
  const typeOk =
    REFERENCE_TYPES.includes(file.type.toLowerCase()) ||
    ["png", "jpg", "jpeg", "gif", "webp"].includes(extension);
  if (!typeOk) return `"${file.name}" is not a PNG, JPEG, GIF or WebP image.`;
  if (file.size > MAX_REFERENCE_BYTES) {
    return (
      `"${file.name}" is ${(file.size / 1_000_000).toFixed(1)} MB. ` +
      `Photos are limited to ${MAX_REFERENCE_BYTES / 1_000_000} MB.`
    );
  }
  return null;
}

/** Upload a photo to the chat. The backend cleans it (EXIF, size) and keeps a PNG. */
export async function uploadReference(file: File, threadId: string): Promise<UploadedReference> {
  const form = new FormData();
  form.append("file", file);
  form.append("threadId", threadId);
  const response = await fetch("/api/references", { method: "POST", body: form });
  if (!response.ok) {
    let detail = "";
    try {
      const body = await response.json();
      detail = typeof body?.detail === "string" ? body.detail : "";
    } catch {
      // Not JSON: the status says enough.
    }
    throw new Error(`Couldn't upload "${file.name}"${detail ? `: ${detail}` : ` (${response.status})`}`);
  }
  const data = await response.json();
  return {
    id: String(data.id),
    name: String(data.name ?? file.name),
    width: Number(data.width) || 0,
    height: Number(data.height) || 0,
  };
}

export class ReferenceGoneError extends Error {
  constructor() {
    super("Photo deleted");
  }
}

/** Delete a reference for good. A photo already gone counts as deleted. */
export async function deleteReference(id: string): Promise<void> {
  const response = await fetch(referenceUrl(id), { method: "DELETE" });
  if (!response.ok && response.status !== 404) {
    throw new Error(`Delete failed (${response.status})`);
  }
}

/** True if the photo was deleted (410) or is not this user's (404). */
export async function referenceIsGone(id: string): Promise<boolean> {
  try {
    // GET, not HEAD: the backend route answers GET only. The PNG is small.
    const response = await fetch(referenceUrl(id));
    return response.status === 410 || response.status === 404;
  } catch {
    return false;
  }
}

/** The name as it can sit inside the marker's quotes. */
function markerName(name: string): string {
  return name.replace(/["<>\n\r]/g, "'").slice(0, 200);
}

export function formatReferenceMarker(ref: { id: string; name: string }): string {
  return `\n<reference-image id="${ref.id}" name="${markerName(ref.name)}" />\n`;
}

const MARKER = /\s*<reference-image id="([^"]+)" name="([^"]*)" \/>\s*/g;

export type MessageSegment =
  | { kind: "text"; text: string }
  | { kind: "references"; refs: { id: string; name: string }[] };

/**
 * A message's text split into text and runs of reference markers, so the
 * markers can be shown as thumbnails. Adjacent markers form one run (one row).
 */
export function splitReferenceMarkers(text: string): MessageSegment[] {
  const segments: MessageSegment[] = [];
  let last = 0;
  for (const match of text.matchAll(MARKER)) {
    const before = text.slice(last, match.index);
    const ref = { id: match[1]!, name: match[2]! };
    const previous = segments[segments.length - 1];
    if (before.trim() === "" && previous?.kind === "references") {
      previous.refs.push(ref);
    } else {
      if (before.trim() !== "") segments.push({ kind: "text", text: before });
      segments.push({ kind: "references", refs: [ref] });
    }
    last = match.index! + match[0].length;
  }
  const rest = text.slice(last);
  if (rest.trim() !== "" || segments.length === 0) segments.push({ kind: "text", text: rest });
  return segments;
}

/** The references a generate_image result says it used. */
export function resultReferences(record: Record<string, unknown>): { id: string; name: string }[] {
  const raw = record.references;
  if (!Array.isArray(raw)) return [];
  return raw
    .filter((r): r is Record<string, unknown> => typeof r === "object" && r !== null)
    .filter((r) => typeof r.id === "string" && r.id !== "")
    .map((r) => ({ id: r.id as string, name: String(r.name ?? "photo") }));
}
