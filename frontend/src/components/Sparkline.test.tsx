import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { Sparkline } from "./Sparkline";
import { TrendChart } from "./TrendChart";

describe("charts draw no dots", () => {
  it("sparkline marks the live end and lone readings with ticks, not circles", () => {
    const html = renderToStaticMarkup(<Sparkline data={[1, null, 3, 4, 5]} color="red" />);
    expect(html).not.toContain("<circle");
    expect(html.match(/<line /g)).toHaveLength(2); // lone reading + live end
  });

  it("trend chart has no pulsing dot", () => {
    const t = (m: number) => new Date(Date.UTC(2026, 0, 1, 0, m)).toISOString();
    const html = renderToStaticMarkup(
      <TrendChart label="HR" unit="bpm" data={[{ time: t(0), value: 70 }, { time: t(15), value: 80 }]} />,
    );
    expect(html).not.toContain("<circle");
    expect(html).not.toContain("<animate");
  });
});
