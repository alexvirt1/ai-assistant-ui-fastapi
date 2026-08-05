import { describe, expect, it } from "vitest";

import { SECTION_PROTOCOL, remarkSections, sectionFromHref } from "./remarkSections";

type Node = { type: string; value?: string; url?: string; children?: Node[] };

const text = (value: string): Node => ({ type: "text", value });
const para = (...children: Node[]): Node => ({ type: "root", children: [{ type: "paragraph", children }] });

function run(tree: Node): Node {
  remarkSections()(tree);
  return tree;
}

const paragraphOf = (tree: Node): Node[] => tree.children![0]!.children!;

describe("sectionFromHref", () => {
  it("reads the section number", () => {
    expect(sectionFromHref(`${SECTION_PROTOCOL}148`)).toBe(148);
  });

  it("ignores ordinary links", () => {
    // Otherwise a real URL in an answer would be hijacked into a citation.
    expect(sectionFromHref("https://example.com")).toBeNull();
  });

  it("rejects a non-numeric section", () => {
    expect(sectionFromHref(`${SECTION_PROTOCOL}abc`)).toBeNull();
  });

  it("rejects section zero, since sections are one-based", () => {
    expect(sectionFromHref(`${SECTION_PROTOCOL}0`)).toBeNull();
  });
});

describe("remarkSections", () => {
  it("turns a citation into a link node", () => {
    const nodes = paragraphOf(run(para(text("See [Section 148] for this."))));
    const link = nodes.find((n) => n.type === "link");
    expect(link?.url).toBe(`${SECTION_PROTOCOL}148`);
  });

  it("keeps the surrounding text intact", () => {
    const nodes = paragraphOf(run(para(text("See [Section 148] for this."))));
    expect(nodes.map((n) => n.value ?? n.children?.[0]?.value).join("")).toBe(
      "See [Section 148] for this.",
    );
  });

  it("keeps the citation text visible in the link", () => {
    const nodes = paragraphOf(run(para(text("[Section 7]"))));
    expect(nodes[0]!.children![0]!.value).toBe("[Section 7]");
  });

  it("handles several citations in one sentence", () => {
    const nodes = paragraphOf(
      run(para(text("Both [Section 148] and [Section 149] say so."))),
    );
    const urls = nodes.filter((n) => n.type === "link").map((n) => n.url);
    expect(urls).toEqual([`${SECTION_PROTOCOL}148`, `${SECTION_PROTOCOL}149`]);
  });

  it("handles the plural form the model sometimes writes", () => {
    const nodes = paragraphOf(run(para(text("[Sections 12]"))));
    expect(nodes[0]!.url).toBe(`${SECTION_PROTOCOL}12`);
  });

  it("leaves text without citations alone", () => {
    const nodes = paragraphOf(run(para(text("No citation here."))));
    expect(nodes).toEqual([text("No citation here.")]);
  });

  it("does not rewrite inside an existing link", () => {
    // Nesting a link inside a link is invalid markup.
    const link: Node = {
      type: "link",
      url: "https://example.com",
      children: [text("[Section 5]")],
    };
    const nodes = paragraphOf(run(para(link)));
    expect(nodes[0]!.children![0]!.value).toBe("[Section 5]");
    expect(nodes[0]!.children![0]!.type).toBe("text");
  });

  it("rewrites citations nested inside emphasis", () => {
    const tree = para({ type: "emphasis", children: [text("see [Section 3]")] });
    run(tree);
    const emphasis = paragraphOf(tree)[0]!;
    expect(emphasis.children!.some((n) => n.url === `${SECTION_PROTOCOL}3`)).toBe(true);
  });

  it("does not loop forever on its own output", () => {
    // The inserted link contains the citation text again; a visitor that walked
    // into what it just inserted would never terminate.
    const tree = para(text("[Section 1] [Section 2] [Section 3]"));
    run(tree);
    expect(paragraphOf(tree).filter((n) => n.type === "link")).toHaveLength(3);
  });

  it("is a no-op on a tree with no children", () => {
    expect(() => run({ type: "root" })).not.toThrow();
  });

  it("does not match a bare section mention", () => {
    // Only the bracketed citation form is a citation.
    const nodes = paragraphOf(run(para(text("section 148 of the document"))));
    expect(nodes.every((n) => n.type === "text")).toBe(true);
  });
});
