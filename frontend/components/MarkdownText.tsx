"use client";

import { makeMarkdownText } from "@assistant-ui/react-markdown";
import type { ComponentPropsWithoutRef } from "react";
import rehypeKatex from "rehype-katex";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";

import { remarkSections } from "@/lib/remarkSections";

import { SectionCitation } from "./attachments/SectionCitation";
import { MermaidDiagram, NoCodeHeader } from "./MermaidDiagram";

/**
 * A table that scrolls sideways instead of widening the message.
 *
 * Replaces the package's default `table` entirely rather than wrapping it, so
 * the `aui-md-table` class its stylesheet keys on is applied here. Without the
 * wrapper a table with many columns pushes past the thread's width and the
 * whole conversation gains a horizontal scrollbar.
 */
export function MarkdownTable({
  // Kept out of `props`, as in SectionCitation: react-markdown passes its mdast
  // node, which is not a DOM attribute.
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  node: _node,
  className,
  ...props
}: ComponentPropsWithoutRef<"table"> & { node?: unknown }) {
  return (
    <div className="overflow-x-auto">
      <table
        className={["aui-md-table", className].filter(Boolean).join(" ")}
        {...props}
      />
    </div>
  );
}

/**
 * A Markdown image in an answer, shown as a note instead of being loaded.
 *
 * The model's text is not trusted to make the browser fetch things. Seen live:
 * qwen3:8b answered a request for images with ![...](https://image.pollinations
 * .ai/prompt/<the user's prompt>) - a public text-to-image site - so loading
 * it sent the prompt to a third party and showed that site's "at its limit"
 * page. Generated images reach the chat through the image card instead
 * (components/tools/GeneratedImage.tsx), never through Markdown. The PDF export
 * already prints Markdown images as their alt text, for the same reason.
 */
export function BlockedImage({
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  node: _node,
  src,
  alt,
}: ComponentPropsWithoutRef<"img"> & { node?: unknown }) {
  let host = "";
  try {
    host = new URL(typeof src === "string" ? src : "", window.location.href).host;
  } catch {
    // Not a URL; the note just leaves the site out.
  }
  return (
    <span
      title="Images written into an answer are not loaded"
      className="inline-flex items-center gap-1 rounded border border-gray-200 px-1.5 py-0.5 text-xs text-gray-500 dark:border-gray-700 dark:text-gray-400"
    >
      <span aria-hidden>🖼️</span>
      <span>
        Image not loaded{alt ? `: ${alt}` : ""}
        {host ? ` (${host})` : ""}
      </span>
    </span>
  );
}

// remarkGfm parses GitHub-flavoured markdown - tables, task lists,
// strikethrough, bare URLs. Models answer comparisons as pipe tables, which
// plain CommonMark leaves as lines of literal "|" characters.
//
// singleTilde is off because models write "~5 min" for "about five minutes";
// with it on, two such approximations in a sentence strike out the text
// between them. "~~struck~~" still works.
//
// remarkMath parses $...$ (inline) and $$...$$ (display) math; rehypeKatex
// renders it. KaTeX emits markup only, so katex.min.css is imported once in
// app/layout.tsx — without it the math renders unstyled.
export const MarkdownText = makeMarkdownText({
  // remarkSections rewrites "[Section 148]" into a link with a section: URL,
  // which the `a` override below renders as an openable citation.
  remarkPlugins: [[remarkGfm, { singleTilde: false }], remarkMath, remarkSections],
  rehypePlugins: [rehypeKatex],
  components: { a: SectionCitation, table: MarkdownTable, img: BlockedImage },
  // ```mermaid fences are drawn as diagrams; every other language keeps the
  // default code block.
  componentsByLanguage: {
    mermaid: { SyntaxHighlighter: MermaidDiagram, CodeHeader: NoCodeHeader },
  },
});
