// Shared by every graph: the frame (headline, subtitle, a readout sentence, the plot, a foot line),
// number formatting, and "one highlighted mark" selection for graphs made of buttons.
//
// Design rules the frame enforces (docs/graphs.md):
//   - the headline states the finding, the subtitle says what was measured;
//   - values are read from a sentence above the plot, never from a tooltip floating over the data;
//   - one mark is highlighted, everything else is context.
import { uid, rovingIndex } from '../core/dom.js';

const NS = 'http://www.w3.org/2000/svg';
export const missing = (v) => v === null || v === undefined || Number.isNaN(v);

export function svg(tag, attrs, parent) {
  const n = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs ?? {})) if (!missing(v)) n.setAttribute(k, String(v));
  parent?.append(n);
  return n;
}
export function el(tag, className, parent, text) {
  const n = document.createElement(tag);
  if (className) n.className = className;
  if (!missing(text)) n.textContent = text; // textContent only: labels are often user data
  parent?.append(n);
  return n;
}

/** v => "0.13", "57%", "–". Pass config.format to take over completely. */
export function formatter({ format, unit = '' } = {}) {
  if (format) return (v) => (missing(v) ? '–' : format(v));
  const nf = new Intl.NumberFormat(document.documentElement.lang || undefined, { maximumFractionDigits: 2 });
  return (v) => (missing(v) ? '–' : nf.format(v) + unit);
}

/** A round top for an axis that starts at zero, and the gridline values under it (two to four steps). */
export function niceScale(max) {
  if (!(max > 0)) return { top: 1, ticks: [0, 1] };
  let best = null;
  [2, 3, 4].forEach((parts) => {
    const mag = 10 ** Math.floor(Math.log10(max / parts));
    const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s * parts >= max - 1e-9);
    if (!best || step * parts < best.step * best.parts - 1e-9) best = { step, parts };
  });
  const ticks = [];
  for (let i = 0; i <= best.parts; i++) ticks.push(+(best.step * i).toFixed(10));
  return { top: best.step * best.parts, ticks };
}

export function frame(root, kind, { title, subtitle, level = 3 } = {}) {
  root.replaceChildren();
  root.classList.add('hw-graph');
  root.dataset.hwGraph = kind;
  root.setAttribute('role', 'figure');
  const head = el('div', 'hw-graph__head', root);
  const heading = el(`h${level}`, 'hw-graph__title', head, title);
  heading.id = uid('hw-graph-title');
  root.setAttribute('aria-labelledby', heading.id);
  if (subtitle) el('p', 'hw-graph__subtitle', head, subtitle);

  const readout = el('p', 'hw-graph__readout', root);
  readout.setAttribute('aria-live', 'polite');
  readout.setAttribute('aria-atomic', 'true');
  const value = el('span', 'hw-graph__value', readout);
  const reading = el('span', 'hw-graph__reading', readout);
  const label = el('span', 'hw-graph__label', reading);
  const detail = el('span', 'hw-graph__detail', reading);

  const plot = el('div', 'hw-graph__plot', root);
  const foot = el('div', 'hw-graph__foot', root);

  return {
    root, plot, foot, titleId: heading.id,
    /** Update the sentence above the plot. tone: 'urgent' | 'emergency' colours the number. */
    say({ value: v = '', label: l = '', detail: d = '', tone } = {}) {
      value.textContent = v;
      label.textContent = l;
      detail.textContent = d;
      if (tone) readout.dataset.tone = tone; else delete readout.dataset.tone;
    },
    hint(text) { if (text) el('span', 'hw-graph__hint', foot, text); },
    /** Adds a "Show as a table" switch that swaps the plot for its numbers. */
    table({ columns, rows }) {
      const wrap = el('div', 'hw-graph__table hw-table-wrap');
      wrap.hidden = true;
      plot.after(wrap);
      const table = el('table', 'hw-table', wrap);
      el('caption', 'hw-visually-hidden', table, title);
      const headRow = el('tr', '', el('thead', '', table));
      columns.forEach((c, i) => { const th = el('th', i ? 'hw-table__num' : '', headRow, c); th.scope = 'col'; });
      const body = el('tbody', '', table);
      rows.forEach((r) => {
        const tr = el('tr', '', body);
        r.forEach((cell, i) => { if (i) el('td', 'hw-table__num', tr, cell); else el('th', '', tr, cell).scope = 'row'; });
      });
      const toggle = el('button', 'hw-graph__toggle', null, 'Show as a table');
      toggle.type = 'button';
      toggle.setAttribute('aria-pressed', 'false');
      foot.prepend(toggle);
      toggle.addEventListener('click', () => {
        const on = wrap.hidden;
        wrap.hidden = !on;
        plot.hidden = on;
        toggle.setAttribute('aria-pressed', String(on));
        toggle.textContent = on ? 'Show as a graph' : 'Show as a table';
      });
    },
  };
}

/**
 * One highlighted mark among several buttons. aria-pressed marks it; the group is one tab stop and the
 * arrow keys move the highlight. Pointing with a mouse, tapping and tabbing all select.
 */
export function selectable(group, buttons, { selected = 0, onSelect } = {}) {
  let current = -1;
  const set = (i, focus = false) => {
    if (i !== current) {
      current = i;
      buttons.forEach((b, k) => {
        b.setAttribute('aria-pressed', String(k === i));
        b.tabIndex = k === i ? 0 : -1;
      });
      onSelect?.(i);
    }
    if (focus) buttons[i].focus();
  };
  buttons.forEach((b, i) => {
    b.addEventListener('click', () => set(i));
    b.addEventListener('focus', () => set(i));
    b.addEventListener('pointerenter', (e) => { if (e.pointerType === 'mouse') set(i); });
  });
  group.addEventListener('keydown', (e) => {
    if (!['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End'].includes(e.key)) return;
    e.preventDefault();
    set(rovingIndex(e.key, current, buttons.length), true);
  });
  set(Math.max(0, Math.min(buttons.length - 1, selected)));
  return { select: set, get index() { return current; } };
}
