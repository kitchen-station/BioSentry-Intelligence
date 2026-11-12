// @vitest-environment jsdom
/** The fetch behind the panel: once per patient, never throws, ignores an answer for the wrong or a previous patient. */

import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { BaselineResponse } from "../types";
import { useBaseline } from "./useBaseline";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const api = vi.hoisted(() => ({ getBaseline: vi.fn() }));
vi.mock("../api", () => api);

const found = (patient_id: string) => ({ patient_id }) as BaselineResponse;

afterEach(() => {
  vi.useRealTimers();
  cleanup();
  vi.clearAllMocks();
});

describe("useBaseline", () => {
  it("is loading, then the finding, fetched once for the id", async () => {
    api.getBaseline.mockResolvedValue(found("p-1"));
    const { result } = renderHook(() => useBaseline("p-1"));
    expect(result.current).toBeUndefined();
    await waitFor(() => expect(result.current).toEqual(found("p-1")));
    expect(api.getBaseline).toHaveBeenCalledOnce();
    expect(api.getBaseline).toHaveBeenCalledWith("p-1");
  });

  it("passes fixture mode's null through", async () => {
    api.getBaseline.mockResolvedValue(null);
    const { result } = renderHook(() => useBaseline("p-1"));
    await waitFor(() => expect(result.current).toBeNull());
  });

  it("is failed when the call fails", async () => {
    api.getBaseline.mockRejectedValue(new Error("404"));
    const { result } = renderHook(() => useBaseline("p-1"));
    await waitFor(() => expect(result.current).toBe("failed"));
  });

  it("is failed, not shown, when the answer is about another patient", async () => {
    api.getBaseline.mockResolvedValue(found("p-2"));
    const { result } = renderHook(() => useBaseline("p-1"));
    await waitFor(() => expect(result.current).toBe("failed"));
  });

  it("is failed when the answer is not an object at all", async () => {
    api.getBaseline.mockResolvedValue(undefined);
    const { result } = renderHook(() => useBaseline("p-1"));
    await waitFor(() => expect(result.current).toBe("failed"));
  });

  it("asks for nothing without an id", () => {
    const { result } = renderHook(() => useBaseline(undefined));
    expect(result.current).toBeUndefined();
    expect(api.getBaseline).not.toHaveBeenCalled();
  });

  it("resets on a new id and ignores a late answer for the old one", async () => {
    let late: (b: BaselineResponse) => void = () => undefined;
    api.getBaseline.mockImplementationOnce(() => new Promise((r) => (late = r)));
    api.getBaseline.mockResolvedValueOnce(found("p-2"));
    const { result, rerender } = renderHook(({ id }) => useBaseline(id), { initialProps: { id: "p-1" } });
    rerender({ id: "p-2" });
    expect(result.current).toBeUndefined(); // nothing of p-1 is shown for p-2
    await waitFor(() => expect(result.current).toEqual(found("p-2")));
    await act(async () => late(found("p-1")));
    expect(result.current).toEqual(found("p-2"));
  });

  it("ignores a late failure for the old id too", async () => {
    let fail: (e: Error) => void = () => undefined;
    api.getBaseline.mockImplementationOnce(() => new Promise((_, r) => (fail = r)));
    api.getBaseline.mockResolvedValueOnce(found("p-2"));
    const { result, rerender } = renderHook(({ id }) => useBaseline(id), { initialProps: { id: "p-1" } });
    rerender({ id: "p-2" });
    await waitFor(() => expect(result.current).toEqual(found("p-2")));
    await act(async () => fail(new Error("late")));
    expect(result.current).toEqual(found("p-2"));
  });
});

describe("useBaseline refresh", () => {
  const minute = (ms = 60_000) => act(async () => { await vi.advanceTimersByTimeAsync(ms); });

  it("asks again every 60 seconds, and a newer answer replaces the older one", async () => {
    vi.useFakeTimers();
    api.getBaseline.mockResolvedValueOnce({ ...found("p-1"), label: "first" });
    api.getBaseline.mockResolvedValueOnce({ ...found("p-1"), label: "second" });
    const { result } = renderHook(() => useBaseline("p-1"));
    await minute(1);
    expect(api.getBaseline).toHaveBeenCalledTimes(1);
    expect((result.current as BaselineResponse).label).toBe("first");
    await minute(59_000);
    expect(api.getBaseline).toHaveBeenCalledTimes(1); // not yet
    await minute(1_000);
    expect(api.getBaseline).toHaveBeenCalledTimes(2);
    expect((result.current as BaselineResponse).label).toBe("second");
  });

  it("stops asking when the page goes away or moves to another patient", async () => {
    vi.useFakeTimers();
    api.getBaseline.mockResolvedValue(found("p-1"));
    const { rerender, unmount } = renderHook(({ id }) => useBaseline(id), { initialProps: { id: "p-1" } });
    await minute();
    expect(api.getBaseline).toHaveBeenCalledTimes(2);
    api.getBaseline.mockResolvedValue(found("p-2"));
    rerender({ id: "p-2" });
    await minute(1);
    expect(api.getBaseline).toHaveBeenLastCalledWith("p-2");
    api.getBaseline.mockClear();
    await minute();
    expect(api.getBaseline).toHaveBeenCalledTimes(1); // one timer, for p-2 only
    unmount();
    api.getBaseline.mockClear();
    await minute(180_000);
    expect(api.getBaseline).not.toHaveBeenCalled();
  });

  it("shows the error line's state, not the old finding, when a refetch fails", async () => {
    vi.useFakeTimers();
    api.getBaseline.mockResolvedValueOnce(found("p-1"));
    api.getBaseline.mockRejectedValueOnce(new Error("offline"));
    const { result } = renderHook(() => useBaseline("p-1"));
    await minute(1);
    expect(result.current).toEqual(found("p-1"));
    await minute();
    expect(result.current).toBe("failed");
  });
});
