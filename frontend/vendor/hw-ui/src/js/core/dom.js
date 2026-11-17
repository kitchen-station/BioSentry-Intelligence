// Small shared helpers. No dependencies.
let counter = 0;
export const uid = (prefix = 'hw') => `${prefix}-${++counter}`;

/** Add a listener and return a function that removes it. */
export function on(target, type, handler, options) {
  target.addEventListener(type, handler, options);
  return () => target.removeEventListener(type, handler, options);
}

/** Run every cleanup function in a list. */
export const cleanupAll = (fns) => () => fns.forEach((fn) => fn());

/** Flip a popup above its trigger when there is not enough room below. */
export function placePopup(trigger, popup, gap = 4) {
  const rect = trigger.getBoundingClientRect();
  const below = window.innerHeight - rect.bottom;
  const needed = Math.min(popup.scrollHeight, 240) + gap;
  popup.dataset.placement = below < needed && rect.top > below ? 'top' : 'bottom';
}

/** Move focus among items with arrow keys / Home / End. Returns the newly active index. */
export function rovingIndex(key, index, length) {
  switch (key) {
    case 'ArrowDown': case 'ArrowRight': return (index + 1) % length;
    case 'ArrowUp': case 'ArrowLeft': return (index - 1 + length) % length;
    case 'Home': return 0;
    case 'End': return length - 1;
    default: return index;
  }
}
