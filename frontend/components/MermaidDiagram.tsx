"use client";

import { useContentPartText } from "@assistant-ui/react";
import type { SyntaxHighlighterProps } from "@assistant-ui/react-markdown";
import { useTheme } from "next-themes";
import { useEffect, useId, useState } from "react";
import { createPortal } from "react-dom";

import { drawSvg } from "@/lib/mermaid";

/**
 * Whether the fence around `code` has closed in `text`.
 *
 * While an answer streams, the block grows line by line, and Mermaid would
 * fail on - or worse, draw - every partial diagram along the way.
 */
export function fenceClosed(text: string, code: string): boolean {
  const at = text.lastIndexOf(code.trimEnd());
  if (at === -1) return false;
  return /^\s*```/.test(text.slice(at + code.trimEnd().length));
}

type State =
  | { kind: "waiting" }
  | { kind: "drawn"; svg: string }
  | { kind: "failed"; reason: string };

/** The source, styled like every other code block in the answer. */
function Source({ components: { Pre, Code }, code }: SyntaxHighlighterProps) {
  return (
    // node is required by the types but unused by the default components.
    <Pre node={undefined as never}>
      <Code node={undefined as never}>{code}</Code>
    </Pre>
  );
}

/**
 * The diagram filling the window, over the chat.
 *
 * Closed by Escape, the close button, or a click anywhere outside the
 * diagram. Portalled to <body>: inside the answer it would be clipped by the
 * figure's rounded border and stacked under the composer.
 */
function Enlarged({ svg, onClose }: { svg: string; onClose: () => void }) {
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
      aria-label="Enlarged diagram"
      onClick={onClose}
      className="fixed inset-0 z-50 flex cursor-zoom-out items-center justify-center bg-black/70 p-4"
    >
      <button
        type="button"
        onClick={onClose}
        // Focused on open, so Enter or Space closes too.
        autoFocus
        className="absolute right-4 top-4 rounded-md bg-white/90 px-2.5 py-1 text-xs font-medium text-gray-800 hover:bg-white dark:bg-gray-900/90 dark:text-gray-200 dark:hover:bg-gray-900"
      >
        Close
      </button>
      <div
        role="img"
        aria-label="Diagram"
        // A click on the diagram itself is not a click outside it.
        onClick={(event) => event.stopPropagation()}
        // Mermaid writes max-width onto the <svg> as an inline style, so the
        // overrides need !important to let it grow to the window.
        className="h-[90vh] w-[95vw] cursor-default overflow-auto rounded-lg bg-white p-6 dark:bg-gray-950 [&_svg]:!h-full [&_svg]:!w-full [&_svg]:!max-w-none"
        // The same sanitised Mermaid output the inline diagram shows.
        dangerouslySetInnerHTML={{ __html: svg }}
      />
    </div>,
    document.body,
  );
}

/**
 * A ```mermaid block drawn as a diagram.
 *
 * Registered for the "mermaid" language only (see MarkdownText); every other
 * fence keeps the default code block.
 */
export function MermaidDiagram(props: SyntaxHighlighterProps) {
  const { code } = props;
  const { text, status } = useContentPartText();
  const complete = status.type !== "running" || fenceClosed(text, code);
  const { resolvedTheme } = useTheme();
  const dark = resolvedTheme === "dark";
  // Mermaid uses the id in CSS selectors, so only characters valid there.
  const id = `mermaid-${useId().replace(/[^a-zA-Z0-9]/g, "")}`;
  const [state, setState] = useState<State>({ kind: "waiting" });
  const [showSource, setShowSource] = useState(false);
  const [copied, setCopied] = useState(false);
  const [enlarged, setEnlarged] = useState(false);

  useEffect(() => {
    if (!complete) return;
    let cancelled = false;

    drawSvg(code, id, { dark })
      .then((svg) => {
        if (!cancelled) setState({ kind: "drawn", svg });
      })
      .catch((cause: unknown) => {
        if (!cancelled) {
          setState({
            kind: "failed",
            reason: cause instanceof Error ? cause.message : "Unknown error",
          });
        }
      });

    return () => {
      cancelled = true;
    };
  }, [complete, code, dark, id]);

  async function copy() {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // Clipboard access can be refused (plain http on a LAN address);
      // the source is on screen to select by hand.
    }
  }

  if (!complete) return <Source {...props} />;

  if (state.kind === "failed") {
    return (
      <div className="my-4">
        <p role="alert" className="mb-1 text-xs text-amber-700 dark:text-amber-500">
          Couldn&apos;t draw this diagram: {state.reason}
        </p>
        <Source {...props} />
      </div>
    );
  }

  return (
    <figure className="my-4 overflow-hidden rounded-lg border border-gray-200 dark:border-gray-700">
      <figcaption className="flex items-center justify-between border-b border-gray-200 px-3 py-1 text-xs text-gray-500 dark:border-gray-700 dark:text-gray-400">
        <span>Diagram</span>
        <span className="flex gap-3">
          <button
            type="button"
            onClick={() => setShowSource((shown) => !shown)}
            className="hover:text-gray-800 dark:hover:text-gray-200"
          >
            {showSource ? "Show diagram" : "Show code"}
          </button>
          <button
            type="button"
            onClick={copy}
            className="hover:text-gray-800 dark:hover:text-gray-200"
          >
            {copied ? "Copied" : "Copy"}
          </button>
        </span>
      </figcaption>
      {showSource ? (
        <Source {...props} />
      ) : state.kind === "drawn" ? (
        <button
          type="button"
          onClick={() => setEnlarged(true)}
          title="Click to enlarge"
          aria-label="Enlarge diagram"
          className="flex w-full cursor-zoom-in justify-center overflow-x-auto bg-white p-4 dark:bg-gray-950 [&_svg]:h-auto [&_svg]:max-w-full"
          // Mermaid's own output under securityLevel "strict", which
          // sanitises it with DOMPurify before returning it.
          dangerouslySetInnerHTML={{ __html: state.svg }}
        />
      ) : (
        <p className="p-4 text-center text-xs text-gray-400">Drawing diagram…</p>
      )}
      {enlarged && state.kind === "drawn" ? (
        <Enlarged svg={state.svg} onClose={() => setEnlarged(false)} />
      ) : null}
    </figure>
  );
}

/** The default header (language + copy) is replaced by the diagram's own. */
export function NoCodeHeader() {
  return null;
}
