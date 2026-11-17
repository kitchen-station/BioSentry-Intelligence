// Tooltips from an attribute: <button data-hw-tooltip="Last reading 14:05">…</button>
// Shows on hover AND keyboard focus after a short delay; Esc hides it.
import { on, cleanupAll, uid } from './core/dom.js';

const DELAY = 300;
let tip;
let timer;

function show(target) {
  hide();
  tip = document.createElement('div');
  tip.className = 'hw-tooltip';
  tip.id = uid('hw-tooltip');
  tip.setAttribute('role', 'tooltip');
  tip.textContent = target.dataset.hwTooltip;
  document.body.append(tip);
  target.setAttribute('aria-describedby', tip.id);

  const r = target.getBoundingClientRect();
  const t = tip.getBoundingClientRect();
  const above = r.top - t.height - 8;
  const top = above >= 8 ? above : r.bottom + 8;
  const left = Math.min(Math.max(8, r.left + r.width / 2 - t.width / 2), window.innerWidth - t.width - 8);
  tip.style.top = `${top}px`;
  tip.style.left = `${left}px`;
}

function hide() {
  clearTimeout(timer);
  if (!tip) return;
  document.querySelector(`[aria-describedby="${tip.id}"]`)?.removeAttribute('aria-describedby');
  tip.remove();
  tip = null;
}

const schedule = (e) => {
  const target = e.target.closest?.('[data-hw-tooltip]');
  if (!target) return;
  clearTimeout(timer);
  timer = setTimeout(() => show(target), DELAY);
};
const cancel = (e) => { if (e.target.closest?.('[data-hw-tooltip]')) hide(); };

export function init() {
  return cleanupAll([
    on(document, 'pointerover', schedule),
    on(document, 'pointerout', cancel),
    on(document, 'focusin', schedule),
    on(document, 'focusout', cancel),
    on(document, 'keydown', (e) => { if (e.key === 'Escape') hide(); }),
    on(window, 'scroll', hide, { passive: true, capture: true }),
  ]);
}
