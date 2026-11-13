// @vitest-environment jsdom
/**
 * The flip and the row move: the motion the two apps agreed, and the rules that keep a value
 * readable at once (the new text is in the DOM from the first frame, the old one is only painted).
 */

import { act, cleanup, fireEvent, render, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { readFileSync } from "node:fs";
import { Flip, useMoveOnReorder } from "./Flip";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const css = readFileSync("src/components/Flip.module.css", "utf8");
const tokens = readFileSync("src/index.css", "utf8");
const flipping = (c: HTMLElement) => c.querySelector('[class*="_flip_"]');
const ghost = (c: HTMLElement) => c.querySelector("[data-old]");
const reduce = (on: boolean) => vi.stubGlobal("matchMedia", (q: string) => ({ matches: on && q === "(prefers-reduced-motion: reduce)" }));

beforeEach(() => {
  vi.useFakeTimers();
  reduce(false);
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("Flip", () => {
  it("does not flip on first render, nor when the same value comes again", () => {
    const { container, rerender } = render(<Flip>{7}</Flip>);
    expect(flipping(container)).toBeNull();
    rerender(<Flip>{7}</Flip>);
    expect(flipping(container)).toBeNull();
    expect(ghost(container)).toBeNull();
  });

  it("shows the new text at once, paints the old one from a data attribute, and ends on animationend", () => {
    const { container, rerender, queryByText, getByText } = render(<Flip>7</Flip>);
    rerender(<Flip>8</Flip>);
    expect(getByText("8")).toBeTruthy();
    expect(queryByText("7")).toBeNull(); // no old text node exists
    expect(flipping(container)).not.toBeNull();
    expect(ghost(container)?.getAttribute("data-old")).toBe("7");
    expect(ghost(container)?.getAttribute("aria-hidden")).toBe("true");
    expect(ghost(container)?.textContent).toBe("");
    // jsdom has no AnimationEvent, so React listens for the prefixed name there.
    fireEvent(flipping(container) as Element, new Event("webkitAnimationEnd", { bubbles: true }));
    expect(ghost(container)).toBeNull();
    expect(flipping(container)).toBeNull();
  });

  it("ends by a timer at 320 ms when animationend never comes", () => {
    const { container, rerender } = render(<Flip>a</Flip>);
    rerender(<Flip>b</Flip>);
    act(() => void vi.advanceTimersByTime(319));
    expect(ghost(container)).not.toBeNull();
    act(() => void vi.advanceTimersByTime(1));
    expect(ghost(container)).toBeNull();
    expect(flipping(container)).toBeNull();
  });

  it("flips once in 400 ms: a change at 100 ms swaps the text with no second ghost and no restart", () => {
    const { container, rerender, getByText } = render(<Flip>1</Flip>);
    rerender(<Flip>2</Flip>);
    act(() => void vi.advanceTimersByTime(100));
    rerender(<Flip>3</Flip>);
    expect(getByText("3")).toBeTruthy();
    expect(container.querySelectorAll("[data-old]")).toHaveLength(1);
    expect(ghost(container)?.getAttribute("data-old")).toBe("1");
    act(() => void vi.advanceTimersByTime(220)); // the first flip's own 320 ms: not restarted
    expect(ghost(container)).toBeNull();
  });

  it("flips again for a change 450 ms after the last flip", () => {
    const { container, rerender } = render(<Flip>1</Flip>);
    rerender(<Flip>2</Flip>);
    act(() => void vi.advanceTimersByTime(450));
    rerender(<Flip>3</Flip>);
    expect(ghost(container)?.getAttribute("data-old")).toBe("2");
  });

  it("never flips with still", () => {
    const { container, rerender } = render(<Flip still>1</Flip>);
    rerender(<Flip still>2</Flip>);
    expect(flipping(container)).toBeNull();
    expect(ghost(container)).toBeNull();
  });

  it("renders no ghost and no flip class under reduced motion", () => {
    reduce(true);
    const { container, rerender, getByText } = render(<Flip>1</Flip>);
    rerender(<Flip>2</Flip>);
    expect(getByText("2")).toBeTruthy();
    expect(flipping(container)).toBeNull();
    expect(ghost(container)).toBeNull();
  });

  it("works where matchMedia does not exist", () => {
    vi.stubGlobal("matchMedia", undefined);
    const { container, rerender } = render(<Flip>1</Flip>);
    rerender(<Flip>2</Flip>);
    expect(ghost(container)).not.toBeNull();
  });

  it("leaves no timer and no warning when it unmounts mid-flip", () => {
    const error = vi.spyOn(console, "error").mockImplementation(() => undefined);
    const { rerender, unmount } = render(<Flip>1</Flip>);
    rerender(<Flip>2</Flip>);
    expect(vi.getTimerCount()).toBe(1);
    unmount();
    expect(vi.getTimerCount()).toBe(0);
    act(() => void vi.advanceTimersByTime(1000));
    expect(error).not.toHaveBeenCalled();
    error.mockRestore();
  });
});

describe("the agreed motion in the CSS", () => {
  it("holds the numbers, the names and the reduced-motion rules", () => {
    expect(tokens).toContain("--flip-ms: 320ms");
    expect(tokens).toContain("--move-ms: 420ms");
    expect(tokens).toContain("--flip-ease: cubic-bezier(0.2, 0.8, 0.2, 1)");
    expect(css).toContain("perspective: 400px");
    expect(css).toContain("backface-visibility: hidden");
    expect(css).toContain("animation: flip-in var(--flip-ms) var(--flip-ease)");
    expect(css).toContain("animation: flip-out var(--flip-ms) var(--flip-ease)");
    expect(css).toMatch(/@media \(prefers-reduced-motion: reduce\) \{\s*\.flip \{\s*animation: none;\s*\}\s*\.flip-old \{\s*display: none;/);
  });

  it("animates only transform and opacity in the two keyframes", () => {
    const frames = [...css.matchAll(/@keyframes (flip-out|flip-in) \{([\s\S]*?)\n\}/g)];
    expect(frames.map((m) => m[1])).toEqual(["flip-out", "flip-in"]);
    for (const m of frames) {
      const props = [...m[2].matchAll(/^\s+([\w-]+):/gm)].map((p) => p[1]);
      expect(new Set(props)).toEqual(new Set(["transform", "opacity"]));
    }
  });
});

describe("useMoveOnReorder", () => {
  let top: Record<string, number>;
  let frames: FrameRequestCallback[];
  beforeEach(() => {
    top = { a: 0, b: 50 };
    frames = [];
    vi.stubGlobal("requestAnimationFrame", (f: FrameRequestCallback) => frames.push(f));
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (this: HTMLElement) {
      return { top: top[this.id] ?? 0 } as DOMRect;
    });
  });
  afterEach(() => vi.restoreAllMocks());

  const mount = (keys: string[]) => {
    const els = new Map<string, HTMLElement>();
    for (const k of [...keys, "c"]) {
      const el = document.createElement("div");
      el.id = k;
      els.set(k, el);
    }
    const hook = renderHook(({ keys: ks }) => useMoveOnReorder(ks), { initialProps: { keys } });
    const attach = () => hook.result.current && ["a", "b", "c"].forEach((k) => hook.result.current(k)(els.get(k) as HTMLElement));
    attach();
    // The refs are set after the first layout effect in a real tree: set them and render the same keys again.
    hook.rerender({ keys });
    return { els, hook, attach };
  };

  it("inverts a moved row, then plays the transition; a still row gets nothing", () => {
    const { els, hook } = mount(["a", "b"]);
    top = { a: 50, b: 0 };
    hook.rerender({ keys: ["b", "a"] });
    expect(els.get("a")?.style.transform).toBe("translateY(-50px)");
    expect(els.get("b")?.style.transform).toBe("translateY(50px)");
    frames.forEach((f) => f(0));
    expect(els.get("a")?.style.transition).toBe("transform var(--move-ms) var(--flip-ease)");
    expect(els.get("a")?.style.transform).toBe("");
    act(() => void vi.advanceTimersByTime(470));
    expect(els.get("a")?.style.transition).toBe("");
  });

  it("does not move a row that kept its place, nor a new row", () => {
    const { els, hook } = mount(["a", "b"]);
    top = { a: 0, b: 50, c: 100 };
    hook.rerender({ keys: ["a", "b", "c"] });
    for (const k of ["a", "b", "c"]) expect(els.get(k)?.style.transform).toBe("");
  });

  it("keeps a row's recorded place while it moves, and lets a row go", () => {
    const { els, hook } = mount(["a", "b"]);
    top = { a: 50, b: 0 };
    hook.rerender({ keys: ["b", "a"] });
    hook.rerender({ keys: ["b", "a"] }); // a tick in mid-move: no new move
    expect(els.get("a")?.style.transform).toBe("translateY(-50px)");
    top = { a: 0, b: 0 };
    hook.rerender({ keys: ["a"] }); // b left, a moves again: its running move is settled first
    expect(els.get("a")?.style.transform).toBe("translateY(50px)");
  });

  it("does not move under reduced motion", () => {
    reduce(true);
    const { els, hook } = mount(["a", "b"]);
    top = { a: 50, b: 0 };
    hook.rerender({ keys: ["b", "a"] });
    expect(els.get("a")?.style.transform).toBe("");
  });

  it("skips a key with no element, and leaves no timer after unmount", () => {
    const { hook } = mount(["a", "b"]);
    hook.result.current("a")(null);
    top = { a: 50, b: 0 };
    hook.rerender({ keys: ["b", "a", "zz"] });
    hook.unmount();
    expect(vi.getTimerCount()).toBe(0);
  });
});
