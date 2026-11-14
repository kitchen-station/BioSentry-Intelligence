// @vitest-environment jsdom
/**
 * The draw-on reveal: which bins a redraw starts from, and when the line is shown at once instead
 * of animated (reduced motion, or nothing new to reveal). A clinical line is never left clipped.
 */

import { cleanup, render, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { DrawOn, useDrawFrom } from "./DrawOn";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("useDrawFrom", () => {
  it("draws the whole line first, then only the appended bins, and all again when replaced", () => {
    const { result, rerender } = renderHook(({ keys }) => useDrawFrom(keys), {
      initialProps: { keys: ["a", "b", "c"] },
    });
    expect(result.current).toMatchObject({ from: -1, version: 0 });

    rerender({ keys: ["a", "b", "c"] }); // same series: no redraw
    expect(result.current).toMatchObject({ from: -1, version: 0 });

    rerender({ keys: ["b", "c", "d"] }); // one bin slid in: start after the last old one
    expect(result.current).toMatchObject({ from: 1, version: 1 });

    rerender({ keys: ["x", "y", "z"] }); // a different series: draw it all
    expect(result.current).toMatchObject({ from: -1, version: 2 });
  });

  it("stays still when the series is empty again (a new empty array each render must not loop)", () => {
    const { result, rerender } = renderHook(({ keys }) => useDrawFrom(keys), { initialProps: { keys: [] as string[] } });
    rerender({ keys: [] });
    expect(result.current).toMatchObject({ from: -1, version: 0 });
    rerender({ keys: ["a"] }); // the first bin: draw it
    expect(result.current).toMatchObject({ from: -1, version: 1 });
  });
});

const widthAfter = (fromX: number, toX: number) => {
  const { container } = render(
    <svg>
      <DrawOn fromX={fromX} toX={toX} height={10} version={1}>
        <path d="M0 0" />
      </DrawOn>
    </svg>,
  );
  return container.querySelector("rect")?.getAttribute("width");
};

describe("DrawOn shows the line at once", () => {
  it("when the viewer prefers reduced motion", () => {
    vi.stubGlobal("matchMedia", (q: string) => ({ matches: q === "(prefers-reduced-motion: reduce)" }));
    expect(widthAfter(0, 100)).toBe("100");
  });

  it("when there is nothing new to reveal", () => {
    vi.stubGlobal("matchMedia", () => ({ matches: false }));
    expect(widthAfter(100, 100)).toBe("100");
  });

  it("otherwise starts clipped at fromX and animates", () => {
    vi.stubGlobal("matchMedia", () => ({ matches: false }));
    expect(widthAfter(20, 100)).toBe("20");
  });
});
