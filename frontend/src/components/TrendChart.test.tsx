// @vitest-environment jsdom
/** The trend chart on the kit graph: the finding in the title, what the kit cannot draw in words, the readout and the table. */

import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { trend } from "hw-ui";
import { formatClock } from "./format";
import { TrendChart } from "./TrendChart";

// The kit's own graph, watched: the clinical options must reach it.
vi.mock("hw-ui", async (importOriginal) => {
  const real = await importOriginal<typeof import("hw-ui")>();
  return { ...real, trend: vi.fn(real.trend) };
});

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
afterEach(cleanup);

// jsdom lays nothing out: give the plot a width so the kit draws its line and says the readout.
beforeAll(() => Object.defineProperty(HTMLElement.prototype, "clientWidth", { configurable: true, get: () => 600 }));
afterAll(() => Reflect.deleteProperty(HTMLElement.prototype, "clientWidth")); // back to jsdom's own (on Element)

const t = (m: number) => new Date(Date.UTC(2026, 0, 1, 0, m)).toISOString();
const two = [{ time: t(0), value: 70 }, { time: t(15), value: 90 }];
const figure = () => screen.getByRole("figure");
const subtitle = () => figure().querySelector(".hw-graph__subtitle")?.textContent;

describe("TrendChart", () => {
  it("is a figure titled with the latest value, with the warning lines in words", () => {
    render(<TrendChart label="HR" unit="bpm" warningHigh={130} warningLow={40} data={two} />);
    expect(screen.getByRole("heading", { level: 3 }).textContent).toBe("HR 90 bpm");
    expect(figure().getAttribute("aria-labelledby")).toBe(screen.getByRole("heading").id);
    expect(subtitle()).toBe("Warning above 130 or below 40 bpm");
    expect(screen.getByRole("group", { name: "HR 90 bpm. Use the left and right arrow keys to read each point." })).toBeTruthy();
  });

  it("words one warning line alone, and none without one", () => {
    const { rerender } = render(<TrendChart label="SpO2" unit="%" warningLow={94} data={two} />);
    expect(subtitle()).toBe("Warning below 94 %");
    rerender(<TrendChart label="SpO2" unit="%" warningHigh={99} data={two} />);
    expect(subtitle()).toBe("Warning above 99 %");
    rerender(<TrendChart label="SpO2" unit="%" data={two} />);
    expect(subtitle()).toBeUndefined();
  });

  it("reads the latest value to one decimal, and every value in the table", async () => {
    render(<TrendChart label="HR" unit="bpm" data={[{ time: t(0), value: 70 }, { time: t(15), value: 108.51226339288638 }]} />);
    const readout = figure().querySelector(".hw-graph__readout")!;
    expect(readout.textContent).toBe(`108.5 bpmHR, ${formatClock(Date.parse(t(15)))}`);
    expect(figure().textContent).not.toContain("108.51");
    await userEvent.click(screen.getByRole("button", { name: "Show as a table" }));
    const rows = within(screen.getByRole("table")).getAllByRole("row").map((r) => r.textContent);
    expect(rows).toEqual(["TimeHR", `${formatClock(Date.parse(t(0)))}70 bpm`, `${formatClock(Date.parse(t(15)))}108.5 bpm`]);
  });

  it("says the latest 15-minute set is missing when it is a gap, and shows the gap as a dash", async () => {
    render(<TrendChart label="HR" unit="bpm" data={[{ time: t(0), value: 70 }, { time: t(15), value: null }]} />);
    expect(screen.getByRole("heading").textContent).toBe("HR: latest 15-minute set missing");
    await userEvent.click(screen.getByRole("button", { name: "Show as a table" }));
    expect(within(screen.getByRole("table")).getAllByRole("row")[2].textContent).toBe(`${formatClock(Date.parse(t(15)))}–`);
  });

  it("labels a time it cannot read as sent, and a set on an earlier day with its date", async () => {
    const day = Date.UTC(2026, 0, 2, 0, 0);
    render(<TrendChart label="HR" unit="bpm" data={[{ time: "soon", value: 70 }, { time: t(0), value: 72 }, { time: new Date(day).toISOString(), value: 75 }]} />);
    await userEvent.click(screen.getByRole("button", { name: "Show as a table" }));
    const heads = within(screen.getByRole("table")).getAllByRole("rowheader").map((h) => h.textContent);
    expect(heads[0]).toBe("soon");
    expect(heads[1]).not.toBe(formatClock(Date.parse(t(0)))); // another day: the date comes with it
    expect(heads[1]).toContain(formatClock(Date.parse(t(0))));
    expect(heads[2]).toBe(formatClock(day));
  });

  it.each([
    [62, 78, "above"],
    [92, 110, "below"],
    [80, 95, "within"],
  ])("says the latest value against an own baseline of %i to %i: %s", (low, high, where) => {
    render(<TrendChart label="HR" unit="bpm" warningHigh={90} data={two} band={{ low, high, flagged: false }} />);
    expect(screen.getByRole("heading").textContent).toBe(`HR 90 bpm, ${where} own baseline`);
    expect(subtitle()).toBe(`Warning above 90 bpm · Own baseline ${low} to ${high} bpm`);
  });

  it("says when the latest value is past a warning line, with the baseline after it", () => {
    const { rerender } = render(<TrendChart label="HR" unit="bpm" warningHigh={80} data={two} />);
    expect(screen.getByRole("heading").textContent).toBe("HR 90 bpm, above the warning line");
    rerender(<TrendChart label="HR" unit="bpm" warningLow={95} data={two} />);
    expect(screen.getByRole("heading").textContent).toBe("HR 90 bpm, below the warning line");
    rerender(<TrendChart label="HR" unit="bpm" warningHigh={80} data={two} band={{ low: 62, high: 78, flagged: false }} />);
    expect(screen.getByRole("heading").textContent).toBe("HR 90 bpm, above the warning line and above own baseline");
  });

  it("says Flagged when the server flagged the band, and no band without one", () => {
    const { rerender } = render(<TrendChart label="HR" unit="bpm" data={two} band={{ low: 62, high: 78, flagged: true }} />);
    expect(subtitle()).toBe("Own baseline 62 to 78 bpm · Flagged");
    rerender(<TrendChart label="HR" unit="bpm" data={two} />);
    expect(figure().textContent).not.toContain("Own baseline");
    expect(figure().textContent).not.toContain("own baseline");
  });

  it("draws nothing for fewer than two sets or no reading at all", () => {
    for (const data of [[{ time: t(0), value: 70 }], [{ time: t(0), value: null }, { time: t(15), value: null }]]) {
      const { container, unmount } = render(<TrendChart label="HR" unit="bpm" data={data} />);
      expect(container.innerHTML).toBe("");
      unmount();
    }
  });

  it("gives the kit the clinical range, the limits, the named own band and the points stretch, and none without them", () => {
    const adds = (v: number) => v > 80;
    const limits = [{ at: 80, label: "+1 point above 80" }];
    render(<TrendChart label="HR" unit="bpm" data={two} range={[40, 140]} limits={limits} band={{ low: 62, high: 78, flagged: false }} highlightWhere={adds} />);
    const config = vi.mocked(trend).mock.calls.at(-1)![1];
    expect(config).toMatchObject({ range: [40, 140], limits, band: [62, 78], bandLabel: "Own baseline 62–78" });
    expect(config.highlightWhere).toBe(adds);
    cleanup();
    render(<TrendChart label="HR" unit="bpm" data={two} />);
    const plain = vi.mocked(trend).mock.calls.at(-1)![1];
    for (const key of ["range", "limits", "band", "bandLabel", "highlightWhere"]) expect(plain, key).not.toHaveProperty(key);
  });

  it("is drawn again when the points rule changes, not when the same one comes back", () => {
    const a = (v: number) => v > 80;
    const { rerender } = render(<TrendChart label="HR" unit="bpm" data={two} highlightWhere={a} />);
    const drawn = vi.mocked(trend).mock.calls.length;
    rerender(<TrendChart label="HR" unit="bpm" data={two} highlightWhere={a} />);
    expect(vi.mocked(trend).mock.calls.length).toBe(drawn);
    rerender(<TrendChart label="HR" unit="bpm" data={two} highlightWhere={(v) => v > 70} />);
    expect(vi.mocked(trend).mock.calls.length).toBe(drawn + 1);
  });

  it("is not rebuilt for the same figures, is for new ones, and goes when the data does", () => {
    const { container, rerender } = render(<TrendChart label="HR" unit="bpm" data={two} />);
    const heading = screen.getByRole("heading");
    rerender(<TrendChart label="HR" unit="bpm" data={two.map((p) => ({ ...p }))} />);
    expect(screen.getByRole("heading")).toBe(heading);
    rerender(<TrendChart label="HR" unit="bpm" data={[...two, { time: t(30), value: 95 }]} />);
    expect(screen.getByRole("heading").textContent).toBe("HR 95 bpm");
    rerender(<TrendChart label="HR" unit="bpm" data={[]} />);
    expect(container.innerHTML).toBe("");
  });
});
