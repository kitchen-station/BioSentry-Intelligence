import { useId, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { appendedCount } from "./chartPath";

const DRAW_MS = 700;

/**
 * Which bin the next draw starts after: -1 draws the whole line (first mount, or the series
 * was replaced); otherwise only the bins after `from` are new. `version` bumps per draw.
 */
export function useDrawFrom(keys: readonly unknown[]): { from: number; version: number } {
  const [s, setS] = useState({ keys, version: 0, from: -1 });
  const k = appendedCount(s.keys, keys);
  if (k === 0 || (keys.length === 0 && s.keys.length === 0)) return s; // empty twice: nothing to draw
  const next = { keys, version: s.version + 1, from: k === null ? -1 : keys.length - 1 - k };
  setS(next);
  return next;
}

interface Props {
  fromX: number;
  toX: number;
  height: number;
  version: number;
  children: ReactNode;
}

/** Reveals its children left to right from fromX to toX once per version: a cut, then still. */
export function DrawOn({ fromX, toX, height, version, children }: Props) {
  const id = `draw-${useId().replace(/[^\w-]/g, "")}`;
  const rect = useRef<SVGRectElement>(null);

  useLayoutEffect(() => {
    const el = rect.current;
    if (!el) return;
    const still = window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches;
    if (still || toX <= fromX) {
      el.setAttribute("width", String(toX));
      return;
    }
    const t0 = performance.now();
    let raf = 0;
    const step = (t: number) => {
      const p = Math.min(1, (t - t0) / DRAW_MS);
      el.setAttribute("width", String(fromX + (toX - fromX) * (1 - (1 - p) ** 3)));
      if (p < 1) raf = requestAnimationFrame(step);
    };
    el.setAttribute("width", String(fromX));
    raf = requestAnimationFrame(step);
    // Fail open: frames pause in hidden tabs, and a clinical line must never stay clipped away.
    const done = setTimeout(() => el.setAttribute("width", String(toX)), DRAW_MS + 50);
    return () => {
      cancelAnimationFrame(raf);
      clearTimeout(done);
      el.setAttribute("width", String(toX));
    };
  }, [version, fromX, toX]);

  return (
    <>
      <clipPath id={id}>
        <rect ref={rect} x={0} y={0} width={toX} height={height} />
      </clipPath>
      <g clipPath={`url(#${id})`}>{children}</g>
    </>
  );
}

/** Short vertical mark at a reading: the live end of a line, or a reading alone between gaps. */
export function Tick({ x, y, color, half = 4 }: { x: number; y: number; color: string; half?: number }) {
  return <line x1={x} y1={y - half} x2={x} y2={y + half} stroke={color} strokeWidth={1.5} strokeLinecap="round" />;
}
