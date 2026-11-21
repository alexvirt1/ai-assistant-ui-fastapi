"use client";

import { makeAssistantToolUI } from "@assistant-ui/react";
import { useEffect, useState } from "react";
import { createPortal } from "react-dom";

import {
  deleteImage,
  fetchImage,
  imageFilename,
  imageIsGone,
  imageUrl,
  ImageGoneError,
  parseImageResult,
  saveBlob,
  type GeneratedImageInfo,
} from "@/lib/images";

import { RunningIndicator, WaitingForAnswer } from "./ToolExecutionIndicators";

/** Seconds since the card mounted, while generation runs. */
function useElapsed(active: boolean): number {
  const [seconds, setSeconds] = useState(0);
  useEffect(() => {
    if (!active) return;
    const started = Date.now();
    const timer = setInterval(() => setSeconds(Math.floor((Date.now() - started) / 1000)), 1000);
    return () => clearInterval(timer);
  }, [active]);
  return seconds;
}

function Running({ model }: { model: string }) {
  const seconds = useElapsed(true);
  return (
    <RunningIndicator
      icon="🎨"
      description={`Generating image${model ? ` with ${model}` : ""}… ${seconds} s`}
    />
  );
}

const cardClass =
  "my-3 w-full max-w-lg overflow-hidden rounded-lg border border-gray-200 bg-white dark:border-gray-700 dark:bg-gray-950";

function Notice({ children, tone }: { children: React.ReactNode; tone: "muted" | "warn" }) {
  return (
    <div
      role={tone === "warn" ? "alert" : "status"}
      className={`my-3 w-fit max-w-lg rounded-lg border px-3 py-2 text-sm ${
        tone === "warn"
          ? "border-amber-300 text-amber-800 dark:border-amber-800 dark:text-amber-400"
          : "border-gray-200 text-gray-500 dark:border-gray-700 dark:text-gray-400"
      }`}
    >
      {children}
    </div>
  );
}

/** The image filling the window. Escape, Close or a click outside closes it. */
function Enlarged({ image, onClose }: { image: GeneratedImageInfo; onClose: () => void }) {
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  return createPortal(
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Enlarged image"
      onClick={onClose}
      className="fixed inset-0 z-50 flex cursor-zoom-out items-center justify-center bg-black/80 p-4"
    >
      <button
        type="button"
        onClick={onClose}
        autoFocus
        className="absolute right-4 top-4 rounded-md bg-white/90 px-2.5 py-1 text-xs font-medium text-gray-800 hover:bg-white dark:bg-gray-900/90 dark:text-gray-200"
      >
        Close
      </button>
      {/* eslint-disable-next-line @next/next/no-img-element -- a same-origin PNG from the backend, not a static asset */}
      <img
        src={imageUrl(image.imageId)}
        alt={image.prompt}
        onClick={(event) => event.stopPropagation()}
        className="max-h-[92vh] max-w-[95vw] cursor-default rounded-md object-contain"
      />
    </div>,
    document.body,
  );
}

type CardState = "shown" | "deleting" | "deleted" | "unavailable";

export function ImageCard({ image }: { image: GeneratedImageInfo }) {
  const [state, setState] = useState<CardState>("shown");
  const [enlarged, setEnlarged] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function download() {
    setDownloading(true);
    setError(null);
    try {
      saveBlob(await fetchImage(image.imageId), imageFilename(image.prompt));
    } catch (cause) {
      if (cause instanceof ImageGoneError) setState("deleted");
      else setError(cause instanceof Error ? cause.message : "Download failed");
    } finally {
      setDownloading(false);
    }
  }

  async function remove() {
    if (!window.confirm("Delete this image permanently? This cannot be undone.")) return;
    setState("deleting");
    setError(null);
    try {
      await deleteImage(image.imageId);
      setState("deleted");
    } catch (cause) {
      setState("shown");
      setError(cause instanceof Error ? cause.message : "Delete failed");
    }
  }

  async function onLoadError() {
    setState((await imageIsGone(image.imageId)) ? "deleted" : "unavailable");
  }

  if (state === "deleted") return <Notice tone="muted">🗑️ Image deleted</Notice>;
  if (state === "unavailable") {
    return <Notice tone="warn">The image could not be loaded. Reload the chat to try again.</Notice>;
  }

  const button =
    "rounded px-1.5 py-0.5 hover:bg-gray-100 hover:text-gray-800 disabled:opacity-50 dark:hover:bg-gray-800 dark:hover:text-gray-200";

  return (
    <figure className={cardClass}>
      <button
        type="button"
        onClick={() => setEnlarged(true)}
        title="Click to enlarge"
        aria-label="Enlarge image"
        className="block w-full cursor-zoom-in bg-gray-50 dark:bg-gray-900"
        style={{ aspectRatio: `${image.width} / ${image.height}` }}
      >
        {/* eslint-disable-next-line @next/next/no-img-element -- a same-origin PNG from the backend, not a static asset */}
        <img
          src={imageUrl(image.imageId)}
          alt={image.prompt}
          width={image.width}
          height={image.height}
          loading="lazy"
          onError={onLoadError}
          className="h-full w-full object-contain"
        />
      </button>
      <figcaption className="space-y-1 border-t border-gray-200 px-3 py-2 text-xs text-gray-500 dark:border-gray-700 dark:text-gray-400">
        <p className="line-clamp-3 text-gray-700 dark:text-gray-300" title={image.prompt}>
          {image.prompt}
        </p>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <span>
            {image.model} · {image.width}×{image.height} · seed {image.seed}
          </span>
          <span className="flex gap-1">
            <button type="button" onClick={download} disabled={downloading} className={button}>
              {downloading ? "Downloading…" : "Download"}
            </button>
            <button
              type="button"
              onClick={remove}
              disabled={state === "deleting"}
              className={`${button} text-red-600 hover:text-red-700 dark:text-red-400`}
            >
              {state === "deleting" ? "Deleting…" : "Delete"}
            </button>
          </span>
        </div>
        {error ? (
          <p role="alert" className="text-amber-700 dark:text-amber-500">
            {error}
          </p>
        ) : null}
      </figcaption>
      {enlarged ? <Enlarged image={image} onClose={() => setEnlarged(false)} /> : null}
    </figure>
  );
}

/**
 * The card for a generate_image call: progress while it runs, then the image
 * (or why there is none). Unlike the other tools' indicators it stays once the
 * call is done - the image is the answer - and history replay restores it from
 * the stored call (see backend app/chats/messages.py VISIBLE_TOOLS).
 */
export const GeneratedImageUI = makeAssistantToolUI<
  { prompt?: string; model?: string },
  string
>({
  toolName: "generate_image",
  render: ({ args, result, status, toolCallId }) => {
    if (status.type === "running" || result === undefined) {
      return <Running model={typeof args?.model === "string" ? args.model : ""} />;
    }
    const parsed = parseImageResult(result);
    return (
      <>
        {parsed.kind === "image" ? (
          <ImageCard image={parsed.image} />
        ) : (
          <Notice tone="warn">Couldn&apos;t generate the image: {parsed.message}</Notice>
        )}
        <WaitingForAnswer toolCallId={toolCallId} />
      </>
    );
  },
});
