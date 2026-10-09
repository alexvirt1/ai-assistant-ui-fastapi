"use client";

import { makeMarkdownText } from "@assistant-ui/react-markdown";
import type { ComponentPropsWithoutRef } from "react";
import rehypeKatex from "rehype-katex";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";

import { remarkSections } from "@/lib/remarkSections";

import { SectionCitation } from "./attachments/SectionCitation";

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
  components: { a: SectionCitation, table: MarkdownTable },
});
