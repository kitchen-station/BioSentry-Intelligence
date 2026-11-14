// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, renderHook } from "@testing-library/react";
import { forgetWardNow, hasWardNow, syncWardNow, useWardNow } from "./wardNow";

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

  it("is 0 before any read, and the server's time plus what has passed after one", () => {
    expect(hasWardNow()).toBe(false);
    const { result } = renderHook(() => useWardNow());
    expect(result.current).toBe(0);
    syncWardNow(SERVED);
    expect(hasWardNow()).toBe(true);
    act(() => void vi.advanceTimersByTime(5000));
    expect(result.current).toBe(Date.parse(SERVED) + 5000);
  });

  it("never runs backward when the server serves the same time again (a seeded ward)", () => {
    syncWardNow(SERVED);
    const { result } = renderHook(() => useWardNow());
    act(() => void vi.advanceTimersByTime(5000));
    syncWardNow(SERVED);
    act(() => void vi.advanceTimersByTime(1000));
    expect(result.current).toBe(Date.parse(SERVED) + 6000);
  });

  it("moves to a newer served time", () => {
    syncWardNow(SERVED);
    const { result } = renderHook(() => useWardNow());
    act(() => void vi.advanceTimersByTime(5000));
    syncWardNow("2026-09-03T07:10:00Z");
    act(() => void vi.advanceTimersByTime(1000));
    expect(result.current).toBe(Date.parse("2026-09-03T07:10:00Z") + 1000);
  });
});
