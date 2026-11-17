// Exit animations. State (aria-expanded, focus, events) changes at once; only the visual removal waits.
// CSS owns the look: each component styles its own [data-hw-leaving] animation.

const MAX_MS = 1000; // never leave something stuck on screen if an animation never ends
const pending = new WeakMap();

export const reducedMotion = () => window.matchMedia('(prefers-reduced-motion: reduce)').matches;

const finite = (a) => a.effect?.getComputedTiming().endTime !== Infinity;

/**
 * Mark an element as leaving, wait for its exit animation, then resolve.
 * Resolves true when the exit finished, false when cancelLeave() interrupted it
 * (for example the user reopened a menu mid-close), so callers only hide on true.
 */
export function leave(el) {
  if (!el) return Promise.resolve(true);
  if (pending.has(el)) return pending.get(el);
  if (reducedMotion()) return Promise.resolve(true);

  el.setAttribute('data-hw-leaving', '');
  const animations = el.getAnimations({ subtree: true }).filter(finite);
  const timeout = new Promise((resolve) => { setTimeout(resolve, MAX_MS); });
  const done = Promise.race([Promise.allSettled(animations.map((a) => a.finished)), timeout]).then(() => {
    pending.delete(el);
    const finished = el.hasAttribute('data-hw-leaving');
    el.removeAttribute('data-hw-leaving');
    return finished;
  });
  pending.set(el, done);
  return done;
}

/** Stop an exit in progress; the pending leave() then resolves false. */
export function cancelLeave(el) {
  el?.removeAttribute('data-hw-leaving');
}
