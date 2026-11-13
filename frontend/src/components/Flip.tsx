import { useCallback, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import styles from "./Flip.module.css";

const FLIP_MS = 320; // the same as --flip-ms in index.css
const GAP_MS = 400; // one flip for an element in any 400 ms
const MOVE_MS = 420; // the same as --move-ms in index.css

const reduced = () => !!window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches;

/**
 * Flips when the text it shows changes after the first render. The DOM holds the new text at once;
 * the old text is only painted, by CSS, from a data attribute on an aria-hidden ghost.
 */
export function Flip({ children, still = false }: { children: ReactNode; still?: boolean }) {
  const inner = useRef<HTMLSpanElement>(null);
  const shown = useRef<string | null>(null);
  const lastFlip = useRef(-Infinity);
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined);
  const [old, setOld] = useState<string | null>(null);
  const end = useCallback(() => {
    clearTimeout(timer.current);
    setOld(null);
  }, []);

  // Compares the displayed text, not the objects: a poll that changed nothing flips nothing.
  useLayoutEffect(() => {
    const text = inner.current?.textContent ?? "";
    const before = shown.current;
    shown.current = text;
    if (before === null || before === text || still || reduced()) return;
    const at = Date.now();
    if (at - lastFlip.current < GAP_MS) return; // inside 400 ms: swap in place, no new flip
    lastFlip.current = at;
    setOld(before);
    timer.current = setTimeout(end, FLIP_MS); // in case animationend never comes
  });
  useEffect(() => () => clearTimeout(timer.current), []);

  return (
    <span className={styles.flipbox} onAnimationEnd={end}>
      {old !== null && <span className={styles["flip-old"]} data-old={old} aria-hidden="true" />}
      <span ref={inner} className={old !== null ? styles.flip : undefined}>{children}</span>
    </span>
  );
}

/**
 * Moves a row from its old place to its new one when the order of the keys changes.
 * Returns a ref setter for each key. Off under reduced motion.
 */
export function useMoveOnReorder(keys: readonly string[]): (key: string) => (el: HTMLElement | null) => void {
  const els = useRef(new Map<string, HTMLElement>());
  const tops = useRef(new Map<string, number>());
  const order = useRef<string | null>(null);
  const moving = useRef(new Map<string, ReturnType<typeof setTimeout>>());

  const settle = (key: string) => {
    clearTimeout(moving.current.get(key));
    moving.current.delete(key);
    const el = els.current.get(key);
    if (el) {
      el.style.transition = "";
      el.style.transform = "";
    }
  };

  useLayoutEffect(() => {
    const same = order.current === JSON.stringify(keys);
    order.current = JSON.stringify(keys);
    const next = new Map<string, number>();
    for (const key of keys) {
      const el = els.current.get(key);
      if (!el) continue;
      if (moving.current.has(key)) {
        if (same) {
          next.set(key, tops.current.get(key) as number); // mid-move: the rect includes the transform
          continue;
        }
        settle(key);
      }
      const top = el.getBoundingClientRect().top + window.scrollY;
      next.set(key, top);
      const from = tops.current.get(key);
      if (same || from === undefined || from === top || reduced()) continue;
      el.style.transition = "none";
      el.style.transform = `translateY(${from - top}px)`;
      requestAnimationFrame(() => {
        el.style.transition = "transform var(--move-ms) var(--flip-ease)";
        el.style.transform = "";
      });
      // Clears the inline style even if no frame comes (a hidden tab).
      moving.current.set(key, setTimeout(() => settle(key), MOVE_MS + 50));
    }
    tops.current = next;
  });
  useEffect(() => {
    const running = moving.current;
    return () => running.forEach(clearTimeout);
  }, []);

  return (key) => (el) => {
    if (el) els.current.set(key, el);
    else els.current.delete(key);
  };
}
