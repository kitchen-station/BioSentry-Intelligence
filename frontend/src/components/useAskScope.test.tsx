// @vitest-environment jsdom
import { renderHook } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { useAskScope } from "./useAskScope";

const scopeAt = (path: string) => renderHook(() => useAskScope(), { wrapper: ({ children }) => <MemoryRouter initialEntries={[path]}>{children}</MemoryRouter> }).result.current;

describe("useAskScope", () => {
  it.each([
    ["/patient/p-1", { kind: "patient", id: "p-1" }],
    ["/patient/p-1/", { kind: "patient", id: "p-1" }],
    ["/patient/p%20001/", { kind: "patient", id: "p 001" }],
    ["/ask/patient/p-1", { kind: "patient", id: "p-1" }],
    ["/ward", { kind: "ward" }],
    ["/what-if", { kind: "ward" }],
    ["/ask", { kind: "ward" }],
    ["/", { kind: "none" }],
  ])("%s", (path, scope) => expect(scopeAt(path)).toEqual(scope));
});
