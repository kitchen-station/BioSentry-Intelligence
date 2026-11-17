// Range: a value now against its usual range, one row per measure. A reading outside its range is
// highlighted and says how far outside it is.
//
//   range(el, {
//     title: 'Four of this patient’s readings are outside their own usual range',
//     subtitle: 'Reyes, M., bed 4. Readings at 14:00 against the last five days',
//     items: [{ label: 'Breathing', value: 22, unit: ' /min', usual: [14, 18], scale: [8, 30] }, …],
//   })
// scale is the span of the little axis for that row; usual is drawn as a band on it.
import { frame, el, missing } from './frame.js';

const round = (n) => Math.round(n * 10) / 10;

export function range(root, config) {
  const f = frame(root, 'range', config);
  f.say(); // this graph has no readout: every row already carries its value in words
  f.root.querySelector('.hw-graph__readout').hidden = true;

  const list = el('ul', 'hw-graph__list', f.plot);
  config.items.forEach((item) => {
    const [lo, hi] = item.usual;
    const [min, max] = item.scale ?? [Math.min(lo, item.value ?? lo) - (hi - lo), Math.max(hi, item.value ?? hi) + (hi - lo)];
    const pos = (v) => Math.max(0, Math.min(100, ((v - min) / (max - min)) * 100));
    const v = item.value;
    const above = !missing(v) && v > hi;
    const below = !missing(v) && v < lo;
    const unit = item.unit ?? '';

    const row = el('li', 'hw-graph__item', list);
    row.toggleAttribute('data-off', above || below);
    el('span', 'hw-graph__item-label', row, item.label);
    el('span', 'hw-graph__item-value', row, missing(v) ? 'Missing' : `${v}${unit}`);
    const track = el('span', 'hw-graph__item-track', row);
    track.setAttribute('aria-hidden', 'true');
    const band = el('span', 'hw-graph__usual', track);
    band.style.setProperty('--hw-graph-from', `${pos(lo)}%`);
    band.style.setProperty('--hw-graph-span', `${pos(hi) - pos(lo)}%`);
    if (above || below) {
      const gap = el('span', 'hw-graph__gap', track);
      gap.style.setProperty('--hw-graph-from', `${above ? pos(hi) : pos(v)}%`);
      gap.style.setProperty('--hw-graph-span', `${above ? pos(v) - pos(hi) : pos(lo) - pos(v)}%`);
    }
    if (!missing(v)) el('span', 'hw-graph__now', track).style.setProperty('--hw-graph-at', `${pos(v)}%`);
    const usual = `usual ${lo} to ${hi}`;
    el('span', 'hw-graph__item-note', row, above ? `${round(v - hi)} above ${usual}` : below ? `${round(lo - v)} below ${usual}` : usual);
  });

  const legend = el('p', 'hw-graph__legend', f.foot);
  legend.setAttribute('aria-hidden', 'true');
  [['usual', 'Usual range'], ['inside', 'Now, inside it'], ['outside', 'Now, outside it'], ['gap', 'How far outside']].forEach(([kind, text]) => {
    const item = el('span', 'hw-graph__legend-item', legend);
    el('span', 'hw-graph__legend-mark', item).dataset.kind = kind;
    item.append(text);
  });
  return { destroy() { root.replaceChildren(); } };
}
