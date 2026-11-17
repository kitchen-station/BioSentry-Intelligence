// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ApiError } from "../api/client";
import type { AssistantMessage, LabRunDetail, LabRunItem } from "../types";
import { PatientHistoryArea } from "./PatientHistoryArea";

const api = vi.hoisted(() => ({ getAssistantHistory: vi.fn(), getPatientRuns: vi.fn(), getPatientRun: vi.fn() }));
vi.mock("../api", async () => ({ ...api, ApiError: (await import("../api/client")).ApiError }));
const signOut = vi.fn();
const run: LabRunItem = { id: 2, kind: "compare", requested_by: "md-santos", requested_at: "2026-11-17T00:00:00Z", mode: "w4", seed: 1, days: 3, baseline: "news2_total_ge_5", change: "news2_chart_trigger", state: "done", claimed_at: null, finished_at: null, reason: null };
const detail: LabRunDetail = { ...run, report: { synthetic: true }, report_text: "Synthetic result\nSecond line" };
const messages: AssistantMessage[] = [
  { id: 1, role: "user", text: "My synthetic question", lab_run_id: 1, created_at: run.requested_at },
  { id: 2, role: "assistant", text: "My synthetic answer", lab_run_id: 1, created_at: run.requested_at },
];

beforeEach(() => {
  vi.clearAllMocks();
  api.getAssistantHistory.mockResolvedValue([]);
  api.getPatientRuns.mockResolvedValue([]);
  api.getPatientRun.mockResolvedValue(detail);
});
afterEach(cleanup);
const page = () => render(<PatientHistoryArea patientId="p-001" onSignOut={signOut} />);

it("shows both empty states and explains privacy and simulated data", async () => {
  page();
  expect(screen.getByText("Loading history…")).toBeTruthy();
  expect(await screen.findByText("No assistant messages in the last 90 days.")).toBeTruthy();
  expect(screen.getByText("No simulated runs linked to this patient yet.")).toBeTruthy();
  expect(screen.getByText("Only your messages and answers, kept for 90 days.")).toBeTruthy();
  expect(api.getAssistantHistory).toHaveBeenCalledWith("p-001");
  expect(api.getPatientRuns).toHaveBeenCalledWith("p-001");
});

it("renders server order, plain message text, and a read-only simulated result", async () => {
  api.getAssistantHistory.mockResolvedValue(messages);
  api.getPatientRuns.mockResolvedValue([run, { ...run, id: 1 }]);
  page();
  expect(await screen.findByText("My synthetic answer")).toBeTruthy();
  expect(screen.getByText("You")).toBeTruthy();
  expect(screen.getByText("Assistant")).toBeTruthy();
  expect(screen.getAllByRole("button", { name: /View simulated run/ }).map((b) => b.textContent)).toEqual(["View simulated run 2", "View simulated run 1"]);
  fireEvent.click(screen.getByRole("button", { name: "View simulated run 2" }));
  expect(screen.getByText("Loading simulated result…")).toBeTruthy();
  expect(await screen.findByText("Synthetic result")).toBeTruthy();
  expect(screen.getByText("Second line")).toBeTruthy();
  expect(api.getPatientRun).toHaveBeenCalledWith("p-001", 2);
  expect(screen.queryByRole("textbox")).toBeNull();
  expect(screen.queryByRole("button", { name: /start|steer|send/i })).toBeNull();
  api.getAssistantHistory.mockRejectedValueOnce(new Error("revoked"));
  fireEvent.click(screen.getByRole("button", { name: "Refresh history" }));
  expect(screen.queryByText("My synthetic answer")).toBeNull();
  expect(screen.queryByText("Synthetic result")).toBeNull();
  expect(await screen.findByRole("alert")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Refresh history" }));
  expect(await screen.findByText("My synthetic answer")).toBeTruthy();
});

it.each([401, 404, 500])("handles history error %s without showing stale data", async (status) => {
  api.getPatientRuns.mockRejectedValueOnce(new ApiError(status, "failed", null));
  page();
  expect(await screen.findByRole("alert")).toBeTruthy();
  expect(signOut).toHaveBeenCalledTimes(status === 401 ? 1 : 0);
});

it.each([401, 404])("handles result error %s", async (status) => {
  api.getPatientRuns.mockResolvedValue([run]);
  api.getPatientRun.mockRejectedValueOnce(new ApiError(status, "failed", null));
  page();
  fireEvent.click(await screen.findByRole("button", { name: "View simulated run 2" }));
  expect(await screen.findByText("The simulated result could not be read.")).toBeTruthy();
  expect(signOut).toHaveBeenCalledTimes(status === 401 ? 1 : 0);
});

it("handles a missing result text", async () => {
  api.getPatientRuns.mockResolvedValue([run]);
  api.getPatientRun.mockResolvedValue({ ...detail, report_text: null });
  page();
  fireEvent.click(await screen.findByRole("button", { name: "View simulated run 2" }));
  expect(await screen.findByText("No result text stored.")).toBeTruthy();
});

it.each([false, true])("ignores history responses after leaving, rejected=%s", async (reject) => {
  let finish!: (value: unknown) => void;
  api.getAssistantHistory.mockReturnValueOnce(new Promise((resolve, refuse) => { finish = reject ? refuse : resolve; }));
  const view = page();
  view.unmount();
  await act(async () => finish(reject ? new ApiError(401, "expired", null) : []));
  expect(signOut).not.toHaveBeenCalled();
});

it.each([false, true])("ignores result responses after leaving, rejected=%s", async (reject) => {
  let finish!: (value: unknown) => void;
  api.getPatientRuns.mockResolvedValue([run]);
  api.getPatientRun.mockReturnValueOnce(new Promise((resolve, refuse) => { finish = reject ? refuse : resolve; }));
  const view = page();
  fireEvent.click(await screen.findByRole("button", { name: "View simulated run 2" }));
  await waitFor(() => expect(api.getPatientRun).toHaveBeenCalled());
  view.unmount();
  await act(async () => finish(reject ? new ApiError(401, "expired", null) : detail));
  expect(signOut).not.toHaveBeenCalled();
});
