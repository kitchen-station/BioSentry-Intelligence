// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { LabRunDetail, LabRunItem } from "../types";
import { LabRuns as Runs } from "./LabRuns";

const signOut = vi.fn();
const LabRuns = ({ role }: { role: "md" | "rn" | "charge_rn" | "admin" }) => <Runs role={role} onSignOut={signOut} />;

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const api = vi.hoisted(() => {
  class MockApiError extends Error {
    status: number;
    constructor(status: number) {
      super(String(status));
      this.status = status;
    }
  }
  return { getLabRuns: vi.fn(), getLabRun: vi.fn(), requestLabRun: vi.fn(), hasLabRuns: true, ApiError: MockApiError };
});
const MockApiError = api.ApiError;
vi.mock("../api", () => api);

const item = (over: Partial<LabRunItem>): LabRunItem => ({
  id: 1, kind: "compare", requested_by: "md-santos", requested_at: "2026-11-13T09:30:00Z", mode: "w4", seed: 4, days: 30,
  baseline: "news2_total_ge_5", change: "news2_chart_trigger", state: "queued", claimed_at: null, finished_at: null, reason: null,
  ...over,
});
const RUNS = [
  item({ id: 1 }),
  item({ id: 2, state: "running", claimed_at: "2026-11-13T09:31:00Z" }),
  item({ id: 3, state: "done", mode: "hospital", seed: 101, days: 7, baseline: "typical", change: "staffing=doh-standard" }),
  item({ id: 4, state: "failed", reason: "no worker claimed it within an hour" }),
  item({ id: 5, state: "refused", reason: "compare: beds is not a group" }),
];

beforeEach(() => {
  api.getLabRuns.mockResolvedValue(RUNS);
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  api.hasLabRuns = true;
});

const options = (name: string) => within(screen.getByLabelText(name)).getAllByRole("option").map((o) => o.textContent);

describe("simulated runs: who sees what", () => {
  it("says the server is needed in the fixture build and calls nothing", () => {
    api.hasLabRuns = false;
    render(<LabRuns role="md" />);
    expect(screen.getByText("Simulated runs need the server.")).toBeTruthy();
    expect(screen.queryByRole("button")).toBeNull();
    expect(api.getLabRuns).not.toHaveBeenCalled();
  });

  it("gives an admin one line and calls nothing", () => {
    render(<LabRuns role="admin" />);
    expect(screen.getByText("Simulated runs are for the ward's clinicians.")).toBeTruthy();
    expect(api.getLabRuns).not.toHaveBeenCalled();
  });

  it("shows a nurse the list and no form", async () => {
    render(<LabRuns role="rn" />);
    expect(await screen.findByText("Waiting for the lab worker")).toBeTruthy();
    expect(screen.getByText("A physician or the charge nurse can ask for a simulated run.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Ask for a simulated run" })).toBeNull();
  });

  it.each(["md", "charge_rn"] as const)("shows %s the form and the honest worker line", async (role) => {
    render(<LabRuns role={role} />);
    expect(await screen.findByRole("button", { name: "Ask for a simulated run" })).toBeTruthy();
    expect(screen.getByText("A lab worker beside the ward runs each request. If none is running, a request fails after an hour.")).toBeTruthy();
    expect(screen.getAllByRole("combobox")).toHaveLength(4);
    expect(screen.queryAllByRole("textbox")).toHaveLength(0);
  });
});

describe("simulated runs: the list", () => {
  it("writes each state in words and the request in the simulator's names", async () => {
    render(<LabRuns role="rn" />);
    expect(await screen.findByText("W4, seed 4, 30 days: news2_total_ge_5 against news2_chart_trigger", { selector: "tr:nth-child(1) td" })).toBeTruthy();
    expect(screen.getByText("Hospital, seed 101, 7 days: typical, change staffing=doh-standard")).toBeTruthy();
    expect(screen.getByText("Running since 09:31 UTC")).toBeTruthy();
    expect(screen.getByText("Failed: no worker claimed it within an hour")).toBeTruthy();
    expect(screen.getByText("Refused by the simulator: compare: beds is not a group")).toBeTruthy();
    expect(screen.getAllByText("2026-11-13 09:30 UTC")).toHaveLength(5);
    expect(screen.getAllByRole("button", { name: "Show result" })).toHaveLength(1);
  });

  it("says when there are none", async () => {
    api.getLabRuns.mockResolvedValue([]);
    render(<LabRuns role="rn" />);
    expect(await screen.findByText("No simulated runs yet.")).toBeTruthy();
  });

  it("reads once on load and once for each Refresh click", async () => {
    render(<LabRuns role="rn" />);
    await screen.findByText("Done");
    expect(api.getLabRuns).toHaveBeenCalledTimes(1);
    await userEvent.click(screen.getByRole("button", { name: "Refresh" }));
    expect(api.getLabRuns).toHaveBeenCalledTimes(2);
  });

  it("says so when the list cannot be read, and for a 403", async () => {
    api.getLabRuns.mockRejectedValueOnce(new Error("x")).mockRejectedValueOnce(new MockApiError(403));
    render(<LabRuns role="rn" />);
    expect((await screen.findByRole("alert")).textContent).toBe("The runs could not be read. Try Refresh.");
    await userEvent.click(screen.getByRole("button", { name: "Refresh" }));
    expect((await screen.findByText("Not available for this account.")).getAttribute("role")).toBe("alert");
  });
});

describe("simulated runs: a failed or odd read", () => {
  it("leaves no old table after a failed Refresh", async () => {
    render(<LabRuns role="rn" />);
    await screen.findByText("Done");
    api.getLabRuns.mockRejectedValueOnce(new Error("x"));
    await userEvent.click(screen.getByRole("button", { name: "Refresh" }));
    await screen.findByRole("alert");
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("signs out on a 401 when the list is read", async () => {
    api.getLabRuns.mockRejectedValue(new MockApiError(401));
    render(<LabRuns role="rn" />);
    expect((await screen.findByRole("alert")).textContent).toBe("Your session has ended. Sign in again.");
    expect(signOut).toHaveBeenCalledTimes(1);
  });

  it.each([["a string", "oops"], ["an object", { a: 1 }]])("treats a list body that is %s as a failed read", async (_n, body) => {
    api.getLabRuns.mockResolvedValue(body);
    render(<LabRuns role="rn" />);
    expect((await screen.findByRole("alert")).textContent).toBe("The runs could not be read. Try Refresh.");
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("shows odd items without breaking the page", async () => {
    api.getLabRuns.mockResolvedValue([
      item({ id: 7, requested_at: 12345 as unknown as string }),
      item({ id: 8, state: "paused" as LabRunItem["state"] }),
      item({ id: 9, state: "running", claimed_at: null }),
      item({ id: 10, state: "failed" }),
      item({ id: 11, state: "refused" }),
    ]);
    render(<LabRuns role="rn" />);
    expect(await screen.findByText("12345 UTC")).toBeTruthy();
    expect(screen.getByText("paused")).toBeTruthy();
    expect(screen.getByText("Running")).toBeTruthy();
    expect(screen.getByText("Failed: no reason given")).toBeTruthy();
    expect(screen.getByText("Refused by the simulator: no reason given")).toBeTruthy();
  });
});

describe("simulated runs: the form", () => {
  it("offers the fixed lists for each mode", async () => {
    render(<LabRuns role="md" />);
    await screen.findByText("Done");
    expect(options("Mode")).toEqual(["Ward W4: two warning rules", "Hospital: one group of settings"]);
    expect(options("Days")).toEqual(["1", "3", "7", "14", "30"]);
    expect(options("Baseline")).toHaveLength(21);
    expect(options("Baseline")[0]).toBe("news2_total_ge_1");
    expect(options("Change")).toHaveLength(20);
    expect(options("Change")).not.toContain("news2_total_ge_1");
    await userEvent.selectOptions(screen.getByLabelText("Baseline"), "news2_total_ge_7");
    expect(options("Change")).toContain("news2_total_ge_1");
    expect(options("Change")).not.toContain("news2_total_ge_7");
    await userEvent.selectOptions(screen.getByLabelText("Mode"), "hospital");
    expect(options("Baseline")).toEqual(["typical", "surge", "doh-standard"]);
    expect(options("Change")).toEqual(["staffing=doh-standard", "nurses=doh-standard", "doctors=doh-standard"]);
    await userEvent.selectOptions(screen.getByLabelText("Baseline"), "doh-standard");
    expect(options("Change")).toEqual(["staffing=typical", "nurses=typical", "doctors=typical"]);
  });

  it("sends the choice as a compare request and reads the list again", async () => {
    api.requestLabRun.mockResolvedValue(item({}));
    render(<LabRuns role="charge_rn" />);
    await screen.findByText("Done");
    await userEvent.selectOptions(screen.getByLabelText("Mode"), "hospital");
    await userEvent.selectOptions(screen.getByLabelText("Change"), "nurses=doh-standard");
    await userEvent.selectOptions(screen.getByLabelText("Days"), "7");
    await userEvent.clear(screen.getByLabelText(/Seed/));
    await userEvent.type(screen.getByLabelText(/Seed/), "101");
    await userEvent.click(screen.getByRole("button", { name: "Ask for a simulated run" }));
    expect(api.requestLabRun).toHaveBeenCalledWith({
      kind: "compare", mode: "hospital", seed: 101, days: 7, baseline: "typical", change: "nurses=doh-standard",
    });
    await vi.waitFor(() => expect(api.getLabRuns).toHaveBeenCalledTimes(2));
  });

  it("reads again exactly once after a request", async () => {
    api.requestLabRun.mockResolvedValue(item({}));
    render(<LabRuns role="md" />);
    await screen.findByText("Done");
    await userEvent.click(screen.getByRole("button", { name: "Ask for a simulated run" }));
    await act(async () => {
      await new Promise((r) => setTimeout(r, 50));
    });
    expect(api.getLabRuns).toHaveBeenCalledTimes(2);
  });

  it("sends one request for a double click", async () => {
    api.requestLabRun.mockReturnValue(new Promise(() => {}));
    render(<LabRuns role="md" />);
    await screen.findByText("Done");
    await userEvent.dblClick(screen.getByRole("button", { name: "Ask for a simulated run" }));
    expect(api.requestLabRun).toHaveBeenCalledTimes(1);
  });

  it("will not send a seed that is empty, negative, fractional or too large", async () => {
    render(<LabRuns role="md" />);
    await screen.findByText("Done");
    const button = screen.getByRole("button", { name: "Ask for a simulated run" }) as HTMLButtonElement;
    const seed = screen.getByLabelText(/Seed/);
    const hint = screen.getByText("A whole number from 0 to 2147483647.");
    expect(seed.getAttribute("aria-describedby")).toBe(hint.id);
    expect(hint.closest("label")).toBeNull();
    expect(hint.className).toBe("");
    for (const bad of ["", "-1", "1.5", "2147483648"]) {
      await userEvent.clear(seed);
      if (bad) await userEvent.type(seed, bad);
      expect(button.disabled, bad).toBe(true);
      expect(hint.className, bad).not.toBe("");
    }
    await userEvent.clear(seed);
    await userEvent.type(seed, "2147483647");
    expect(button.disabled).toBe(false);
    expect(hint.className).toBe("");
  });

  it.each([
    [429, "A limit is reached: one open request for each clinician, 10 in 24 hours, 5 waiting on the ward. Try again later."],
    [403, "Not available for this account."],
    [500, "The request was not sent. Try again."],
  ])("explains a %s", async (status, text) => {
    api.requestLabRun.mockRejectedValue(new MockApiError(status));
    render(<LabRuns role="md" />);
    await screen.findByText("Done");
    await userEvent.click(screen.getByRole("button", { name: "Ask for a simulated run" }));
    expect((await screen.findByRole("alert")).textContent).toBe(text);
  });

  it("explains a failure that is not an API error", async () => {
    api.requestLabRun.mockRejectedValue(new Error("offline"));
    render(<LabRuns role="md" />);
    await screen.findByText("Done");
    await userEvent.click(screen.getByRole("button", { name: "Ask for a simulated run" }));
    expect((await screen.findByRole("alert")).textContent).toBe("The request was not sent. Try again.");
  });

  it("signs out on a 401 when the request fails", async () => {
    api.requestLabRun.mockRejectedValue(new MockApiError(401));
    render(<LabRuns role="md" />);
    await screen.findByText("Done");
    await userEvent.click(screen.getByRole("button", { name: "Ask for a simulated run" }));
    expect((await screen.findByRole("alert")).textContent).toBe("Your session has ended. Sign in again.");
    expect(signOut).toHaveBeenCalledTimes(1);
  });
});

const detail = (over: Partial<LabRunDetail>): LabRunDetail => ({
  ...item({ id: 3, state: "done" }), report: null, report_text: null, ...over,
});

describe("simulated runs: the result", () => {
  const show = async () => {
    render(<LabRuns role="rn" />);
    await userEvent.click(await screen.findByRole("button", { name: "Show result" }));
  };

  it("shows the header, compare.txt as stored and the whole stored json, keys it does not know included", async () => {
    const report = {
      mode: "hospital", seed: 101, days: 7, baseline: { name: "typical", beds: 20 },
      change: { kind: "group", name: "staffing", fields: [{ path: "staffing", baseline: 1, change: { a: 2 } }, { path: "x" }] },
      pairing: { status: "one cohort for both arms", same_person_at_each_arrival: true },
      paired_episodes: { median: 3 },
    };
    api.getLabRun.mockResolvedValue(detail({ report, report_text: "line one\n  line two\nsynthetic, not validated\n" }));
    await show();
    expect(api.getLabRun).toHaveBeenCalledWith(3);
    expect(screen.getByText("Synthetic, not validated. One seed is one run: it shows how the comparison works. Use several seeds before reading anything into a difference.")).toBeTruthy();
    const dl = within(screen.getByText("compare.json, as stored").closest("div")!.querySelector("dl")!);
    expect(dl.getAllByRole("term").map((t) => t.textContent)).toEqual([
      "mode", "seed", "days", "baseline", "change", "pairing.status", "same_person_at_each_arrival",
    ]);
    expect(dl.getByText("typical")).toBeTruthy();
    expect(dl.getByText("group staffing")).toBeTruthy();
    expect(dl.getByText("staffing: 1 -> {\"a\":2}")).toBeTruthy();
    expect(dl.getByText("x: - -> -")).toBeTruthy();
    expect(dl.getByText("true")).toBeTruthy();
    const txt = screen.getByRole("region", { name: "compare.txt" });
    expect(txt.tagName).toBe("PRE");
    expect(txt.textContent).toBe("line one\n  line two\nsynthetic, not validated\n");
    expect(txt.getAttribute("tabindex")).toBe("0");
    expect(screen.queryByRole("region", { name: "compare.json", hidden: true })).toBeNull();
    expect(document.body.textContent).not.toContain("paired_episodes");
    const details = document.querySelector("details")!;
    details.open = true;
    fireEvent(details, new Event("toggle"));
    const json = screen.getByRole("region", { name: "compare.json", hidden: true });
    expect(json.textContent).toBe(JSON.stringify(report, null, 2));
    expect(json.textContent).toContain("paired_episodes");
  });

  it("takes a W4 string baseline and shows a dash for every missing key", async () => {
    api.getLabRun.mockResolvedValue(detail({ report: { baseline: "news2_total_ge_5", change: {} }, report_text: "t" }));
    await show();
    expect(screen.getByText("news2_total_ge_5")).toBeTruthy();
    expect(screen.getByText("- -")).toBeTruthy();
    expect(screen.getAllByText("-")).toHaveLength(5);
  });

  it("shows dashes when the stored report is missing", async () => {
    api.getLabRun.mockResolvedValue(detail({}));
    await show();
    expect(screen.getAllByText("-")).toHaveLength(7);
    expect(screen.queryByText("compare.json, as stored")).toBeNull();
    expect(screen.queryByRole("region", { name: "compare.txt" })).toBeNull();
  });

  it.each([[404], [0]])("says so when the result cannot be read (%s)", async (status) => {
    api.getLabRun.mockRejectedValue(status ? new MockApiError(status) : new Error("x"));
    await show();
    expect((await screen.findByRole("alert")).textContent).toBe("The result could not be read. Try again.");
  });

  it("signs out on a 401 when the result is read", async () => {
    api.getLabRun.mockRejectedValue(new MockApiError(401));
    await show();
    expect((await screen.findByRole("alert")).textContent).toBe("Your session has ended. Sign in again.");
    expect(signOut).toHaveBeenCalledTimes(1);
  });

  it("moves focus to the result heading", async () => {
    api.getLabRun.mockResolvedValue(detail({ report_text: "t" }));
    await show();
    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole("heading", { name: "Result of run 3" })));
  });

  it("clears the old result when the next one fails", async () => {
    api.getLabRun.mockResolvedValueOnce(detail({ report_text: "first" })).mockRejectedValueOnce(new Error("x"));
    await show();
    await screen.findByText("first");
    await userEvent.click(screen.getByRole("button", { name: "Show result" }));
    await screen.findByRole("alert");
    expect(screen.queryByText("first")).toBeNull();
    expect(screen.queryByRole("heading", { name: "Result of run 3" })).toBeNull();
  });

  it("uses report_text only when it is a string", async () => {
    api.getLabRun.mockResolvedValue(detail({ report: "odd" as unknown as null, report_text: { a: 1 } as unknown as string }));
    await show();
    expect(await screen.findByRole("heading", { name: "Result of run 3" })).toBeTruthy();
    expect(screen.queryByRole("region", { name: "compare.txt" })).toBeNull();
    expect(screen.getAllByText("-")).toHaveLength(7);
  });
});
