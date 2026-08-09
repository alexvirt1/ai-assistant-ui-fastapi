"use client";

import { useSyncExternalStore } from "react";

import {
  getDocuments,
  getPendingUploads,
  subscribe,
  type AttachedDocument,
  type PendingUpload,
} from "@/lib/documentStore";

/**
 * The documents this conversation can search, and the ones on their way.
 *
 * A large attachment leaves no trace in the UI: its text is never shown, the
 * message carries only a reference the model sees, and embedding runs for about
 * a minute in the background. Tested against a 5 MB file, the only visible sign
 * anything had happened was the search_document tool firing on the next
 * question. These chips are the missing acknowledgement - the document is here,
 * it has this many sections, and it is or is not ready yet.
 *
 * Reads the same store the runtime sends to the backend, so the chips cannot
 * disagree with what the model was told is attached. Uploads in flight come
 * from a separate slice of that store for exactly that reason: they are shown,
 * but they are not yet claimed to the model.
 */
export function DocumentChips() {
  const documents = useSyncExternalStore(subscribe, getDocuments, getDocuments);
  const uploads = useSyncExternalStore(
    subscribe,
    getPendingUploads,
    getPendingUploads,
  );
  if (documents.length === 0 && uploads.length === 0) return null;

  return (
    <ul
      // Polite rather than silent: the upload chip exists to report progress,
      // and a purely visual indicator reports it to only some users.
      aria-live="polite"
      className="flex min-w-0 flex-wrap items-center gap-2"
      aria-label="Attached documents"
    >
      {uploads.map((upload) => (
        <UploadChip key={upload.id} upload={upload} />
      ))}
      {documents.map((document) => (
        <DocumentChip key={document.id} document={document} />
      ))}
    </ul>
  );
}

const CHIP_CLASS =
  "flex max-w-[16rem] items-center gap-1.5 rounded-full border border-gray-200 bg-gray-50 px-2.5 py-1 text-xs text-gray-700 dark:border-gray-700 dark:bg-gray-800/60 dark:text-gray-200";

// truncate needs a min-w-0 flex child, otherwise a long filename widens the
// chip past its max-width instead of ellipsing.
const NAME_CLASS = "min-w-0 truncate font-medium";

const DETAIL_CLASS = "shrink-0 text-gray-500 dark:text-gray-400";

const STATUS_LABEL = {
  indexing: "preparing…",
  ready: "ready",
  failed: "prepares on first question",
} as const;

const DOT_CLASS = {
  indexing: "animate-pulse bg-amber-500",
  ready: "bg-emerald-500",
  failed: "bg-gray-400",
} as const;

/**
 * A file being uploaded right now.
 *
 * No section count yet - that number comes back with the upload response, and
 * the whole point of this chip is the window before there is a response.
 */
function UploadChip({ upload }: { upload: PendingUpload }) {
  return (
    <li className={CHIP_CLASS} title={`Uploading ${upload.name}…`}>
      <span
        aria-hidden="true"
        className="size-1.5 shrink-0 animate-pulse rounded-full bg-sky-500"
      />
      <span className={NAME_CLASS}>{upload.name}</span>
      <span className={DETAIL_CLASS}>· uploading…</span>
    </li>
  );
}

function DocumentChip({ document }: { document: AttachedDocument }) {
  const { name, sections, status, message } = document;

  return (
    <li
      // The scope message quotes the cost of *summarising* the document, which
      // is a different and much slower operation than searching it. Hiding it
      // in the tooltip keeps that distinction from reading as "your question
      // will take 45 minutes".
      title={
        status === "failed"
          ? `Background indexing failed; the first question will index it instead.${message ? ` ${message}` : ""}`
          : message || undefined
      }
      className={CHIP_CLASS}
    >
      <span
        aria-hidden="true"
        className={`size-1.5 shrink-0 rounded-full ${DOT_CLASS[status]}`}
      />
      <span className={NAME_CLASS}>{name}</span>
      <span className={DETAIL_CLASS}>
        · {sections} {sections === 1 ? "section" : "sections"} ·{" "}
        {STATUS_LABEL[status]}
      </span>
    </li>
  );
}
