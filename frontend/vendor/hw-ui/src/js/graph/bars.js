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
//
// Changes against a reference: diverging: true puts a labelled zero line in the middle, losses to its left and
// gains to its right (the scale is symmetric, so equal changes have equal lengths). Any item may carry an
// interval: [lo, hi], drawn as a whisker across its bar and read out with intervalLabel.
//
//   bars(el, {
//     title: 'Only the checked student added to NEWS2 gained beyond chance', diverging: true,
//     zeroLabel: '0 · NEWS2 alone', sides: ['a loss', 'a gain'], intervalLabel: '98.33% interval',
//     items: [{ name: 'Checked student', value: 0.018, interval: [0.008, 0.029] }, { name: 'Student', value: -0.040, interval: [-0.054, -0.025] }],
//     highlight: 0,
//   })
import { frame, el, formatter, missing, niceScale, selectable } from './frame.js';

export function bars(root, config) {
  const { items, baseline, diverging = false } = config;
  const fmt = diverging && !config.format ? formatter({ ...config, signDisplay: 'exceptZero' }) : formatter(config);
  const ends = items.flatMap((i) => [i.value, ...(i.interval ?? [])]).filter((v) => !missing(v));
  // Diverging: one nice half-scale mirrored around zero. Otherwise the scale starts at zero, as before.
  const half = config.max ? { top: config.max, ticks: [0, config.max / 2, config.max] } : niceScale(Math.max(...ends.map(diverging ? Math.abs : (v) => v)));
  const { top, ticks } = diverging
    ? { top: half.top, ticks: [...half.ticks.slice(1).reverse().map((t) => -t), ...half.ticks] }
    : half;
  const f = frame(root, 'bars', config);
  root.toggleAttribute('data-diverging', diverging);

  const group = el('div', 'hw-graph__rows', f.plot);
  group.setAttribute('role', 'group');
  group.setAttribute('aria-labelledby', f.titleId);
  const at = (v) => Math.max(0, Math.min(100, diverging ? ((v + top) / (2 * top)) * 100 : (v / top) * 100));
  const pct = (v) => `${at(v)}%`;
  const zero = at(0);
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
    const negative = diverging && item.value < 0;
    if (diverging) {
      track.dataset.sign = negative ? 'negative' : 'positive';
      track.style.setProperty('--hw-graph-start', `${Math.min(zero, at(item.value))}%`);
      track.style.setProperty('--hw-graph-size', `${Math.abs(at(item.value) - zero)}%`);
    } else {
      track.style.setProperty('--hw-graph-size', pct(item.value));
    }
    rules(track);
    el('span', 'hw-graph__bar', track);
    if (item.interval) {
      const [lo, hi] = item.interval;
      const whisker = el('span', 'hw-graph__interval', track);
      whisker.setAttribute('aria-hidden', 'true');
      whisker.style.setProperty('--hw-graph-start', pct(lo));
      whisker.style.setProperty('--hw-graph-size', `${at(hi) - at(lo)}%`);
      // The value sits past the whisker's far end, so the two never overlap.
      track.style.setProperty('--hw-graph-label', pct(negative ? Math.min(lo, item.value) : Math.max(hi, item.value)));
    } else if (negative) {
      track.style.setProperty('--hw-graph-label', pct(item.value));
    }
    el('span', 'hw-graph__bar-value', track, fmt(item.value));
    return row;
  });
  const scale = el('div', 'hw-graph__scale', f.plot);
  scale.setAttribute('aria-hidden', 'true');
  el('span', '', scale);
  const marks = el('span', 'hw-graph__track', scale);
  ticks.forEach((t, i) => {
    const mark = el('span', 'hw-graph__scale-mark', marks, diverging && t === 0 && config.zeroLabel ? config.zeroLabel : fmt(t));
    mark.style.setProperty('--hw-graph-at', pct(t));
    if (i === 0) mark.dataset.edge = 'start';
    if (i === ticks.length - 1) mark.dataset.edge = 'end';
    mark.toggleAttribute('data-base', diverging && t === 0);
  });
  if (diverging && config.sides) {
    el('span', '', scale);
    const sides = el('span', 'hw-graph__track hw-graph__sides', scale);
    sides.style.setProperty('--hw-graph-at', `${zero}%`);
    config.sides.forEach((text, i) => { el('span', 'hw-graph__side', sides, text).dataset.side = i ? 'gain' : 'loss'; });
  }

  const intervalText = (item) => (item.interval ? `${config.intervalLabel ?? 'Interval'} ${fmt(item.interval[0])} to ${fmt(item.interval[1])}` : '');
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
    onSelect: (i) => f.say({
      value: fmt(items[i].value),
      label: config.label ? config.label(items[i]) : items[i].name,
      detail: [intervalText(items[i]), compare(i)].filter(Boolean).join('. '),
    }),
  });
  f.hint(config.hint ?? 'Point at a bar, tap it, or use the arrow keys');
  return { destroy() { root.replaceChildren(); } };
}
