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
});
