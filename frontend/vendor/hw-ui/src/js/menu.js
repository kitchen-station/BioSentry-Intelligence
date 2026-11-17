// Action menu (WAI-ARIA APG "Menu Button").
// Markup:
// <div class="hw-menu-anchor">
//   <button class="hw-button" aria-haspopup="menu" aria-expanded="false" data-hw-menu-trigger>Actions</button>
//   <div class="hw-menu" role="menu" hidden>
//     <button class="hw-menu__item" role="menuitem" data-action="ack">Acknowledge</button>
//   </div>
// </div>
// Emits: "hw:select" on the menu with detail { action, item }.
import { on, cleanupAll, uid, rovingIndex } from './core/dom.js';
import { leave, cancelLeave } from './core/motion.js';

const items = (menu) => [...menu.querySelectorAll('[role="menuitem"]:not([aria-disabled="true"])')];

function open(trigger, focusLast = false) {
  const menu = trigger.nextElementSibling;
  cancelLeave(menu);
  menu.hidden = false;
  trigger.setAttribute('aria-expanded', 'true');
  menu.querySelectorAll('[role="menuitem"]').forEach((i) => i.setAttribute('tabindex', '-1'));
  const list = items(menu);
  (focusLast ? list.at(-1) : list[0])?.focus();
}

function close(trigger, { focus = true } = {}) {
  const menu = trigger.nextElementSibling;
  if (trigger.getAttribute('aria-expanded') !== 'true') return;
  trigger.setAttribute('aria-expanded', 'false');
  if (focus) trigger.focus();
  leave(menu).then((done) => { if (done) menu.hidden = true; });
}

export function init(scope = document) {
  scope.querySelectorAll('[data-hw-menu-trigger]').forEach((t) => {
    const menu = t.nextElementSibling;
    menu.id ||= uid('hw-menu');
    t.setAttribute('aria-controls', menu.id);
  });

  return cleanupAll([
    on(document, 'click', (e) => {
      const trigger = e.target.closest('[data-hw-menu-trigger]');
      const item = e.target.closest('[role="menuitem"]');
      document.querySelectorAll('[data-hw-menu-trigger][aria-expanded="true"]').forEach((t) => {
        if (t !== trigger && !t.nextElementSibling.contains(e.target)) close(t, { focus: false });
      });
      if (trigger) {
        trigger.getAttribute('aria-expanded') === 'true' ? close(trigger) : open(trigger);
      } else if (item && item.getAttribute('aria-disabled') !== 'true') {
        const menu = item.closest('[role="menu"]');
        menu.dispatchEvent(new CustomEvent('hw:select', { bubbles: true, detail: { action: item.dataset.action, item } }));
        close(menu.previousElementSibling);
      }
    }),
    on(document, 'keydown', (e) => {
      const trigger = e.target.closest?.('[data-hw-menu-trigger]');
      if (trigger && (e.key === 'ArrowDown' || e.key === 'ArrowUp')) {
        e.preventDefault();
        open(trigger, e.key === 'ArrowUp');
        return;
      }
      const menu = e.target.closest?.('[role="menu"]');
      if (!menu) return;
      const list = items(menu);
      const index = list.indexOf(document.activeElement);
      if (e.key === 'Escape') { e.preventDefault(); close(menu.previousElementSibling); }
      else if (e.key === 'Tab') close(menu.previousElementSibling, { focus: false });
      else if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(e.key)) {
        e.preventDefault();
        list[rovingIndex(e.key, index, list.length)]?.focus();
      }
    }),
  ]);
}
