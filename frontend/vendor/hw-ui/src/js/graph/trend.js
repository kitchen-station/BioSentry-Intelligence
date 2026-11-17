// Trend: lines over time. One line is highlighted, the rest are context; names and last values sit at the
// line ends; the sentence above the plot reads out whichever point you are on.
//
//   trend(el, {
//     title: 'Checking alerts against each patient’s own normal cut them by two thirds',
//     subtitle: 'Alerts per nurse shift on held-out wards, six weeks',
//     x: ['Week 1', …], xLabel: 'Week',
//     series: [{ name: 'NEWS2 alone', values: [0.42, …] }, { name: 'Student model', values: […], dashed: true }, …],
//     highlight: 'Checked student',   // name or index; defaults to the last series
//     unit: '', format: (v) => v.toFixed(2), height: 250,
//   })
// null in values breaks the line. Returns { update(config), destroy() }.
import { frame, svg, el, missing, formatter, niceScale } from './frame.js';

const GAP = 16; // minimum distance between two end labels

/** Keep labels apart without leaving the plot: push down, pull back from the floor, re-centre pushed groups. */
function spread(items, floor) {
  items.sort((a, b) => a.at - b.at);
  items.forEach((it) => { it.y = it.at; });
  for (let i = 1; i < items.length; i++) items[i].y = Math.max(items[i].y, items[i - 1].y + GAP);
  for (let i = 0; i < items.length; i++) {
    let j = i;
    while (j + 1 < items.length && items[j + 1].y - items[j].y <= GAP + 0.01) j++;
    if (j > i) {
      const shift = items.slice(i, j + 1).reduce((s, it) => s + (it.y - it.at), 0) / (j - i + 1);
      for (let k = i; k <= j; k++) items[k].y -= shift;
    }
    i = j;
  }
  const over = items.length ? items.at(-1).y - floor : 0;
  if (over > 0) items.forEach((it) => { it.y -= over; });
  return items;
}

export function trend(root, initial) {
  let config = initial;
  let f, keyButtons, fmt, focus, index, width = 0, geo = null;

  const clampIndex = (i) => Math.max(0, Math.min(config.x.length - 1, i));

  function say() {
    const { series, x } = config;
    f.say({
      value: fmt(series[focus].values[index]),
      label: `${series[focus].name}, ${x[index]}`,
      detail: series.filter((_, k) => k !== focus).map((s) => `${s.name} ${fmt(s.values[index])}`).join(', '),
    });
  }

  /** Move the reading to point i without redrawing (keeps keyboard focus where it is). */
  function point(i) {
    index = clampIndex(i);
    if (geo) {
      geo.guide.setAttribute('x1', geo.xAt(index));
      geo.guide.setAttribute('x2', geo.xAt(index));
      geo.dots.forEach((dot, k) => {
        const v = config.series[k].values[index];
        dot.toggleAttribute('hidden', missing(v));
        if (!missing(v)) { dot.setAttribute('cx', geo.xAt(index)); dot.setAttribute('cy', geo.yAt(v)); }
      });
      geo.ticks.forEach((t, k) => t?.toggleAttribute('data-on', k === index));
    }
    say();
  }

  function draw() {
    const { x, series, height = 250 } = config;
    width = Math.round(f.plot.clientWidth);
    if (!width) return;
    f.plot.querySelector('svg')?.remove();
    const n = x.length;
    const all = series.flatMap((s) => s.values).filter((v) => !missing(v));
    const { top, ticks } = niceScale(Math.max(config.max ?? 0, ...all));
    const endLabels = width >= 480 && series.length <= 4;
    const longest = Math.max(...series.map((s) => s.name.length + fmt(s.values.findLast((v) => !missing(v))).length + 1));
    const right = endLabels ? Math.min(width * 0.36, longest * 6.6 + 24) : 14;
    const x0 = 36, x1 = width - right - 6, y0 = 14, y1 = height - 34;
    const xAt = (i) => (n === 1 ? (x0 + x1) / 2 : x0 + (i * (x1 - x0)) / (n - 1));
    const yAt = (v) => y1 - (v / top) * (y1 - y0);

    const root_ = svg('svg', { viewBox: `0 0 ${width} ${height}`, height, class: 'hw-graph__svg', 'aria-hidden': 'true' });
    f.plot.prepend(root_);
    ticks.forEach((t) => {
      svg('line', { x1: 0, x2: x1 + 6, y1: yAt(t), y2: yAt(t), class: t === 0 ? 'hw-graph__baseline' : 'hw-graph__gridline' }, root_);
      svg('text', { x: 0, y: yAt(t) - 6, class: 'hw-graph__tick' }, root_).textContent = fmt(t);
    });
    const room = Math.max(...x.map((label) => String(label).length)) * 6.6 + 14; // one time label
    const every = Math.max(1, Math.ceil(n / Math.max(1, Math.floor((x1 - x0) / room) + 1)));
    const tickEls = x.map((label, i) => {
      const lastOne = i === n - 1;
      if (!lastOne && (i % every || n - 1 - i < every)) return null;
      const t = svg('text', { x: xAt(i), y: height - 12, class: 'hw-graph__tick hw-graph__tick--x' }, root_);
      t.textContent = label;
      return t;
    });
    const guide = svg('line', { y1: y0, y2: y1, class: 'hw-graph__guide' }, root_);

    const order = series.map((_, k) => k).filter((k) => k !== focus).concat(focus); // highlight on top
    const ends = [];
    order.forEach((k) => {
      const s = series[k];
      let d = '', pen = false;
      s.values.forEach((v, i) => {
        if (missing(v)) { pen = false; return; }
        d += `${pen ? 'L' : 'M'}${xAt(i).toFixed(1)},${yAt(v).toFixed(1)}`;
        pen = true;
      });
      svg('path', { d, class: 'hw-graph__line', 'data-on': k === focus ? '' : null, 'data-dashed': s.dashed ? '' : null }, root_);
      const end = s.values.findLastIndex((v) => !missing(v));
      if (end < 0) return;
      svg('circle', { cx: xAt(end), cy: yAt(s.values[end]), r: 4, class: 'hw-graph__dot', 'data-on': k === focus ? '' : null }, root_);
      ends.push({ k, at: yAt(s.values[end]), x: xAt(end), text: fmt(s.values[end]) });
    });
    if (endLabels) {
      spread(ends, y1).forEach((e) => {
        const t = svg('text', { x: e.x + 14, y: e.y, class: 'hw-graph__end', 'data-on': e.k === focus ? '' : null }, root_);
        svg('tspan', {}, t).textContent = series[e.k].name;
        svg('tspan', { dx: 6, class: 'hw-graph__end-value' }, t).textContent = e.text;
      });
    }
    const dots = series.map((_, k) => svg('circle', { r: 4.5, class: 'hw-graph__dot', 'data-on': k === focus ? '' : null }, root_));
    root_.append(dots[focus]); // highlighted reading dot on top

    geo = { xAt, yAt, guide, dots, ticks: tickEls, x0, x1 };
    keyButtons.forEach((b, k) => b.setAttribute('aria-pressed', String(k === focus)));
    point(index);
  }

  function build() {
    const { series, x } = config;
    fmt = formatter(config);
    const named = typeof config.highlight === 'number' ? config.highlight : series.findIndex((s) => s.name === config.highlight);
    focus = named >= 0 && named < series.length ? named : series.length - 1;
    index = x.length - 1;
    geo = null;
    f = frame(root, 'trend', config);

    const keys = el('div', 'hw-graph__keys');
    keys.setAttribute('role', 'group');
    keys.setAttribute('aria-label', 'Highlight a line');
    keys.hidden = series.length < 2;
    f.plot.before(keys);
    keyButtons = series.map((s, k) => {
      const b = el('button', 'hw-graph__key', keys);
      b.type = 'button';
      b.toggleAttribute('data-dashed', Boolean(s.dashed));
      el('span', 'hw-graph__swatch', b);
      b.append(s.name);
      b.addEventListener('click', () => { focus = k; draw(); });
      return b;
    });

    f.plot.tabIndex = 0;
    f.plot.setAttribute('role', 'group');
    f.plot.setAttribute('aria-label', `${config.title}. Use the left and right arrow keys to read each point.`);
    f.table({
      columns: [config.xLabel ?? '', ...series.map((s) => s.name)],
      rows: x.map((label, i) => [label, ...series.map((s) => fmt(s.values[i]))]),
    });
    f.hint(config.hint ?? 'Point at the graph, or use the arrow keys, to read each point');
    draw();
  }

  const fromPointer = (e) => {
    if (!geo) return;
    const px = e.clientX - f.plot.getBoundingClientRect().left;
    const n = config.x.length;
    point(n === 1 ? 0 : Math.round(((px - geo.x0) / (geo.x1 - geo.x0)) * (n - 1)));
  };
  const toLatest = () => point(config.x.length - 1);
  const onKey = (e) => {
    const step = { ArrowRight: 1, ArrowUp: 1, ArrowLeft: -1, ArrowDown: -1 }[e.key];
    if (step) { e.preventDefault(); point(index + step); }
    else if (e.key === 'Home') { e.preventDefault(); point(0); }
    else if (e.key === 'End') { e.preventDefault(); toLatest(); }
  };
  function listen(on) {
    const m = on ? 'addEventListener' : 'removeEventListener';
    f.plot[m]('pointermove', fromPointer);
    f.plot[m]('pointerdown', fromPointer);
    f.plot[m]('pointerleave', toLatest);
    f.plot[m]('keydown', onKey);
  }

  build();
  listen(true);
  const ro = new ResizeObserver(() => { if (!f.plot.hidden && Math.round(f.plot.clientWidth) !== width) draw(); });
  ro.observe(root);

  return {
    update(next) { listen(false); config = { ...config, ...next }; build(); listen(true); },
    destroy() { ro.disconnect(); listen(false); root.replaceChildren(); },
  };
}
