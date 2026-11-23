import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// The card is rendered on its own; useMessage (for the waiting dot) needs a
// runtime, so it reports "not waiting".
vi.mock("@assistant-ui/react", () => ({
  makeAssistantToolUI: (config: unknown) => config,
  useMessage: () => false,
}));

import { ImageCard } from "./GeneratedImage";

const image = {
  imageId: "abc",
  model: "z-image-turbo",
  width: 1024,
  height: 1024,
  seed: 42,
  prompt: "A lighthouse at dusk",
  references: [] as { id: string; name: string }[],
};

beforeEach(() => {
  URL.createObjectURL = vi.fn(() => "blob:png");
  URL.revokeObjectURL = vi.fn();
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("ImageCard", () => {
  it("shows the reference photos it was made from", () => {
    render(<ImageCard image={{ ...image, references: [{ id: "r1", name: "dog.jpg" }] }} />);
    expect(screen.getByText("References:")).toBeTruthy();
    expect(screen.getByRole("img", { name: "dog.jpg" }).getAttribute("src")).toBe("/api/references/r1");
  });

  it("shows no references row without references", () => {
    render(<ImageCard image={image} />);
    expect(screen.queryByText("References:")).toBeNull();
  });

  it("shows the image, its prompt and details", () => {
    render(<ImageCard image={image} />);
    const img = screen.getByRole("img", { name: "A lighthouse at dusk" });
    expect(img.getAttribute("src")).toBe("/api/images/abc");
    expect(screen.getByText(/z-image-turbo · 1024×1024 · seed 42/)).toBeTruthy();
  });

  it("downloads the PNG under a name from the prompt", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({ ok: true, status: 200, blob: async () => new Blob(["png"]) }) as Response),
    );
    const saved: string[] = [];
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
      saved.push(this.download);
    });
    render(<ImageCard image={image} />);
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Download" })));
    expect(saved).toEqual(["a-lighthouse-at-dusk.png"]);
  });

  it("deletes after confirmation and says so", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const fetchMock = vi.fn(async () => ({ ok: true, status: 204 }) as Response);
    vi.stubGlobal("fetch", fetchMock);
    render(<ImageCard image={image} />);
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Delete" })));
    expect(fetchMock).toHaveBeenCalledWith("/api/images/abc", { method: "DELETE" });
    expect(screen.getByText(/Image deleted/)).toBeTruthy();
    expect(screen.queryByRole("img")).toBeNull();
  });

  it("does nothing when the delete is not confirmed", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(false);
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    render(<ImageCard image={image} />);
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Delete" })));
    expect(fetchMock).not.toHaveBeenCalled();
    expect(screen.getByRole("img", { name: "A lighthouse at dusk" })).toBeTruthy();
  });

  it("keeps the image and shows the reason when a delete fails", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: false, status: 500 }) as Response));
    render(<ImageCard image={image} />);
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Delete" })));
    expect(screen.getByRole("alert").textContent).toContain("Delete failed (500)");
    expect(screen.getByRole("img", { name: "A lighthouse at dusk" })).toBeTruthy();
  });

  it("shows 'Image deleted' when a restored image is gone", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: false, status: 410 }) as Response));
    render(<ImageCard image={image} />);
    await act(async () => fireEvent.error(screen.getByRole("img", { name: "A lighthouse at dusk" })));
    expect(screen.getByText(/Image deleted/)).toBeTruthy();
  });

  it("opens full size and closes on Escape", () => {
    render(<ImageCard image={image} />);
    fireEvent.click(screen.getByRole("button", { name: "Enlarge image" }));
    expect(screen.getByRole("dialog", { name: "Enlarged image" })).toBeTruthy();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});

describe("GeneratedImageUI", () => {
  it("shows progress while running and the card once done", async () => {
    const { GeneratedImageUI } = await import("./GeneratedImage");
    const config = GeneratedImageUI as unknown as {
      render: (props: Record<string, unknown>) => React.ReactElement;
    };
    const running = render(
      config.render({ args: { model: "flux2-klein" }, status: { type: "running" }, toolCallId: "c1" }),
    );
    expect(running.container.textContent).toContain("Generating image with flux2-klein");
    running.unmount();

    render(
      config.render({
        args: {},
        result: "Error: The image server on AI-1 is busy",
        status: { type: "complete" },
        toolCallId: "c1",
      }),
    );
    expect(screen.getByRole("alert").textContent).toContain("is busy");
  });
});
