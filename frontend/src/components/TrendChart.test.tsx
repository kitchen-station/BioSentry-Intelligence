// @vitest-environment jsdom
/** The trend chart's warning thresholds and its latest reading. */

import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { TrendChart } from "./TrendChart";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
afterEach(cleanup);

const t = (m: number) => new Date(Date.UTC(2026, 0, 1, 0, m)).toISOString();

describe("TrendChart", () => {
  it("draws a dashed line and a label at each warning threshold", () => {
    const { container } = render(
      <TrendChart
        label="HR"
        unit="bpm"
        warningHigh={130}
        warningLow={40}
        data={[{ time: t(0), value: 70 }, { time: t(15), value: 90 }]}
      />,
    );
    const dashed = container.querySelectorAll('line[stroke-dasharray="6,4"]');
    expect(dashed).toHaveLength(2);
    const labels = [...container.querySelectorAll("text")].map((n) => n.textContent);
    expect(labels).toContain("130");
    expect(labels).toContain("40");
    expect(container.textContent).toContain("90 bpm");
  });

  it("draws no threshold without one, and shows -- when the latest bin is a gap", () => {
    const { container } = render(
      <TrendChart label="HR" unit="bpm" data={[{ time: t(0), value: 70 }, { time: t(15), value: null }]} />,
    );
    expect(container.querySelectorAll('line[stroke-dasharray="6,4"]')).toHaveLength(0);
    expect(container.textContent).toContain("--");
  });

  // Without warning lines the range is 5% under the lowest and over the highest reading; the plot
  // is 108 px high from y 8 (height 140, padT 8, padB 24).
  const two = [{ time: t(0), value: 70 }, { time: t(15), value: 90 }];
  const yOf = (v: number) => 8 + 108 - ((v - 66.5) / 28) * 108;
  const rect = (c: HTMLElement) => c.querySelector('rect[data-testid="own-baseline-band"]');

  it("draws the server's band behind the line at the rows its numbers map to", () => {
    const { container } = render(
      <TrendChart label="HR" unit="bpm" data={two} band={{ low: 75, high: 85, flagged: false }} />,
    );
    const r = rect(container)!;
    expect(Number(r.getAttribute("y"))).toBeCloseTo(yOf(85));
    expect(Number(r.getAttribute("y")) + Number(r.getAttribute("height"))).toBeCloseTo(yOf(75));
    const line = container.querySelector("path[stroke]")!;
    expect(r.compareDocumentPosition(line) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("says the band in words in the header and the accessible text, and Flagged when it is", () => {
    const { container, rerender } = render(
      <TrendChart label="HR" unit="bpm" data={two} band={{ low: 62, high: 78, flagged: false }} />,
    );
    expect(container.textContent).toContain("Own baseline 62 to 78 bpm");
    expect(container.textContent).not.toContain("Flagged");
    expect(container.querySelector("svg")!.getAttribute("aria-label")).toBe("HR trend. Own baseline 62 to 78 bpm");
    rerender(<TrendChart label="HR" unit="bpm" data={two} band={{ low: 62, high: 78, flagged: true }} />);
    expect(container.textContent).toContain("Own baseline 62 to 78 bpm \u00b7 Flagged");
    expect(container.querySelector("svg")!.getAttribute("aria-label")).toContain("Flagged");
  });

  it("clips a band that reaches past the drawn range", () => {
    const { container } = render(
      <TrendChart label="HR" unit="bpm" data={two} band={{ low: 40, high: 80, flagged: false }} />,
    );
    const r = rect(container)!;
    expect(Number(r.getAttribute("y"))).toBeCloseTo(yOf(80));
    expect(Number(r.getAttribute("y")) + Number(r.getAttribute("height"))).toBeCloseTo(yOf(66.5));
  });

  it("draws no rectangle for a band wholly outside the range, and keeps the words", () => {
    for (const [low, high] of [[100, 120], [10, 20]]) {
      const { container, unmount } = render(
        <TrendChart label="HR" unit="bpm" data={two} band={{ low, high, flagged: false }} />,
      );
      expect(rect(container)).toBeNull();
      expect(container.textContent).toContain(`Own baseline ${low} to ${high} bpm`);
      unmount();
    }
  });

  it("has no band and no words without one", () => {
    const { container } = render(<TrendChart label="HR" unit="bpm" data={two} />);
    expect(rect(container)).toBeNull();
    expect(container.textContent).not.toContain("Own baseline");
    expect(container.querySelector("svg")!.getAttribute("aria-label")).toBe("HR trend");
  });
});
