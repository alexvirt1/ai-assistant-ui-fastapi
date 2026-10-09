type Mermaid = typeof import("mermaid").default;

let loading: Promise<Mermaid> | null = null;

/**
 * Mermaid, loaded the first time a diagram is drawn.
 *
 * Imported dynamically because it is by far the largest dependency in the
 * app; most answers have no diagram, and they should not pay for it.
 */
function loadMermaid(): Promise<Mermaid> {
  loading ??= import("mermaid").then((module) => module.default);
  return loading;
}

let queue: Promise<unknown> = Promise.resolve();

/**
 * Run one Mermaid job at a time.
 *
 * initialize() sets global configuration and render() measures text in a
 * scratch element it adds to the page; two diagrams drawn at once - a
 * restored chat with several - can pick up each other's theme or measurements.
 */
function serially<T>(job: () => Promise<T>): Promise<T> {
  const result = queue.then(job);
  queue = result.catch(() => undefined);
  return result;
}

/**
 * A diagram as an SVG string. Rejects with a readable message if it will not draw.
 *
 * `id` must be unique on the page and contain only characters valid in a CSS
 * selector: Mermaid scopes the diagram's styles with it.
 */
export function drawSvg(
  code: string,
  id: string,
  { dark = false, htmlLabels = true }: { dark?: boolean; htmlLabels?: boolean } = {},
): Promise<string> {
  return serially(async () => {
    const mermaid = await loadMermaid();
    mermaid.initialize({
      startOnLoad: false,
      // Sanitises labels and disables click handlers: the diagram is model
      // output, and "strict" keeps it from carrying script into the page.
      securityLevel: "strict",
      theme: dark ? "dark" : "default",
      htmlLabels,
    });
    // parse() first: render() on invalid input leaves an error graphic
    // attached to <body>, which then shows at the bottom of the page.
    const valid = await mermaid.parse(code, { suppressErrors: true });
    if (!valid) {
      throw new Error("The diagram has a syntax error.");
    }
    return (await mermaid.render(id, code)).svg;
  }).catch((cause: unknown) => {
    document.getElementById(`d${id}`)?.remove();
    throw cause;
  });
}

/**
 * The ```mermaid blocks in an answer, as the PDF export keys them: the text
 * between the fences, trimmed.
 *
 * Only fences at the start of a line - the ones the backend draws as
 * images. One nested in a list stays a code block there either way.
 */
export function mermaidSources(text: string): string[] {
  const fence = /^(`{3,})[ \t]*mermaid[ \t]*\n([\s\S]*?)\n\1`*[ \t]*$/gm;
  return [...text.matchAll(fence)].map((match) => match[2]!.trim()).filter(Boolean);
}

/** Scale the PDF's diagrams are rasterised at: sharp on paper, still small. */
const PRINT_SCALE = 3;

export type DiagramImage = {
  source: string;
  /** PNG, base64 without the data: prefix. */
  png: string;
  /** Size the diagram is drawn at on screen, in CSS pixels. */
  width: number;
  height: number;
};

let printed = 0;

/**
 * A diagram as a PNG for the PDF export, or null if it will not draw.
 *
 * PNG rather than SVG: the backend's PDF library ignores the <style> block
 * Mermaid colours everything with. Drawn light whatever the chat's theme, for
 * paper, and without HTML labels: those are <foreignObject>, which a canvas
 * either cannot draw or refuses to export.
 */
export async function diagramPng(source: string): Promise<DiagramImage | null> {
  try {
    const svg = await drawSvg(source, `mermaid-print-${++printed}`, { htmlLabels: false });
    const viewBox = svg.match(/<svg\b[^>]*\bviewBox="[-\d.]+ [-\d.]+ ([\d.]+) ([\d.]+)"/);
    if (!viewBox) return null;
    const width = Number(viewBox[1]);
    const height = Number(viewBox[2]);
    if (!(width > 0 && height > 0)) return null;

    // Mermaid sizes the root as width="100%" with a max-width style, which
    // an <img> has nothing to resolve against; give it the viewBox's size.
    const sized = svg.replace(/<svg\b[^>]*>/, (root) =>
      root
        .replace(/\s(width|height|style)="[^"]*"/g, "")
        .replace(/^<svg/, `<svg width="${width}" height="${height}"`),
    );
    const image = new Image();
    image.src = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(sized)}`;
    await image.decode();

    const canvas = document.createElement("canvas");
    canvas.width = Math.ceil(width * PRINT_SCALE);
    canvas.height = Math.ceil(height * PRINT_SCALE);
    const context = canvas.getContext("2d");
    if (!context) return null;
    context.fillStyle = "#ffffff";
    context.fillRect(0, 0, canvas.width, canvas.height);
    context.drawImage(image, 0, 0, canvas.width, canvas.height);
    const png = canvas.toDataURL("image/png").replace(/^data:image\/png;base64,/, "");
    return { source, png, width, height };
  } catch {
    // Will not draw, or the canvas refused to export it: the PDF shows the
    // block's source instead.
    return null;
  }
}
