import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@assistant-ui/react", () => ({
  BranchPicker: () => null,
  MessagePrimitive: { If: ({ children }: { children: React.ReactNode }) => children },
  UserActionBar: () => null,
  UserMessage: {},
}));

import { ReferenceThumbs } from "./ReferenceThumbs";
import { UserText } from "./UserMessageWithReferences";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const refs = [
  { id: "r1", name: "dog.jpg" },
  { id: "r2", name: "cat.png" },
];

describe("ReferenceThumbs", () => {
  it("shows a thumbnail per photo", () => {
    render(<ReferenceThumbs refs={refs} />);
    const images = screen.getAllByRole("img");
    expect(images.map((img) => img.getAttribute("src"))).toEqual(["/api/references/r1", "/api/references/r2"]);
  });

  it("enlarges on click and deletes after confirmation", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const fetchMock = vi.fn(async () => ({ ok: true, status: 204 }) as Response);
    vi.stubGlobal("fetch", fetchMock);
    render(<ReferenceThumbs refs={[refs[0]!]} />);
    fireEvent.click(screen.getByRole("button", { name: "Enlarge reference photo dog.jpg" }));
    expect(screen.getByRole("dialog", { name: "Reference photo dog.jpg" })).toBeTruthy();
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Delete photo" })));
    expect(fetchMock).toHaveBeenCalledWith("/api/references/r1", { method: "DELETE" });
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.getByText("Photo deleted")).toBeTruthy();
  });

  it("says when a photo was deleted elsewhere", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: false, status: 410 }) as Response));
    render(<ReferenceThumbs refs={[refs[0]!]} />);
    await act(async () => fireEvent.error(screen.getByRole("img")));
    expect(screen.getByText("Photo deleted")).toBeTruthy();
  });
});

describe("UserText", () => {
  it("shows a reloaded message's markers as thumbnails", () => {
    const text = 'Make him an astronaut\n<reference-image id="r1" name="dog.jpg" />\n';
    render(<UserText type="text" text={text} status={{ type: "complete" }} />);
    expect(screen.getByText("Make him an astronaut")).toBeTruthy();
    expect(screen.getByRole("img", { name: "dog.jpg" })).toBeTruthy();
    expect(screen.queryByText(/reference-image/)).toBeNull();
  });
});
