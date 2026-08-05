"use client";

import { useState } from "react";
import { useSyncExternalStore } from "react";

import { getDocuments, subscribe } from "@/lib/documentStore";
import { sectionFromHref } from "@/lib/remarkSections";

/**
 * A `[Section N]` citation the reader can open.
 *
 * Fetches the passage on demand rather than up front: an answer can cite a
 * dozen sections and most are never opened, and each is a database round trip.
 *
 * The citation carries only a section number, so the document comes from the
 * attachment store. With several documents attached that is genuinely
 * ambiguous — the model does not say which one it cited — so the citation
 * stays plain text rather than opening a passage from the wrong document.
 */
export function SectionCitation({
  href,
  children,
  // Destructured only to keep it out of `rest`: react-markdown passes its mdast
  // node here, and forwarding it to the DOM makes React warn about an unknown
  // attribute.
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  node: _node,
  ...rest
}: React.ComponentPropsWithoutRef<"a"> & { node?: unknown }) {
  const section = href ? sectionFromHref(href) : null;
  const documents = useSyncExternalStore(subscribe, getDocuments, getDocuments);
  const [text, setText] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState(false);

  // Not a citation, or nothing to resolve it against: render as a normal link.
  // The remaining props are forwarded rather than dropped - this replaces the
  // library's default `a`, which carries the aui-md-a class, so swallowing them
  // would silently unstyle every real link in an answer. `node` is react-markdown's
  // mdast node and is not a DOM attribute.
  if (section === null || documents.length !== 1) {
    return href ? (
      <a href={href} {...rest}>
        {children}
      </a>
    ) : (
      <>{children}</>
    );
  }
  const document = documents[0]!;

  const toggle = async () => {
    if (open) {
      setOpen(false);
      return;
    }
    setOpen(true);
    if (text !== null || error !== null) return;

    try {
      const response = await fetch(
        `/api/documents/${document.id}/sections/${section}`,
      );
      if (!response.ok) {
        setError(`Section ${section} could not be loaded (${response.status}).`);
        return;
      }
      const data = await response.json();
      setText(String(data.text ?? ""));
    } catch {
      setError(`Section ${section} could not be loaded.`);
    }
  };

  return (
    <span className="inline-block">
      <button
        type="button"
        onClick={toggle}
        aria-expanded={open}
        title={`Show section ${section} of ${document.name}`}
        className="rounded border border-gray-300 bg-gray-100 px-1 py-0.5 align-baseline text-xs font-medium text-gray-700 transition-colors hover:bg-gray-200 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-200 dark:hover:bg-gray-700"
      >
        {children}
      </button>
      {open && (
        <span className="mt-1 block max-h-64 overflow-y-auto whitespace-pre-wrap rounded-md border border-gray-200 bg-gray-50 p-3 text-sm text-gray-800 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-200">
          {error ?? text ?? "Loading…"}
        </span>
      )}
    </span>
  );
}
