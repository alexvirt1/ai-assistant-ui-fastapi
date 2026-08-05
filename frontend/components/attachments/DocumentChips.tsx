"use client";

import { useSyncExternalStore } from "react";

import {
  getDocuments,
  subscribe,
  type AttachedDocument,
} from "@/lib/documentStore";

/**
 * The documents this conversation can search.
 *
 * A large attachment leaves no trace in the UI: its text is never shown, the
 * message carries only a reference the model sees, and embedding runs for about
 * a minute in the background. Tested against a 5 MB file, the only visible sign
 * anything had happened was the search_document tool firing on the next
 * question. These chips are the missing acknowledgement - the document is here,
 * it has this many sections, and it is or is not ready yet.
 *
 * Reads the same store the runtime sends to the backend, so the chips cannot
 * disagree with what the model was told is attached.
 */
export function DocumentChips() {
  const documents = useSyncExternalStore(subscribe, getDocuments, getDocuments);
  if (documents.length === 0) return null;

  return (
    <ul className="flex min-w-0 flex-wrap items-center gap-2" aria-label="Attached documents">
      {documents.map((document) => (
        <DocumentChip key={document.id} document={document} />
      ))}
    </ul>
  );
}

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
      className="flex max-w-[16rem] items-center gap-1.5 rounded-full border border-gray-200 bg-gray-50 px-2.5 py-1 text-xs text-gray-700 dark:border-gray-700 dark:bg-gray-800/60 dark:text-gray-200"
    >
      <span
        aria-hidden="true"
        className={`size-1.5 shrink-0 rounded-full ${DOT_CLASS[status]}`}
      />
      {/* truncate needs a min-w-0 flex child, otherwise a long filename widens
          the chip past its max-width instead of ellipsing. */}
      <span className="min-w-0 truncate font-medium">{name}</span>
      <span className="shrink-0 text-gray-500 dark:text-gray-400">
        · {sections} {sections === 1 ? "section" : "sections"} ·{" "}
        {STATUS_LABEL[status]}
      </span>
    </li>
  );
}
