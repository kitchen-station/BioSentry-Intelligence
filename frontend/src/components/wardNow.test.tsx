// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, renderHook } from "@testing-library/react";
import { forgetWardNow, since, syncWardNow, useWardNow, wardNowMs } from "./wardNow";

const SERVED = "2026-09-03T07:00:00Z";

describe("ward time", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2030-01-01T12:00:00Z"));
    forgetWardNow();
  });
  afterEach(() => {
    forgetWardNow();
    vi.useRealTimers();
  });

  it("is 0 before any read, and the server's time after one", () => {
    const { result } = renderHook(() => useWardNow());
    expect(result.current).toBe(0);
    syncWardNow(SERVED);
    expect(wardNowMs()).toBe(Date.parse(SERVED));
  });

  it("does not move by the browser's seconds: only a read moves it", () => {
    syncWardNow(SERVED);
    act(() => void vi.advanceTimersByTime(60_000));
    expect(wardNowMs()).toBe(Date.parse(SERVED)); // a paused ward, or a fast one, is not the browser's pace
    syncWardNow("2026-09-03T07:10:00Z");
    expect(wardNowMs()).toBe(Date.parse("2026-09-03T07:10:00Z"));
  });

  it("steps with the browser's seconds in the demo with no server, where the ward's time is the browser's clock", () => {
    syncWardNow(SERVED, true);
    const { result } = renderHook(() => useWardNow());
    act(() => void vi.advanceTimersByTime(5000));
    expect(result.current).toBe(Date.parse(SERVED) + 5000);
    syncWardNow(SERVED);
    act(() => void vi.advanceTimersByTime(5000));
    expect(wardNowMs()).toBe(Date.parse(SERVED)); // a server's read ends it
  });

  it("prints the ward's time for a failure, and the browser's only before any read", () => {
    const marker = Date.parse("2030-01-01T12:00:00Z");
    expect(since(marker)).toBe(marker);
    syncWardNow(SERVED);
    expect(since(marker)).toBe(Date.parse(SERVED));
  });
});
