// Single-select listbox (WAI-ARIA APG "Select-Only Combobox").
// Markup:
// <div class="hw-select" data-hw-select>
//   <input type="hidden" name="ward" value="w4">
//   <button type="button" class="hw-select__trigger" aria-haspopup="listbox" aria-expanded="false">Ward W4</button>
//   <ul class="hw-listbox" role="listbox" hidden>
//     <li class="hw-listbox__option" role="option" data-value="w4" aria-selected="true">Ward W4</li>
//   </ul>
// </div>
// Emits: "hw:change" on the .hw-select with detail { value, label }.
import { on, cleanupAll, uid, placePopup, rovingIndex } from './core/dom.js';
import { leave, cancelLeave } from './core/motion.js';

const options = (list) => [...list.querySelectorAll('[role="option"]:not([aria-disabled="true"])')];

const isOpen = (root) => root.querySelector('.hw-select__trigger').getAttribute('aria-expanded') === 'true';

function open(root) {
  const trigger = root.querySelector('.hw-select__trigger');
  const list = root.querySelector('[role="listbox"]');
  cancelLeave(list);
  list.hidden = false;
  trigger.setAttribute('aria-expanded', 'true');
  placePopup(trigger, list);
  const opts = options(list);
  const selected = opts.find((o) => o.getAttribute('aria-selected') === 'true') ?? opts[0];
  setActive(root, selected);
}

function close(root, { focus = true } = {}) {
  const trigger = root.querySelector('.hw-select__trigger');
  const list = root.querySelector('[role="listbox"]');
  if (!isOpen(root)) return;
  trigger.setAttribute('aria-expanded', 'false');
  leave(list).then((done) => { if (done) list.hidden = true; });
  trigger.removeAttribute('aria-activedescendant');
  if (focus) trigger.focus();
}

function setActive(root, option) {
  if (!option) return;
  const trigger = root.querySelector('.hw-select__trigger');
  root.querySelectorAll('[data-active]').forEach((o) => o.removeAttribute('data-active'));
  option.dataset.active = '';
  option.id ||= uid('hw-option');
  trigger.setAttribute('aria-activedescendant', option.id);
  option.scrollIntoView({ block: 'nearest' });
}

function choose(root, option) {
  const trigger = root.querySelector('.hw-select__trigger');
  const hidden = root.querySelector('input[type="hidden"]');
  root.querySelectorAll('[role="option"]').forEach((o) => o.setAttribute('aria-selected', String(o === option)));
  trigger.textContent = option.textContent.trim();
  if (hidden) hidden.value = option.dataset.value ?? option.textContent.trim();
  root.dispatchEvent(new CustomEvent('hw:change', { bubbles: true, detail: { value: option.dataset.value, label: trigger.textContent } }));
  close(root);
}

export function init(scope = document) {
  scope.querySelectorAll('[data-hw-select]').forEach((root) => {
    const list = root.querySelector('[role="listbox"]');
    list.id ||= uid('hw-listbox');
    root.querySelector('.hw-select__trigger').setAttribute('aria-controls', list.id);
  });

  return cleanupAll([
    on(document, 'click', (e) => {
      const root = e.target.closest('[data-hw-select]');
      document.querySelectorAll('[data-hw-select]').forEach((r) => r !== root && close(r, { focus: false }));
      if (!root) return;
      const option = e.target.closest('[role="option"]');
      if (option && option.getAttribute('aria-disabled') !== 'true') return choose(root, option);
      if (e.target.closest('.hw-select__trigger')) {
        isOpen(root) ? close(root) : open(root);
      }
    }),
    on(document, 'keydown', (e) => {
      const trigger = e.target.closest?.('[data-hw-select] .hw-select__trigger');
      if (!trigger) return;
      const root = trigger.closest('[data-hw-select]');
      const list = root.querySelector('[role="listbox"]');
      const opts = options(list);
      const active = opts.findIndex((o) => o.hasAttribute('data-active'));

      if (!isOpen(root)) {
        if (['ArrowDown', 'ArrowUp', 'Enter', ' '].includes(e.key)) { e.preventDefault(); open(root); }
        return;
      }
      if (e.key === 'Escape') { e.preventDefault(); close(root); return; }
      if (e.key === 'Tab') { close(root, { focus: false }); return; }
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); if (opts[active]) choose(root, opts[active]); return; }
      if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(e.key)) {
        e.preventDefault();
        setActive(root, opts[rovingIndex(e.key, Math.max(active, 0), opts.length)]);
        return;
      }
      // Type-ahead: jump to the next option starting with the typed letter
      if (e.key.length === 1) {
        const start = active + 1;
        const match = [...opts.slice(start), ...opts.slice(0, start)]
          .find((o) => o.textContent.trim().toLowerCase().startsWith(e.key.toLowerCase()));
        if (match) setActive(root, match);
      }
    }),
  ]);
}
