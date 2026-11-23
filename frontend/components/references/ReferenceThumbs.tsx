"use client";

import { useEffect, useState } from "react";
import { createPortal } from "react-dom";

import {
  deleteReference,
  referenceIsGone,
  referenceUrl,
} from "@/lib/references";

type Ref = { id: string; name: string };

/** The photo filling the window, with Delete. Escape, Close or a click outside closes it. */
function EnlargedReference({
  reference,
  onClose,
  onDeleted,
}: {
  reference: Ref;
  onClose: () => void;
  onDeleted: () => void;
}) {
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  async function remove() {
    if (
      !window.confirm(
        `Delete the photo "${reference.name}" permanently? Images already made from it stay.`,
      )
    ) {
      return;
    }
    setDeleting(true);
    setError(null);
    try {
      await deleteReference(reference.id);
      onDeleted();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Delete failed");
      setDeleting(false);
    }
  }

  const button =
    "rounded-md bg-white/90 px-2.5 py-1 text-xs font-medium hover:bg-white disabled:opacity-60 dark:bg-gray-900/90";

  return createPortal(
    <div
      role="dialog"
      aria-modal="true"
      aria-label={`Reference photo ${reference.name}`}
      onClick={onClose}
      className="fixed inset-0 z-50 flex cursor-zoom-out flex-col items-center justify-center gap-3 bg-black/80 p-4"
    >
      <div className="absolute right-4 top-4 flex gap-2" onClick={(event) => event.stopPropagation()}>
        <button
          type="button"
          onClick={remove}
          disabled={deleting}
          className={`${button} text-red-700 dark:text-red-400`}
        >
          {deleting ? "Deleting…" : "Delete photo"}
        </button>
        <button type="button" onClick={onClose} autoFocus className={`${button} text-gray-800 dark:text-gray-200`}>
          Close
        </button>
      </div>
      {/* eslint-disable-next-line @next/next/no-img-element -- a same-origin PNG from the backend, not a static asset */}
      <img
        src={referenceUrl(reference.id)}
        alt={reference.name}
        onClick={(event) => event.stopPropagation()}
        className="max-h-[85vh] max-w-[95vw] cursor-default rounded-md object-contain"
      />
      <p className="max-w-[95vw] truncate text-xs text-white/80">{reference.name}</p>
      {error ? (
        <p role="alert" className="rounded bg-white/90 px-2 py-1 text-xs text-amber-800">
          {error}
        </p>
      ) : null}
    </div>,
    document.body,
  );
}

type ThumbState = "shown" | "deleted" | "unavailable";

function Thumb({ reference, size }: { reference: Ref; size: "sm" | "md" }) {
  const [state, setState] = useState<ThumbState>("shown");
  const [enlarged, setEnlarged] = useState(false);
  const box = size === "sm" ? "h-12 w-12" : "h-20 w-20";

  async function onLoadError() {
    setState((await referenceIsGone(reference.id)) ? "deleted" : "unavailable");
  }

  if (state !== "shown") {
    return (
      <li
        title={reference.name}
        className={`${box} flex shrink-0 items-center justify-center rounded-md border border-dashed border-gray-300 p-1 text-center text-[10px] leading-tight text-gray-500 dark:border-gray-600 dark:text-gray-400`}
      >
        {state === "deleted" ? "Photo deleted" : "Not loaded"}
      </li>
    );
  }

  return (
    <li className="shrink-0">
      <button
        type="button"
        onClick={() => setEnlarged(true)}
        title={`${reference.name} (click to enlarge)`}
        aria-label={`Enlarge reference photo ${reference.name}`}
        className={`${box} block cursor-zoom-in overflow-hidden rounded-md border border-gray-200 bg-gray-50 dark:border-gray-700 dark:bg-gray-900`}
      >
        {/* eslint-disable-next-line @next/next/no-img-element -- a same-origin PNG from the backend, not a static asset */}
        <img
          src={referenceUrl(reference.id)}
          alt={reference.name}
          loading="lazy"
          onError={onLoadError}
          className="h-full w-full object-cover"
        />
      </button>
      {enlarged ? (
        <EnlargedReference
          reference={reference}
          onClose={() => setEnlarged(false)}
          onDeleted={() => {
            setEnlarged(false);
            setState("deleted");
          }}
        />
      ) : null}
    </li>
  );
}

/** A wrapping row of reference thumbnails; click one to enlarge (and delete) it. */
export function ReferenceThumbs({
  refs,
  size = "md",
  label = "Reference photos",
}: {
  refs: Ref[];
  size?: "sm" | "md";
  label?: string;
}) {
  if (refs.length === 0) return null;
  return (
    <ul aria-label={label} className="flex flex-wrap gap-2">
      {refs.map((reference) => (
        <Thumb key={reference.id} reference={reference} size={size} />
      ))}
    </ul>
  );
}
