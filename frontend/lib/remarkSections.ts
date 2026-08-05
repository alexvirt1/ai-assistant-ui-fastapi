/**
 * Turns `[Section 148]` in an answer into something the reader can open.
 *
 * The model is told to cite the section behind every fact, but a citation
 * nobody can check is only a claim about a claim — and this is exactly where
 * the model is least reliable. Testing on War and Peace it cited real sections
 * while merging two different scenes into one answer, which is invisible unless
 * you can read the passage.
 *
 * Emits ordinary mdast link nodes rather than a custom node type, so this rides
 * the existing markdown pipeline: the only renderer change is mapping the `a`
 * component. See SECTION_HREF_PREFIX for why the href is a hash.
 *
 * The tree walk is hand-written rather than pulling in `unist-util-visit`:
 * pnpm's strict layout means that is not importable without adding it plus
 * `unified` and `@types/mdast` as direct dependencies, which is a lot of
 * package for one traversal.
 */

/** `[Section 12]`, and the plural the model sometimes writes. */
const SECTION = /\[Sections?\s+(\d+)\]/g;

/**
 * The href a citation link carries.
 *
 * A hash, not a custom `section:` protocol. react-markdown runs every URL
 * through `defaultUrlTransform`, which permits only http, https, irc, mailto
 * and xmpp — anything else is rewritten to the empty string. `section:148`
 * therefore arrived at the renderer as `href=""` and every citation fell back
 * to plain text, which is precisely how this shipped broken the first time.
 * A leading `#` contains no colon, so the sanitiser passes it through
 * untouched, and `javascript:` stays blocked as before.
 *
 * Any future change to this value must keep that property: no colon before the
 * first `/`, `?` or `#`.
 */
export const SECTION_HREF_PREFIX = "#section-";

type Node = {
  type: string;
  value?: string;
  url?: string;
  children?: Node[];
};

/** The section number a citation link points at, or null if it is a real link. */
export function sectionFromHref(href: string): number | null {
  if (!href.startsWith(SECTION_HREF_PREFIX)) return null;
  const value = Number(href.slice(SECTION_HREF_PREFIX.length));
  return Number.isInteger(value) && value > 0 ? value : null;
}

/** Split one text node into text and citation-link nodes. */
function splitCitations(value: string): Node[] | null {
  SECTION.lastIndex = 0;
  const matches = [...value.matchAll(SECTION)];
  if (matches.length === 0) return null;

  const out: Node[] = [];
  let cursor = 0;
  for (const match of matches) {
    const start = match.index ?? 0;
    if (start > cursor) out.push({ type: "text", value: value.slice(cursor, start) });
    out.push({
      type: "link",
      url: `${SECTION_HREF_PREFIX}${match[1]}`,
      children: [{ type: "text", value: match[0] }],
    });
    cursor = start + match[0].length;
  }
  if (cursor < value.length) out.push({ type: "text", value: value.slice(cursor) });
  return out;
}

function walk(node: Node): void {
  const children = node.children;
  if (!children) return;

  for (let i = 0; i < children.length; i += 1) {
    const child = children[i]!;
    // Never rewrite inside a link: a citation the model already wrapped in one
    // would become a nested link, which is invalid markup.
    if (child.type === "link") continue;

    if (child.type === "text" && typeof child.value === "string") {
      const replacement = splitCitations(child.value);
      if (replacement) {
        children.splice(i, 1, ...replacement);
        // Skip what was just inserted. Only an optimisation: re-visiting it is
        // harmless, because the inserted links hit the guard above and the
        // inserted text nodes have had every citation split out of them.
        i += replacement.length - 1;
      }
      continue;
    }
    walk(child);
  }
}

/** Remark plugin. Typed loosely so no `unified` import is needed. */
export function remarkSections() {
  return (tree: Node): void => walk(tree);
}
