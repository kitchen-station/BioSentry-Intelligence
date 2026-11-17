/**
 * Pure geometry for the ward sparklines.
 *
 * A null value is a gap (I1: missing is missing): the line breaks there, nothing is
 * bridged or interpolated, and no point is drawn for it. A stretch of one point between
 * gaps is drawn as a dot, never as a line.
 */

export interface Pt {
  readonly x: number;
  readonly y: number;
}

/** Runs of consecutive non-null points, in order. A null ends the current run. */
export function runs(points: readonly (Pt | null)[]): Pt[][] {
  const out: Pt[][] = [];
  let current: Pt[] = [];
  for (const p of points) {
    if (p === null) {
      if (current.length > 0) out.push(current);
      current = [];
    } else {
      current.push(p);
    }
  }
  if (current.length > 0) out.push(current);
  return out;
}

const pair = (p: Pt) => `${p.x},${p.y}`;

/** "M x,y L x,y ..." for each run of two or more points; "" when there is no line to draw. */
export function linePath(points: readonly (Pt | null)[]): string {
  return runs(points)
    .filter((r) => r.length >= 2)
    .map((r) => `M${r.map(pair).join("L")}`)
    .join("");
}

/** Closed area under each run of two or more points, down to `baseline`. */
export function areaPath(points: readonly (Pt | null)[], baseline: number): string {
  return runs(points)
    .filter((r) => r.length >= 2)
    .map((r) => {
      const first = r[0];
      const last = r[r.length - 1];
      return `M${r.map(pair).join("L")}L${last.x},${baseline}L${first.x},${baseline}Z`;
    })
    .join("");
}

/** Points that stand alone between gaps (or alone in the series). */
export function isolatedPoints(points: readonly (Pt | null)[]): Pt[] {
  return runs(points)
    .filter((r) => r.length === 1)
    .map((r) => r[0]);
}

/** x for index i of n evenly spaced bins across [left, left + width]. */
export function xByIndex(i: number, n: number, left: number, width: number): number {
  return n <= 1 ? left : left + (i / (n - 1)) * width;
}

/** The non-null values, in order. */
export function present(values: readonly (number | null)[]): number[] {
  return values.filter((v): v is number => v !== null);
}

/**
 * How many bins `next` adds at its end compared with `prev`, allowing the window to slide
 * (oldest bins dropped). 0 = nothing new; null = not an extension, so redraw it all.
 */
export function appendedCount<T>(prev: readonly T[], next: readonly T[]): number | null {
  for (let k = 0; k < next.length; k++) {
    const keep = next.length - k;
    if (keep > prev.length) continue;
    const offset = prev.length - keep;
    let same = true;
    for (let i = 0; i < keep && same; i++) same = next[i] === prev[offset + i];
    if (same) return k;
  }
  return null;
}
