// Columns: one measure over time. The highlighted column is read out against the average.
//
//   columns(el, {
//     title: 'Alerts on Ward W4 have fallen for two weeks', subtitle: 'Alerts per day, 3 to 16 November',
//     x: ['3 Nov', '4 Nov', …], values: [9, 7, …],
//     highlight: 13,                    // defaults to the latest
//     average: true, averageLabel: '14-day average',
//     noun: ['alert', 'alerts'],        // singular, plural for the readout
//   })
import { frame, el, missing, formatter, niceScale, selectable } from './frame.js';

export function columns(root, config) {
  const { x, values } = config;
  const fmt = formatter(config);
  const present = values.filter((v) => !missing(v));
  const { top, ticks } = niceScale(Math.max(...present) * 1.12); // headroom for the value above a column
  const avg = present.reduce((a, v) => a + v, 0) / (present.length || 1);
  const averageLabel = config.averageLabel ?? 'average';
  const [one, many] = config.noun ?? ['', ''];
  const f = frame(root, 'columns', config);

  const group = el('div', 'hw-graph__columns', f.plot);
  group.setAttribute('role', 'group');
  group.setAttribute('aria-labelledby', f.titleId);
  const at = (v) => `${(v / top) * 100}%`;
  ticks.slice(1).forEach((t) => {
    const rule = el('span', 'hw-graph__level', group);
    rule.style.setProperty('--hw-graph-at', at(t));
    el('span', 'hw-graph__level-label', rule, fmt(t));
  });
  if (config.average !== false) {
    const line = el('span', 'hw-graph__level hw-graph__level--average', group);
    line.style.setProperty('--hw-graph-at', at(avg));
    el('span', 'hw-graph__level-label', line, `${averageLabel} ${fmt(Math.round(avg * 10) / 10)}`);
  }
  const buttons = values.map((v, i) => {
    const col = el('button', 'hw-graph__column', group);
    col.type = 'button';
    col.setAttribute('aria-label', `${x[i]}, ${fmt(v)}${many ? ` ${v === 1 ? one : many}` : ''}`);
    el('span', 'hw-graph__column-value', col, fmt(v));
    el('span', 'hw-graph__column-bar', col).style.setProperty('--hw-graph-size', missing(v) ? '0%' : at(v));
    return col;
  });
  // Time labels: first, last, and as many in between as fit without touching
  const ticksRow = el('div', 'hw-graph__ticks', f.plot);
  ticksRow.setAttribute('aria-hidden', 'true');
  const every = Math.max(1, Math.ceil(values.length / 7));
  const tickEls = x.map((label, i) => {
    const show = i === 0 || i === x.length - 1 || (i % every === 0 && x.length - 1 - i >= every);
    return el('span', 'hw-graph__tick-label', ticksRow, show ? label : '');
  });

  selectable(group, buttons, {
    selected: config.highlight ?? values.length - 1,
    onSelect: (i) => {
      const v = values[i];
      tickEls.forEach((t, k) => { t.toggleAttribute('data-on', k === i); if (k === i) t.textContent = x[i]; });
      const gap = Math.round(Math.abs(v - avg) * 10) / 10;
      f.say({
        value: fmt(v),
        label: `${many ? `${v === 1 ? one : many} on ` : ''}${x[i]}`,
        detail: config.average === false || missing(v) ? '' : gap === 0 ? `Right on the ${averageLabel}` : `${fmt(gap)} ${v < avg ? 'below' : 'above'} the ${averageLabel}`,
      });
    },
  });
  f.hint(config.hint ?? 'Point at a column, tap it, or use the arrow keys');
  return { destroy() { root.replaceChildren(); } };
}
