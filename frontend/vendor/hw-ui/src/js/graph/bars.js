// Bars: compare a few things. One bar is highlighted; point at, tap or arrow to another and the sentence
// above re-reads it, optionally against a baseline item.
//
//   bars(el, {
//     title: 'Both models catch more deteriorations than NEWS2 alone',
//     subtitle: 'Share of deteriorations flagged early, held-out wards',
//     items: [{ name: 'NEWS2 alone', value: 36 }, { name: 'Student model', value: 56 }, { name: 'Checked student', value: 57 }],
//     highlight: 2, baseline: 0, unit: '%', max: 100,
//     label: (item) => `flagged early by ${item.name}`,   // optional wording for the readout
//   })
import { frame, el, formatter, niceScale, selectable } from './frame.js';

export function bars(root, config) {
  const { items, baseline } = config;
  const fmt = formatter(config);
  const { top, ticks } = config.max ? { top: config.max, ticks: [0, config.max / 2, config.max] } : niceScale(Math.max(...items.map((i) => i.value)));
  const f = frame(root, 'bars', config);

  const group = el('div', 'hw-graph__rows', f.plot);
  group.setAttribute('role', 'group');
  group.setAttribute('aria-labelledby', f.titleId);
  const pct = (v) => `${Math.max(0, Math.min(100, (v / top) * 100))}%`;
  const rules = (track) => ticks.forEach((t) => {
    const rule = el('span', 'hw-graph__rule', track);
    rule.style.setProperty('--hw-graph-at', pct(t));
    rule.toggleAttribute('data-base', t === 0);
  });
  const buttons = items.map((item) => {
    const row = el('button', 'hw-graph__row', group);
    row.type = 'button';
    el('span', 'hw-graph__name', row, item.name);
    row.append(' '); // so the button reads as "name value"; whitespace makes no grid item
    const track = el('span', 'hw-graph__track', row);
    track.style.setProperty('--hw-graph-size', pct(item.value));
    rules(track);
    el('span', 'hw-graph__bar', track);
    el('span', 'hw-graph__bar-value', track, fmt(item.value));
    return row;
  });
  const scale = el('div', 'hw-graph__scale', f.plot);
  scale.setAttribute('aria-hidden', 'true');
  el('span', '', scale);
  const marks = el('span', 'hw-graph__track', scale);
  ticks.forEach((t, i) => {
    const mark = el('span', 'hw-graph__scale-mark', marks, fmt(t));
    mark.style.setProperty('--hw-graph-at', pct(t));
    if (i === 0) mark.dataset.edge = 'start';
    if (i === ticks.length - 1) mark.dataset.edge = 'end';
  });

  function compare(i) {
    if (typeof baseline !== 'number' || !items[baseline]) return '';
    if (i === baseline) return 'The baseline the others are measured against';
    const diff = items[i].value - items[baseline].value;
    if (diff === 0) return `The same as ${items[baseline].name}`;
    const amount = config.unit === '%' ? `${Math.abs(Math.round(diff * 10) / 10)} points` : fmt(Math.abs(diff));
    return `${amount} ${diff > 0 ? 'more' : 'less'} than ${items[baseline].name}`;
  }
  selectable(group, buttons, {
    selected: config.highlight ?? items.length - 1,
    onSelect: (i) => f.say({ value: fmt(items[i].value), label: config.label ? config.label(items[i]) : items[i].name, detail: compare(i) }),
  });
  f.hint(config.hint ?? 'Point at a bar, tap it, or use the arrow keys');
  return { destroy() { root.replaceChildren(); } };
}
