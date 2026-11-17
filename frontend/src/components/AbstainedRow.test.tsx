import type { ReactElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import type { AbstainedRow as AbstainedRowType } from "../types";
import { AbstainedRow } from "./AbstainedRow";
import { forgetWardNow, syncWardNow } from "./wardNow";

const row: AbstainedRowType = {
  id: "p-004",
  name: "Flores, Ramon",
  age: 73,
  sex: "M",
  bed: "4B",
  admitting_context: "Heart failure with preserved EF, admitted 2h ago",
  reason: "Systolic BP missing",
  instruction: "Record a blood pressure",
  synthetic: true,
};

// The name links to the patient: a router around each render.
const html = (el: ReactElement) => renderToStaticMarkup(<MemoryRouter>{el}</MemoryRouter>);

describe("a NOT RANKED row", () => {
  it("states the reason and the instruction", () => {
    const out = html(<AbstainedRow row={row} />);
    expect(out).toContain("Systolic BP missing");
    expect(out).toContain("Record a blood pressure");
    expect(out).toContain('href="/patient/p-004"'); // the strip around it says NOT RANKED; the name opens the patient
  });

  it("offers no entry unless the build can re-rank (fixture mode is unchanged)", () => {
    const out = html(<AbstainedRow row={row} />);
    expect(out).not.toContain("Record vitals");
    expect(out).not.toContain("<button");
  });

  it("offers 'Record vitals', which opens the drawer, when it can", () => {
    const out = html(<AbstainedRow row={row} canRecord />);
    expect(out).toContain("Record vitals");
    expect(out).toContain('aria-haspopup="dialog"');
    expect(out).not.toContain("<form"); // one form, in the drawer: none under the row
  });

  it("disables Record vitals while offline", () => {
    expect(html(<AbstainedRow row={row} canRecord disabled />)).toMatch(/<button[^>]*disabled=""[^>]*>Record vitals/);
    expect(html(<AbstainedRow row={row} canRecord />)).not.toContain('disabled=""');
  });
});

describe("the last stored values of a NOT RANKED row", () => {
  const seen = (value: object, observed_at: string, source = "device") => ({
    data: { present: true, value },
    provenance: { source, tier: null, observed_at, computed_at: null, model_id: null, synthetic: true, quality: "ok" },
  });
  const gap = { data: { present: false, reason: "not_recorded" }, provenance: seen({}, "2026-09-03T05:00:00Z").provenance };
  const last_seen = {
    heart_rate: seen(88 as unknown as object, "2026-09-03T05:00:00Z"),
    respiratory_rate: gap,
    spo2: gap,
    temperature: gap,
    blood_pressure: seen({ systolic: 120, diastolic: 80 }, "2026-09-03T04:30:00Z", "nurse"),
    consciousness: gap,
    supplemental_o2: gap,
    spo2_scale: gap,
  } as unknown as AbstainedRowType["last_seen"];

  it("shows each value with its source and its age against the ward's time", () => {
    syncWardNow("2026-09-03T07:00:00Z");
    const out = html(<AbstainedRow row={{ ...row, last_seen }} />);
    expect(out).toContain("HR 88 (device, 2 h ago,");
    expect(out).toContain("BP 120/80 (nurse, 2 h ago,");
    expect(out).not.toContain("RR");
    forgetWardNow();
  });

  it("names a quality that is not ok, and says nothing for ok", () => {
    syncWardNow("2026-09-03T07:00:00Z");
    const weak = { ...last_seen!, heart_rate: { ...last_seen!.heart_rate, provenance: { ...last_seen!.heart_rate.provenance, quality: "degraded" } } };
    const out = html(<AbstainedRow row={{ ...row, last_seen: weak as AbstainedRowType["last_seen"] }} />);
    expect(out).toContain("HR 88 (device, degraded, 2 h ago,");
    expect(out).toContain("BP 120/80 (nurse, 2 h ago,");
    forgetWardNow();
  });

  it("gives the time alone before the ward's time is known", () => {
    forgetWardNow();
    const out = html(<AbstainedRow row={{ ...row, last_seen }} />);
    expect(out).toMatch(/HR 88 \(device, [^)]*\d\d:\d\d/); // no age yet: the date and clock stand alone, so an old value is not read as today's
    expect(out).not.toMatch(/HR 88 \(device, \d+ (h|min)/);
  });

  it("says no value is stored when none is, and shows nothing when the row has no last_seen", () => {
    const none = { ...last_seen, heart_rate: gap, blood_pressure: gap } as unknown as AbstainedRowType["last_seen"];
    expect(html(<AbstainedRow row={{ ...row, last_seen: none }} />)).toContain("no value stored");
    expect(html(<AbstainedRow row={row} />)).not.toContain("Last seen");
  });
});
