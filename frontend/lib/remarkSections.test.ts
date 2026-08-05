import { describe, expect, it } from "vitest";

import { SECTION_HREF_PREFIX, remarkSections, sectionFromHref } from "./remarkSections";

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
    expect(sectionFromHref(`${SECTION_HREF_PREFIX}148`)).toBe(148);
  });

  it("ignores ordinary links", () => {
    // Otherwise a real URL in an answer would be hijacked into a citation.
    expect(sectionFromHref("https://example.com")).toBeNull();
  });

  it("rejects a non-numeric section", () => {
    expect(sectionFromHref(`${SECTION_HREF_PREFIX}abc`)).toBeNull();
  });

  it("rejects section zero, since sections are one-based", () => {
    expect(sectionFromHref(`${SECTION_HREF_PREFIX}0`)).toBeNull();
  });
});

describe("remarkSections", () => {
  it("turns a citation into a link node", () => {
    const nodes = paragraphOf(run(para(text("See [Section 148] for this."))));
    const link = nodes.find((n) => n.type === "link");
    expect(link?.url).toBe(`${SECTION_HREF_PREFIX}148`);
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
    expect(urls).toEqual([`${SECTION_HREF_PREFIX}148`, `${SECTION_HREF_PREFIX}149`]);
  });

  it("handles the plural form the model sometimes writes", () => {
    const nodes = paragraphOf(run(para(text("[Sections 12]"))));
    expect(nodes[0]!.url).toBe(`${SECTION_HREF_PREFIX}12`);
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
    expect(emphasis.children!.some((n) => n.url === `${SECTION_HREF_PREFIX}3`)).toBe(true);
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

describe("surviving react-markdown's URL sanitiser", () => {
  /**
   * REGRESSION: the first version used a `section:148` URL. react-markdown runs
   * every href through defaultUrlTransform, which allows only http, https, irc,
   * mailto and xmpp and rewrites anything else to "". Citations reached the
   * renderer with href="" and silently fell back to plain text — every unit
   * test passed, because none of them went through react-markdown.
   *
   * This reimplements defaultUrlTransform's rule rather than importing it:
   * react-markdown is a transitive dependency and pnpm's strict layout makes it
   * unimportable from here without adding it as a direct one.
   */
  const survivesSanitiser = (url: string): boolean => {
    const colon = url.indexOf(":");
    const question = url.indexOf("?");
    const hash = url.indexOf("#");
    const slash = url.indexOf("/");
    return (
      colon === -1 ||
      (slash !== -1 && colon > slash) ||
      (question !== -1 && colon > question) ||
      (hash !== -1 && colon > hash) ||
      /^(https?|ircs?|mailto|xmpp)$/i.test(url.slice(0, colon))
    );
  };

  it("the rule is right: it strips what react-markdown strips", () => {
    expect(survivesSanitiser("section:148")).toBe(false);
    expect(survivesSanitiser("javascript:alert(1)")).toBe(false);
    expect(survivesSanitiser("https://example.com")).toBe(true);
  });

  it("the citation href survives it", () => {
    expect(survivesSanitiser(`${SECTION_HREF_PREFIX}148`)).toBe(true);
  });

  it("every href the plugin emits survives it", () => {
    const tree = para(text("[Section 1], [Section 148] and [Section 6238]"));
    run(tree);
    const urls = paragraphOf(tree)
      .filter((n) => n.type === "link")
      .map((n) => n.url!);
    expect(urls).toHaveLength(3);
    for (const url of urls) expect(survivesSanitiser(url)).toBe(true);
  });
});
