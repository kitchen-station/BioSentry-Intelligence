// Steps: a score taken at set times (NEWS2 each hour). The line is flat for each reading, breaks where a
// reading is missing, and never slopes between readings. Limits are drawn and named; the values sit under
// the plot so nothing has to be read off an axis.
//
//   steps(el, {
//     title: 'NEWS2 for bed 4 has climbed since early morning', subtitle: 'Reyes, M. One reading an hour, 03:00 to 14:00',
//     x: ['03', '04', …], times: ['03:00', '04:00', …],        // short labels under the plot, full ones for sentences
//     values: [2, 2, 2, 3, 3, null, 4, 4, 5, 5, 6, 7],
//     limits: [{ at: 5, label: 'Urgent review', tone: 'urgent' }, { at: 7, label: 'Emergency', tone: 'emergency' }],
//     max: 9,
//   })
import { frame, svg, el, missing, formatter } from './frame.js';

export function steps(root, config) {
  const { x, values, limits = [] } = config;
  const times = config.times ?? x;
  const fmt = formatter(config);
  const f = frame(root, 'steps', config);
  const cells = el('div', 'hw-graph__cells', f.plot);
  cells.setAttribute('aria-hidden', 'true');
  const sorted = [...limits].sort((a, b) => a.at - b.at);
  const limitFor = (v) => (missing(v) ? null : sorted.findLast((l) => v >= l.at) ?? null);
  const n = values.length;
  const now = values.findLastIndex((v) => !missing(v));
  const top = Math.max(config.max ?? 0, ...values.filter((v) => !missing(v)), ...sorted.map((l) => l.at));
  let width = 0;

  // values and time labels under the plot, in the same columns as the steps
  cells.style.setProperty('--hw-graph-count', n);
  values.forEach((v) => {
    const cell = el('span', 'hw-graph__cell', cells, fmt(v));
    const tone = limitFor(v)?.tone;
    if (tone) cell.dataset.tone = tone;
  });
  x.forEach((label) => el('span', 'hw-graph__cell hw-graph__cell--time', cells, label));

  // the sentence: latest reading, change since the first, when each limit was reached, what is missing
  if (now >= 0) {
    const first = values.findIndex((v) => !missing(v));
    const change = values[now] - values[first];
    const bits = [change === 0 ? `Unchanged since ${times[first]}.` : `${change > 0 ? 'Up' : 'Down'} ${fmt(Math.abs(change))} since ${times[first]}.`];
    sorted.filter((l) => values[now] >= l.at).forEach((l) => {
      let start = now;
      while (start > 0 && !missing(values[start - 1]) && values[start - 1] >= l.at) start--;
      if (start !== now) bits.push(`Reached ${fmt(l.at)} at ${times[start]}.`);
    });
    const gaps = times.filter((_, i) => missing(values[i]) && i < now);
    if (gaps.length) bits.push(gaps.length <= 2 ? `No reading at ${gaps.join(' or ')}.` : `${gaps.length} readings missing.`);
    if (now < n - 1) bits.push(`No reading since ${times[now]}.`);
    const limit = limitFor(values[now]);
    f.say({ value: fmt(values[now]), label: `at ${times[now]}${limit?.label ? `, ${limit.label.toLowerCase()}` : ''}`, detail: bits.join(' '), tone: limit?.tone });
  } else {
    f.say({ value: '–', label: 'No readings yet' });
  }

  function draw() {
    width = Math.round(f.plot.clientWidth);
    if (!width) return;
    f.plot.querySelector('svg')?.remove();
    const named = sorted.filter((l) => l.label);
    const gutter = named.length && width >= 480 ? Math.min(160, Math.max(...named.map((l) => l.label.length + String(l.at).length + 1)) * 6.8 + 26) : 0;
    const inset = gutter || 6; // room for the latest-reading dot when there is no label gutter
    const plotW = width - inset;
    const colW = plotW / n;
    const height = config.height ?? 200, y0 = 20, y1 = height - 28;
    const yAt = (v) => y1 - (v / top) * (y1 - y0);
    cells.style.setProperty('--hw-graph-gutter', `${inset}px`);

    const root_ = svg('svg', { viewBox: `0 0 ${width} ${height}`, height, class: 'hw-graph__svg', role: 'img', 'aria-label': `${config.title}. Readings: ${values.map((v) => (missing(v) ? 'none' : fmt(v))).join(', ')}` });
    f.plot.prepend(root_);
    svg('line', { x1: 0, x2: plotW, y1, y2: y1, class: 'hw-graph__baseline' }, root_);
    sorted.forEach((l) => {
      svg('line', { x1: 0, x2: plotW + (gutter ? 8 : 0), y1: yAt(l.at), y2: yAt(l.at), class: 'hw-graph__limit', 'data-tone': l.tone }, root_);
      if (!l.label) return;
      const t = gutter
        ? svg('text', { x: plotW + 14, y: yAt(l.at), class: 'hw-graph__limit-label', 'data-tone': l.tone }, root_)
        : svg('text', { x: 2, y: yAt(l.at) - 5, class: 'hw-graph__limit-label hw-graph__limit-label--inside', 'data-tone': l.tone }, root_);
      svg('tspan', { class: 'hw-graph__limit-at' }, t).textContent = fmt(l.at);
      svg('tspan', { dx: 6 }, t).textContent = l.label;
    });
    values.forEach((v, i) => {
      if (missing(v)) {
        svg('rect', { x: i * colW + 0.5, y: y0, width: colW - 1, height: y1 - y0, rx: 3, class: 'hw-graph__hole' }, root_);
        return;
      }
      const next = values[i + 1];
      if (!missing(next) && next !== v) svg('line', { x1: (i + 1) * colW, x2: (i + 1) * colW, y1: yAt(v), y2: yAt(next), class: 'hw-graph__riser' }, root_);
    });
    values.forEach((v, i) => {
      if (missing(v)) return;
      svg('line', { x1: i * colW + 2, x2: (i + 1) * colW - 2, y1: yAt(v), y2: yAt(v), class: 'hw-graph__step', 'data-tone': limitFor(v)?.tone }, root_);
    });
    if (now >= 0) svg('circle', { cx: (now + 1) * colW - 2, cy: yAt(values[now]), r: 5.5, class: 'hw-graph__now-dot', 'data-tone': limitFor(values[now])?.tone }, root_);
  }

  f.hint(config.hint ?? 'The line is flat for each reading and breaks where a reading is missing');
  draw();
  const ro = new ResizeObserver(() => { if (Math.round(f.plot.clientWidth) !== width) draw(); });
  ro.observe(root);
  return { destroy() { ro.disconnect(); root.replaceChildren(); } };
}
