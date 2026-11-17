// Stat: one number with its trend and what changed. Not a graph frame: a small tile for a row of key numbers.
//
//   stat(el, { label: 'Alerts per nurse shift', value: '0.13', values: [0.20, 0.17, 0.15, 0.14, 0.13, 0.128],
//              change: { direction: 'down', text: 'Down 36% in six weeks' }, note: 'NEWS2 alone stayed at 0.41' })
// The arrow says which way the number moved; whether that is good is for the words to say.
import { svg, el, missing } from './frame.js';

export function stat(root, { label, value, values = [], change, note } = {}) {
  root.replaceChildren();
  root.classList.add('hw-stat');
  el('span', 'hw-stat__label', root, label);
  const main = el('div', 'hw-stat__main', root);
  el('span', 'hw-stat__value', main, value);

  const points = values.map((v, i) => [i, v]).filter(([, v]) => !missing(v));
  if (points.length > 1) {
    const W = 140, H = 48, pad = 6;
    const lo = Math.min(...points.map(([, v]) => v)), hi = Math.max(...points.map(([, v]) => v));
    const xAt = (i) => pad + (i / (values.length - 1)) * (W - pad * 2);
    const yAt = (v) => H - pad - ((v - lo) / (hi - lo || 1)) * (H - pad * 2);
    const spark = svg('svg', { viewBox: `0 0 ${W} ${H}`, width: W, height: H, class: 'hw-stat__spark', role: 'img', 'aria-label': `Trend from ${points[0][1]} to ${points.at(-1)[1]}` }, main);
    svg('path', { d: points.map(([i, v], k) => `${k ? 'L' : 'M'}${xAt(i).toFixed(1)},${yAt(v).toFixed(1)}`).join(''), class: 'hw-stat__line' }, spark);
    const [li, lv] = points.at(-1);
    svg('circle', { cx: xAt(li), cy: yAt(lv), r: 4.5, class: 'hw-stat__dot' }, spark);
  }
  if (change?.text) {
    const line = el('span', 'hw-stat__change', root);
    if (change.direction === 'up' || change.direction === 'down') {
      const icon = el('i', `hw-icon fa-solid fa-arrow-${change.direction}`, line);
      icon.setAttribute('aria-hidden', 'true');
    }
    line.append(change.text);
  }
  if (note) el('span', 'hw-stat__note', root, note);
  return { destroy() { root.replaceChildren(); } };
}
