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
//
// Clinical options (all optional; without them the graph draws exactly as before):
//   trend(el, { …, unit: ' bpm',
//     range: [40, 140],                                  // fixed y-range; ticks at its two ends
//     limits: [{ at: 90, label: 'Adds 1 point above 90' }, { at: 110, label: 'Adds 2 points above 110' }],
//     band: [72, 88], bandLabel: 'Own usual 72–88',
//     highlightWhere: (v) => v > 90 || v <= 50,           // only these stretches of the highlighted line are lit
//   })
/**
 * @typedef {object} TrendSeries
 * @property {string} name
 * @property {(number|null)[]} values  one per x; null is a gap, never bridged
 * @property {boolean} [dashed]        tells two context lines apart
 *
 * @typedef {object} TrendLimit
 * @property {number} at               value where the dashed line is drawn (outside `range`: not drawn)
 * @property {string} [label]          its name at the right end of the line; defaults to the formatted value
 * @property {'urgent'|'emergency'} [tone]  colours the line and its name; leave it off for a grey limit
 *
 * @typedef {object} TrendConfig
 * @property {string} title            states the finding
 * @property {string} [subtitle]       says what was measured
 * @property {(string|number)[]} x     one label per point
 * @property {string} [xLabel]         first column of the table
 * @property {TrendSeries[]} series    up to four
 * @property {string|number} [highlight]  name or index of the highlighted line; defaults to the last
 * @property {number} [max]            zero-based scale: at least this tall (ignored with `range`)
 * @property {[number, number]} [range]  a fixed y-range in place of the zero-based scale, ticked at its two ends.
 *                                     Values outside it are drawn on its edge; the readout and the table keep the
 *                                     real value
 * @property {TrendLimit[]} [limits]   dashed horizontal lines, each named at its right end
 * @property {[number, number]} [band] a shaded range (a patient's own usual range), named at its right end
 * @property {string} [bandLabel]      the band's name; defaults to "Usual lo–hi"
 * @property {(v: number) => boolean} [highlightWhere]  only where this holds do the highlighted line's stretches,
 *                                     its dots and the readout number take the highlight colour; the rest of the
 *                                     line is context grey. A segment that crosses is cut where it crosses
 * @property {string} [unit]           appended to every number
 * @property {(v: number) => string} [format]  takes over number formatting
 * @property {string} [hint]           the line under the plot
 * @property {number} [height]         plot height in px, default 250
 * @property {number} [level]          heading level, default 3
 */
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

/**
 * The stretches of a line where pass(v) holds, as one path along the drawn line. A segment that crosses is cut
 * where it crosses (found by bisection on the value).
 * ponytail: a segment that passes at both ends is drawn whole, even if it dips out in between (a jump from below
 * one limit to above another in one step); split it at every crossing if that ever matters.
 */
function stretches(values, xAt, yAt, pass) {
  const ok = (v) => Boolean(pass(v));
  let d = '', pen = false;
  for (let i = 0; i < values.length - 1; i++) {
    const a = values[i], b = values[i + 1];
    if (missing(a) || missing(b) || (!ok(a) && !ok(b))) { pen = false; continue; }
    let t0 = 0, t1 = 1;
    if (ok(a) !== ok(b)) {
      let lo = 0, hi = 1;
      for (let j = 0; j < 16; j++) { const m = (lo + hi) / 2; if (ok(a + (b - a) * m) === ok(a)) lo = m; else hi = m; }
      if (ok(a)) t1 = lo; else t0 = hi;
    }
    const at = (t) => `${(xAt(i) + (xAt(i + 1) - xAt(i)) * t).toFixed(1)},${(yAt(a) + (yAt(b) - yAt(a)) * t).toFixed(1)}`;
    d += `${pen && t0 === 0 ? '' : `M${at(t0)}`}L${at(t1)}`;
    pen = t1 === 1;
  }
  return d;
}

export function trend(root, initial) {
  let config = initial;
  let f, keyButtons, fmt, focus, index, width = 0, geo = null;

  const clampIndex = (i) => Math.max(0, Math.min(config.x.length - 1, i));
  /** Does a reading of the highlighted line take the highlight colour? Always, unless highlightWhere says no. */
  const lit = (v) => !missing(v) && (!config.highlightWhere || Boolean(config.highlightWhere(v)));
  const limitName = (l) => l.label ?? fmt(l.at);
  const bandName = () => config.bandLabel ?? `Usual ${config.band[0]}–${config.band[1]}`;

  function say() {
    const { series, x } = config;
    const v = series[focus].values[index];
    f.say({
      value: fmt(v),
      label: `${series[focus].name}, ${x[index]}`,
      detail: series.filter((_, k) => k !== focus).map((s) => `${s.name} ${fmt(s.values[index])}`).join(', '),
      tone: config.highlightWhere && lit(v) ? 'highlight' : undefined,
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
        if (config.highlightWhere && k === focus) dot.toggleAttribute('data-on', lit(v));
      });
      geo.ticks.forEach((t, k) => t?.toggleAttribute('data-on', k === index));
    }
    say();
  }

  function draw() {
    const { x, series, height = 250, band, range: fixed } = config;
    width = Math.round(f.plot.clientWidth);
    if (!width) return;
    f.plot.querySelector('svg')?.remove();
    const n = x.length;
    const all = series.flatMap((s) => s.values).filter((v) => !missing(v));
    let lo = 0, top, ticks;
    if (fixed) { [lo, top] = fixed; ticks = [lo, top]; }
    else ({ top, ticks } = niceScale(Math.max(config.max ?? 0, ...all, ...(config.limits ?? []).map((l) => l.at), ...(band ?? []))));
    const limits = (config.limits ?? []).filter((l) => l.at >= lo && l.at <= top);
    const endLabels = width >= 480 && series.length <= 4;
    const longest = Math.max(...series.map((s) => s.name.length + fmt(s.values.findLast((v) => !missing(v))).length + 1),
      ...limits.map((l) => limitName(l).length), band ? bandName().length : 0);
    const right = endLabels ? Math.min(width * 0.36, longest * 6.6 + 24) : 14;
    const x0 = 36, x1 = width - right - 6, y0 = 14, y1 = height - 34;
    const xAt = (i) => (n === 1 ? (x0 + x1) / 2 : x0 + (i * (x1 - x0)) / (n - 1));
    const clamp = fixed ? (v) => Math.max(lo, Math.min(top, v)) : (v) => v; // outside a fixed range: on its edge
    const yAt = (v) => y1 - ((clamp(v) - lo) / (top - lo)) * (y1 - y0);

    const root_ = svg('svg', { viewBox: `0 0 ${width} ${height}`, height, class: 'hw-graph__svg', 'aria-hidden': 'true' });
    f.plot.prepend(root_);
    if (band) svg('rect', { x: 0, width: x1 + 6, y: yAt(band[1]), height: yAt(band[0]) - yAt(band[1]), class: 'hw-graph__band' }, root_);
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
    // Limits and the band are named at the right end, in the label gutter with the line ends. A narrow plot has no
    // gutter: the names move inside it, at the left, above their line (as in steps).
    const notes = limits.map((l) => {
      svg('line', { x1: 0, x2: x1 + 6, y1: yAt(l.at), y2: yAt(l.at), class: 'hw-graph__limit', 'data-dashed': '', 'data-tone': l.tone }, root_);
      return { at: yAt(l.at) - (endLabels ? 0 : 5), text: limitName(l), tone: l.tone, kind: 'limit' };
    });
    if (band) notes.push({ at: (yAt(band[0]) + yAt(band[1])) / 2 + (endLabels ? 0 : 4), text: bandName(), kind: 'band' });
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
      const where = k === focus && config.highlightWhere; // then the line is context and only its stretches are lit
      svg('path', { d, class: 'hw-graph__line', 'data-on': k === focus && !where ? '' : null, 'data-dashed': s.dashed ? '' : null }, root_);
      if (where) svg('path', { d: stretches(s.values, xAt, yAt, where), class: 'hw-graph__line', 'data-on': '', 'data-dashed': s.dashed ? '' : null }, root_);
      const end = s.values.findLastIndex((v) => !missing(v));
      if (end < 0) return;
      svg('circle', { cx: xAt(end), cy: yAt(s.values[end]), r: 4, class: 'hw-graph__dot', 'data-on': k === focus && lit(s.values[end]) ? '' : null }, root_);
      ends.push({ k, at: yAt(s.values[end]), x: xAt(end), text: fmt(s.values[end]) });
    });
    spread(endLabels ? ends.concat(notes) : notes, y1).forEach((e) => {
      if (e.kind) {
        const name = `hw-graph__${e.kind}-label`;
        svg('text', { x: endLabels ? x1 + 14 : x0, y: e.y, class: endLabels ? name : `${name} ${name}--inside`, 'data-tone': e.tone }, root_).textContent = e.text;
        return;
      }
      const t = svg('text', { x: e.x + 14, y: e.y, class: 'hw-graph__end', 'data-on': e.k === focus ? '' : null }, root_);
      svg('tspan', {}, t).textContent = series[e.k].name;
      svg('tspan', { dx: 6, class: 'hw-graph__end-value' }, t).textContent = e.text;
    });
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
    const marks = [...(config.limits ?? []).map(limitName), ...(config.band ? [bandName()] : [])];
    f.plot.setAttribute('aria-label', `${config.title}.${marks.length ? ` Marked: ${marks.join('; ')}.` : ''} Use the left and right arrow keys to read each point.`);
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
