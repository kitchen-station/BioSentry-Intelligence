# Graphs

`src/js/graph/` + `src/components/graph.css` (+ `stat.css`). No dependencies. Light and dark.

## Five rules every graph follows

1. **A sentence, not a tooltip.** Above the plot sits a big number and a plain sentence (the readout).
   Pointing, tapping or using the arrow keys changes the sentence. Nothing floats over the data, and it
   behaves the same with a mouse, a finger and a keyboard.
2. **One red thing.** The mark the reader should look at is the highlight colour. Everything else is grey.
   With `highlightWhere`, a trend lights only the stretch of its highlighted line that passes the test (in the
   Patient trends, the stretch adding NEWS2 points). Where red is clinical, a page sets the highlight colour by
   space (see Colour roles).
3. **Labels on the marks.** Names and values sit at the ends of lines and bars. The key buttons above a
   Trend are both the legend and the control that picks the highlighted line.
4. **The title states the finding.** "Both models catch more deteriorations than NEWS2 alone", not
   "Deteriorations by method". The subtitle says what was measured.
5. **Honest shapes.** Bars start at zero. Scores taken at set times are flat steps, never slopes. A missing
   reading is a visible gap, never bridged.

## Which graph

| The data | Use | Notes |
|---|---|---|
| Lines over time, up to 4 series | `trend` | `dashed: true` tells two context lines apart; `range`, `limits`, `band` and `highlightWhere` for a vital sign |
| A few things compared | `bars` | `baseline` makes the readout say "21 points more than …" |
| One measure over time | `columns` | highlighted column is read against the average |
| A score taken at set times | `steps` | `limits` name the thresholds; `null` is a gap |
| A value against its usual range | `range` | one row per measure, each with its own little axis |
| Parts of a whole | `share` | one bar instead of a pie; up to five parts, largest first |
| One key number | `stat` | number, sparkline, and a change line with an arrow |

No pies, no donuts, no dual axes, no stacked areas. Two measures on different scales are two graphs.

## API

Every function takes `(element, config)` and returns `{ destroy() }` (`trend` also has `update(config)`).
Shared config: `title`, `subtitle`, `unit` (appended to numbers), `format(v)` (takes over number
formatting), `hint` (the line under the plot), `level` (heading level, default 3).

```js
import { trend, bars, columns, steps, range, share, stat } from 'hw-ui';

trend(el, { title, subtitle, x: ['Week 1', …], xLabel: 'Week',
            series: [{ name, values: [0.42, …], dashed: false }, …], highlight: 'Checked student' });
            // + range, limits, band, bandLabel, highlightWhere: see "Trend: clinical options" below

bars(el, { title, subtitle, items: [{ name, value }, …], highlight: 2, baseline: 0, unit: '%', max: 100,
           label: (item) => `flagged early by ${item.name}` });

columns(el, { title, subtitle, x: ['3 Nov', …], values: [9, 7, …], averageLabel: '14-day average',
              noun: ['alert', 'alerts'] });

steps(el, { title, subtitle, x: ['03', …], times: ['03:00', …], values: [2, 2, null, 4, …], max: 9,
            limits: [{ at: 5, label: 'Urgent review', tone: 'urgent' }, { at: 7, label: 'Emergency', tone: 'emergency' }] });

range(el, { title, subtitle, items: [{ label: 'Breathing', value: 22, unit: ' /min', usual: [14, 18], scale: [8, 30] }, …] });

share(el, { title, subtitle, parts: [{ name, value }, …], noun: 'alerts',
            label: (part) => `of alerts came from ${part.name.toLowerCase()}` });

stat(el, { label, value: '0.13', values: [0.20, …], change: { direction: 'down', text: 'Down 36% in six weeks' }, note });
```

### Trend: clinical options

All optional. Without them a trend draws exactly as before (zero-based scale, no reference marks).

| Option | Type | What it does |
|---|---|---|
| `range` | `[lo, hi]` | A fixed y-range in place of the zero-based nice scale, ticked at its two ends (the bottom is a gridline, not a zero baseline, unless `lo` is 0). A value outside it is drawn on the nearest edge; the readout and the table keep the real value. `max` is ignored. |
| `limits` | `[{ at, label?, tone? }]` | Dashed horizontal lines, each named at its right end in the label gutter (`label`, or the formatted value). Grey by default; `tone: 'urgent' \| 'emergency'` colours the line and its name, the one place text wears a mark colour. A limit outside `range` is not drawn. Without `range`, the scale grows to include the limits. |
| `band` | `[lo, hi]` | A shaded band (`--hw-graph-band` at half strength, under the lines) for a usual range, named at its right end. |
| `bandLabel` | string | The band's name; default `Usual lo–hi`. |
| `highlightWhere` | `(v) => boolean` | Only where it holds does the highlighted line take the highlight colour: its stretches (cut exactly where a segment crosses), its dots and the readout number. The rest of the line is context grey. A JS-only option. |

The gutter is wide enough for the longest name. Below 480px of plot (phones) there is no gutter: the names move
inside the plot, at the left, above their line, with a surface halo so a line can pass behind them. The plot's
accessible name lists the limits and the band ("Marked: +1 point above 90; Own usual 72–88."); the readout and
"Show as a table" work as before, and `null` is still a gap.

```js
// Heart rate on the Patient page: clinical y-range, the NEWS2 thresholds, the patient's own usual range, red only
// where the reading adds points. NEWS2 heart rate: 51–90 scores 0, 91–110 scores 1, 111–130 scores 2, 41–50 scores 1.
trend(el, {
  title: 'Heart rate has added NEWS2 points since 08:30',
  subtitle: 'Santos, M., bed 4A. Every 15 minutes for six hours, against her own usual range',
  x: ['05:15', '05:30', …, '11:00'], xLabel: 'Time', unit: ' bpm',
  series: [{ name: 'Heart rate', values: [78, 80, …, null, …, 116, 118] }],
  range: [40, 140],
  limits: [{ at: 50, label: '+1 point at 50 or below' }, { at: 90, label: '+1 point above 90' }, { at: 110, label: '+2 points above 110' }],
  band: [72, 88], bandLabel: 'Own usual 72–88',
  highlightWhere: (v) => v > 90 || v <= 50,
});
// The Patient frames' other two: RR range [8, 30], limits 12 and 20, highlightWhere (v) => v > 20 || v < 12;
// SpO2 range [85, 100], limit 96, highlightWhere (v) => v < 96 (scale 1).
```

Declarative, picked up by `initAll()` (no function options):

```html
<div data-hw-graph="trend"><script type="application/json">{ "title": "…", "x": […], "series": […] }</script></div>
```

Put a graph on any surface (a `.hw-card`, a dialog, the page). The graph brings no box of its own.

## Keyboard and screen readers

- `trend`: the plot is one tab stop. Left and right arrows, Home and End move the reading; the readout is
  a polite live region. "Show as a table" swaps the plot for a real table of every value. With `limits` or `band`,
  the plot's name lists them.
- `bars`, `columns`, `share`: the marks are buttons in one group with a single tab stop; arrow keys move
  the highlight (`aria-pressed`).
- `steps`: the plot is an image whose label lists every reading; the values are also printed under it.
- `range`: a list; each row says its value and how far outside its usual range it is, in words.

## Colour roles (tokens in `tokens/semantic.css`, dark values in `themes.css`)

| Role | Token | Light | Job |
|---|---|---|---|
| Highlight | `--hw-graph-highlight` | `#C7000B` | the one thing to look at |
| Context | `--hw-graph-context`, `-strong`, `-soft` | `#939393`, `#595959`, `#C9C9C9` | everything else |
| Limits | `--hw-graph-urgent`, `--hw-graph-emergency` | `#F4840C`, `#C7000B` | thresholds and states, always with a word |
| Band | `--hw-graph-band` | ink at 18% | a usual range (a trend draws it at half strength, under its lines) |
| Ink | `--hw-graph-baseline`, `--hw-graph-grid` | `#191919`, `#DFDFDF` | baselines, gridlines |
| Compare | `--hw-graph-compare-1…4` | `#0A59F7`, `#F4840C`, `#715AFB`, `#09AA71` | only when every series matters equally |

**Highlight colour by space.** Red is the default highlight. In a product where red is clinical (NEWS2 7+, a value
adding points), set the space on the page or on any ancestor of the graphs:

| Attribute | Highlight | Light | Dark | Use |
|---|---|---|---|---|
| *(none)* | `--hw-color-brand` | `#C7000B` | `#EE696F` | the Patient trends, where the only highlight is the stretch adding points (`highlightWhere`) |
| `data-hw-space="clinical"` | `--hw-color-text` (ink) | `#191919` | `#F3F3F3` | clinical analysis pages (Census) |
| `data-hw-space="research"` | `--hw-color-info-fg` | `#1F55B5` | `#9DBDFC` | the research space |

The rules live in `tokens/themes.css` and point at semantic tokens, so the dark values follow the theme.

The compare set is for a future multi-colour graph; none of the seven uses it today. It was checked with
the data-viz palette validator for colour-blind readers in this order, light (`#FFFFFF`) and dark
(`#191919`, using `#3979F9`, `#D9720A`, `#8F7DFC`, `#09AA71`). The light orange is under 3:1 on white, so
anything drawn in it must also carry a label. Do not reorder it or add a fifth colour without re-checking.

## Type

Graphs are set in HarmonyOS Sans (bundled in `fonts/harmonyos-sans`, loaded by `src/fonts.css`): Light
for the big number, Medium for labels, Bold for values on marks.
