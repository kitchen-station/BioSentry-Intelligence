// Dialogs, drawers, nav drawers and bottom sheets, all on native <dialog>.
// Open:  <button data-hw-open="discharge-dialog">…</button>
// Close: <button data-hw-close>…</button> inside the dialog, Esc, a click on the backdrop,
//        or (bottom sheets) a downward swipe on the sheet's header.
// The browser traps focus and returns focus to the opener; we only add the exit animation.
import { on, cleanupAll } from './core/dom.js';
import { leave, cancelLeave } from './core/motion.js';

const SWIPE_CLOSE_PX = 80;

export function openDialog(id) {
  const dialog = typeof id === 'string' ? document.getElementById(id) : id;
  if (!dialog) return null;
  cancelLeave(dialog);
  if (!dialog.open) dialog.showModal();
  return dialog;
}

/** Animate out, then close. Resolves once the dialog is closed (or reopened mid-exit). */
export async function closeDialog(dialog, value = '') {
  if (!dialog?.open) return;
  if (await leave(dialog)) dialog.close(value);
}

function swipeToClose(sheet, startEvent) {
  const startY = startEvent.clientY;
  let dy = 0;
  sheet.toggleAttribute('data-dragging', true);
  try { sheet.setPointerCapture(startEvent.pointerId); } catch { /* pointer already released */ }
  const move = (e) => {
    dy = Math.max(0, e.clientY - startY);
    sheet.style.transform = `translateY(${dy}px)`;
  };
  const end = () => {
    sheet.removeEventListener('pointermove', move);
    sheet.removeAttribute('data-dragging');
    sheet.style.transform = '';
    if (dy > SWIPE_CLOSE_PX) closeDialog(sheet);
  };
  sheet.addEventListener('pointermove', move);
  sheet.addEventListener('pointerup', end, { once: true });
  sheet.addEventListener('pointercancel', end, { once: true });
}

export function init() {
  return cleanupAll([
    on(document, 'click', (e) => {
      const opener = e.target.closest('[data-hw-open]');
      if (opener) {
        const dialog = openDialog(opener.dataset.hwOpen);
        opener.setAttribute('aria-expanded', 'true');
        dialog?.addEventListener('close', () => opener.setAttribute('aria-expanded', 'false'), { once: true });
        return;
      }
      const closer = e.target.closest('[data-hw-close]');
      if (closer) { closeDialog(closer.closest('dialog'), closer.value); return; }
      // Backdrop click: the event target is the <dialog> itself, outside its content box.
      if (e.target instanceof HTMLDialogElement && e.target.classList.contains('hw-dialog')) {
        const r = e.target.getBoundingClientRect();
        const inside = e.clientX >= r.left && e.clientX <= r.right && e.clientY >= r.top && e.clientY <= r.bottom;
        if (!inside) closeDialog(e.target);
      }
    }),
    // Esc fires "cancel" (it does not bubble, so listen in the capture phase). Animate instead of closing at once.
    // If the browser marks it non-cancelable (repeated Esc without user activation) it simply closes.
    on(document, 'cancel', (e) => {
      const dialog = e.target;
      if (!(dialog instanceof HTMLDialogElement) || !dialog.classList.contains('hw-dialog') || !e.cancelable) return;
      e.preventDefault();
      closeDialog(dialog);
    }, true),
    on(document, 'pointerdown', (e) => {
      const header = e.target.closest('.hw-dialog--sheet .hw-dialog__header');
      if (header && e.pointerType !== 'mouse' && !e.target.closest('button')) swipeToClose(header.closest('dialog'), e);
    }),
  ]);
}
