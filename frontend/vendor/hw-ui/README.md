# hw-ui

A Huawei-inspired UI kit for our own products: design tokens, one CSS file per component, motion,
graph components, and small dependency-free JavaScript behaviours. Set in HarmonyOS Sans (bundled); icons
come from Font Awesome Free. Desktop and mobile. Light, dark, Cloud-console family and
compact density are token overrides, so components never change for a theme.

**Not included, on purpose:** Huawei's logo, wordmark, slogans, campaign art or photos. Our product mark
goes in every logo slot.

## Use it

```html
<link rel="stylesheet" href="node_modules/@fortawesome/fontawesome-free/css/all.min.css">
<link rel="stylesheet" href="hw-ui/src/index.css">
<script type="module">
  import { initAll, toast } from './hw-ui/src/js/index.js';
  const destroy = initAll();          // call destroy() on SPA route teardown
</script>
```

Themes and variants go on any ancestor (usually `<html>`):

| Attribute | Effect |
|---|---|
| `data-hw-theme="dark"` | Dark theme (contrast-checked) |
| `data-hw-family="cloud"` | Near-black primary, pill buttons (Huawei Cloud console look) |
| `data-hw-density="compact"` | 32px controls instead of 40px |
| *(automatic)* `pointer: coarse` | 44px controls on touch screens |

## Layout

```
src/
  index.css                 layer order + imports (the only file apps link)
  fonts.css                 @font-face for the bundled HarmonyOS Sans
  tokens/
    primitives.css          tier 1: raw values (color ramps, scales). Never used by components.
    semantic.css            tier 2: meaning (--hw-color-text, --hw-control-height…). Components read only these.
    themes.css              dark / cloud / compact / touch: overrides of tier 2 only
  base/                     reset, focus ring, typography, keyframes, reduced motion
  components/<name>.css     one file per component; tier 3 tokens (--hw-<component>-*) at the top
  utilities/layout.css      container, stack, cluster, grid, app shell, show/hide by viewport
  utilities/motion.css      scroll reveal + one-off entrance helpers
  js/
    core/dom.js             shared helpers (on, cleanupAll, uid, placePopup, rovingIndex)
    core/motion.js          leave() / cancelLeave(): exit animations, reduced-motion aware
    <behaviour>.js          select, menu, tabs, dialog, tooltip, slider, reveal, toast — each init() → cleanup
    graph/                  trend, bars, columns, steps, range, share, stat (+ frame.js shared by all)
    index.js                initAll() + public API
fonts/harmonyos-sans/       the font files and their licence, unmodified
docs/graphs.md              graph rules, API, colour roles
test/
  gallery.html              every component, real markup; open with any static server
  run.py                    keyboard tests, axe scan (light/dark/cloud/phone), screenshots
```

## Icons

Font Awesome Free 7 (peer dependency; icons CC BY 4.0, fonts SIL OFL 1.1, code MIT). Keep its licence
comment in the CSS you ship. Wrap glyphs in `.hw-icon` so they share one box and align with text:

```html
<button class="hw-button hw-button--primary"><i class="hw-icon fa-solid fa-bed-pulse" aria-hidden="true"></i>Acknowledge</button>
<button class="hw-button hw-button--icon" aria-label="Settings"><i class="hw-icon fa-solid fa-gear" aria-hidden="true"></i></button>
<span class="hw-icon-disc"><i class="fa-solid fa-heart-pulse" aria-hidden="true"></i></span>
```

Sizes `hw-icon--sm | --lg | --xl`, colours `--brand | --muted`. Icons are always `aria-hidden`; the
button's text or `aria-label` carries the meaning. Tiny CSS-drawn marks (select chevron, accordion
chevron, breadcrumb separator, step check) stay font-independent on purpose, so they never break if the
icon font fails to load.

## Motion

Tokens: `--hw-motion-micro` 150ms, `--hw-motion-exit` 200ms, `--hw-motion-enter` 300ms,
`--hw-motion-reveal` 600ms; `--hw-ease-enter` (decelerate), `--hw-ease-exit` (accelerate), `--hw-ease-move`.
Exits are shorter than entrances.

| What | How |
|---|---|
| Hover / press | colour .2s; buttons, tiles and interactive cards press to 0.97; bottom-nav icons to 0.88 |
| Popups | menus and selects pop in and out; tooltips fade |
| Overlays | dialogs drop in and lift out, backdrop fades; drawers, nav drawers and sheets slide both ways |
| Bottom sheet | swipe its header down to close (touch) |
| Toasts | slide from their edge (top on desktop, bottom on phones) and back |
| Tabs | one indicator slides between tabs (underline, or the pill in `hw-tabs--pill`) |
| Accordion | content height animates (Chrome/Edge 131+, Safari 18.4+; snaps elsewhere) |
| Scroll reveal | `data-hw-reveal` on an element, or `="stagger"` on a parent; only below-the-fold content is hidden |
| One-offs | `.hw-anim-fade-in`, `.hw-anim-fade-in-up`, `.hw-anim-pop-in` for content added later |

How exits work: JS sets `data-hw-leaving`, the component's CSS plays its exit, then the element is hidden.
State (`aria-expanded`, focus, events) changes immediately, so assistive tech and tests never wait on an
animation. Reopening mid-exit cancels it. `prefers-reduced-motion: reduce` turns all of it off, and
reveal never hides anything.

## Type

HarmonyOS Sans is bundled unmodified in `fonts/harmonyos-sans` with its licence, and loaded by
`src/fonts.css` (Light 300, Regular 400, Medium 500 to 600, Bold 700). The HarmonyOS Sans Fonts License
Agreement allows bundling unmodified copies with software and asks that the product say prominently that
it uses the fonts, so put a line such as "Set in HarmonyOS Sans" on an About screen or in the footer, and
keep `LICENSE.txt` beside the files. Do not subset, convert or rename them: the licence does not allow
modified copies. Read the agreement before shipping; this is a summary, not legal advice.

## Graphs

Seven graph components in `src/js/graph/`: `trend`, `bars`, `columns`, `steps`, `range`, `share`, `stat`.
They share one frame and five rules: a readout sentence instead of tooltips, one highlighted mark, labels
on the marks, a title that states the finding, and honest shapes. Each works with a mouse, touch and the
keyboard, in light and dark. Rules, API and colour roles: [docs/graphs.md](docs/graphs.md).

## Forms

- **Native select.** `<select class="hw-native-select">` is a real `<select>` in the kit's look: no wrapper,
  no JavaScript. It sizes to its options, fills a `.hw-field`, and takes `--sm`, `--lg` and `--block`.
  An empty first option (`<option value="">Choose a shift</option>`) reads as a placeholder. Use it when
  the platform picker fits (phones, long lists, plain forms); use `.hw-select` when options need tags or
  search. Do not wrap a `<select>` in `.hw-input`.
- **Invalid.** A field turns red only after the person has used it (`:user-invalid`) or when the app sets
  `aria-invalid="true"`. Empty required fields are never red on load.
- **Disabled.** The grey look follows the control's own `disabled` state. A disabled `<option>` or a
  disabled clear button does not grey the field.
- **Drawn marks.** The chevrons on `.hw-tile`, `.hw-link-cta` and the breadcrumb separators are drawn with
  borders, not typed as "›". Text in generated content is read as part of a link's name ("Ward ›").

## 0.10.0 (BioSentry Phase 1)

Ward components, CSS only: the app owns behaviour and sets state through the attributes below. Each file opens
with its full contract in a comment, and `test/gallery.html` (section "Ward") has working markup for all of them.

| Component | Contract |
|---|---|
| `hw-score` (`score.css`) | `div.hw-score[data-band=high\|medium\|low][role=img][aria-label] > span.hw-score__value + span.hw-score__band`. 60px (54px below 576px); `--sm` 40px with the band beside the tile (leave the band out where the text next to it already says it). "Low-medium" is `data-band="medium"` plus `data-long`: the word breaks after its hyphen onto two lines, inside the tile and beside `--sm`. |
| `hw-filter` (`filter.css`) | `div.hw-filter[role=group][aria-label] > button.hw-filter__item[aria-pressed] > text + span.hw-filter__count[data-tone=emergency\|urgent][data-count]`. A tone shows only above zero. One sideways-scrolling row on phones. |
| `hw-bell`, `hw-alert-tray` (`alert-tray.css`) | `div.hw-menu-anchor > button.hw-bell[aria-label][aria-haspopup=dialog][aria-expanded] > i.hw-icon + span.hw-bell__count` `+ div.hw-alert-tray[role=dialog] > div.hw-alert-tray__head (h2.hw-alert-tray__title + button.hw-close) + div.hw-alert-tray__item* (div.hw-score.hw-score--sm + div.hw-alert-tray__who > p.hw-alert-tray__name + p.hw-alert-tray__line; button.hw-button Open + button.hw-button.hw-button--accent Acknowledge) + p.hw-alert-tray__empty + p.hw-alert-tray__foot`. The bell is tinted while it holds a count. Phones: full-width panel under the 56px app bar, 44px buttons. Exit: `leave(tray)`, then `hidden`. |
| `hw-search` (`search.css`) | `div.hw-search > label.hw-input > i.hw-input__icon + input.hw-input__control[type=search][role=combobox][aria-expanded][aria-controls][aria-activedescendant] + kbd.hw-search__key` `+ ul.hw-search__results[role=listbox] > li.hw-search__caption[aria-hidden] + li[role=option][aria-selected] > span.hw-search__rank + span.hw-search__name + span.hw-search__bed + span.hw-tag` `+ p.hw-search__empty[role=status]`. |
| `hw-segmented--accent` (`segmented.css`) | The selected option in ink (accent) instead of brand red, for products where red is clinical. The default stays brand, as the emphasis rule says. |
| Band tags (`tag.css`) | `hw-tag--emergency` (7+), `hw-tag--urgent` (5–6 or a single 3), plain `hw-tag` (low), `hw-tag--dashed` (NOT RANKED only). |
| `hw-ranklist` (`ranklist.css`) | `section.hw-ranklist-wrap > div.hw-ranklist__head[aria-hidden] + ol.hw-ranklist[data-density=comfortable\|compact] > li.hw-ranklist__row[data-alert]` with rank, score, who (id, response, context, reasons), five vitals (`[data-points=0-3]`), data (age `[data-stale]`, signal `[data-weak]`, obs) and action. Rows from 1200px (tighter columns and 48px sparklines up to 1365px, so the action stays in view at 1280), cards below. Full element list at the top of the file. |
| `hw-notice--dashed` (`notice.css`) | `section.hw-notice.hw-notice--dashed > div.hw-notice__head (h2.hw-notice__title + p.hw-notice__text + button.hw-notice__toggle[aria-expanded]) + div.hw-notice__body > div.hw-notice__item* (div.hw-notice__main + button.hw-button)`. Dashed means incomplete and nothing else. |
| `hw-banner--inverse` (`alert.css`) | `div.hw-banner.hw-banner--inverse[role=alert] > i.hw-icon + strong.hw-banner__title + span`. Offline: black, white text. |

New tokens: `--hw-color-emergency` (NEWS2 7+ fill; red-50 in dark), `--hw-color-on-emergency` (white),
`--hw-target-size` (44px; 40px in compact density, 44px on touch).

### Phase 2 (0.10.0-biosentry.2): Patient

Patient components, CSS only, as in Phase 1; `trend` gains four options. `test/gallery.html` (section "Patient
(0.10 Phase 2)") has working markup for each, and each CSS file opens with its full element list.

| Component | Contract |
|---|---|
| `hw-pager` (`pager.css`) | `div.hw-pager[role=group][aria-label="Move through patients in rank order"] > button.hw-button[type=button] (i.hw-icon.fa-solid.fa-arrow-left[aria-hidden] + "Previous" + kbd.hw-pager__key[aria-hidden=true] "K") + span.hw-pager__position ("1 of 9 in rank order" \| "Not in the ranked order") + button.hw-button[type=button] (kbd.hw-pager__key[aria-hidden=true] "J" + "Next" + i.hw-icon.fa-solid.fa-arrow-right[aria-hidden])`. `disabled` on the button at either end (both when not in the ranked order). The app wires K and J while focus is not in a field. Key hints hide below 768px; below 576px the position sits above two equal buttons. |
| `hw-decision` (`decision.css`) | `section.hw-decision[aria-label="Decision"][data-band=high\|medium\|low]` (no `data-band` when NOT RANKED) `> div.hw-decision__cell` × 4: NEWS2 (`p.hw-decision__label` + `div.hw-decision__reading > span.hw-decision__score + span.hw-decision__meta > span.hw-decision__band` (+ `i.fa-arrow-up` when rising) `+ span.hw-decision__change`); Next step (`p.hw-decision__label + p.hw-decision__step + p.hw-decision__note`, a `strong` in the note for the NOT RANKED reason); Why (`p.hw-decision__label + ul.hw-decision__reasons > li.hw-tag` or `p.hw-decision__text`, `+ p.hw-decision__model > i.hw-icon.fa-solid.fa-flask + span > strong "Prediction models · rank unchanged." + text`); Signal (`p.hw-decision__label + p.hw-decision__signal + p.hw-decision__note[data-stale]`). The band colours the score, the band word and the next step: high emergency text, medium (and Low-medium) urgent text, low and NOT RANKED ink. A stale source is red and bold. Subtle lines between cells; one row from 1200px, two by two from 576px, stacked on phones; a cell left out leaves no gap. |
| `hw-actionbar` (`actionbar.css`) | `div.hw-actionbar.hw-only-mobile > button.hw-button.hw-button--primary + button.hw-button + button.hw-button` (Record vitals · Notes · Next). The first action takes two shares of the width; 48px buttons; sticky to the bottom of the viewport, padded with the safe-area inset; surface with a top border. **Rule:** it replaces the bottom tab bar on a detail screen. A screen shows `.hw-actionbar` or `.hw-bottomnav`, never both (drop `.hw-has-bottomnav` too). Put it last in the screen. |
| `trend` (`js/graph/trend.js`) | New options, all optional; without them a trend draws exactly as before. `range: [lo, hi]` fixed y-range (ticks at its ends; values outside drawn on the edge, real values in the readout and table). `limits: [{ at, label?, tone? }]` dashed lines named at their right end. `band: [lo, hi]` + `bandLabel` a shaded usual range, named. `highlightWhere: (v) => boolean` only the stretches, dots and readout number where it holds take the highlight colour; the rest of the line is grey. Below 480px of plot the names move inside it. API and example: [docs/graphs.md](docs/graphs.md#trend-clinical-options). |
| Highlight by space (`themes.css`) | `data-hw-space="clinical"` sets `--hw-graph-highlight` to ink (`--hw-color-text`), `data-hw-space="research"` to `--hw-color-info-fg`; dark values follow the theme (`#F3F3F3`, `#9DBDFC`). The Patient trends set no space, so their `highlightWhere` stretch stays red. |

New tokens (component tier, pointing at semantic tokens): `--hw-pager-position-width`; `--hw-decision-band-fg`,
`-bg`, `-line`, `-cols`, `-pad`, `-score-size`, `-step-size`; `--hw-actionbar-pad-y`, `-pad-x`, `-gap`, `-bg`,
`-border`, `-button-height`. No new semantic tokens; the spaces override `--hw-graph-highlight`.

### Phase 3 (0.10.0-biosentry.3): Census and Admit

One new component, CSS only, and one modifier. Census also uses `columns` (with `data-hw-space="clinical"`, so its
highlight is ink), `stat`, `filter` and `table`; Admit uses `steps`, `field`, `native-select`, `segmented--accent`,
`checkbox`, `upload` and `tag`. `test/gallery.html` (section "Census (0.10 Phase 3)") has working markup for both.

| Component | Contract |
|---|---|
| `hw-timeline-beds` (`timeline-beds.css`) | `div.hw-timeline-beds > ul.hw-timeline-beds__legend[aria-label="Legend"] > li.hw-timeline-beds__key (span.hw-timeline-beds__swatch "On the ward" \| span.hw-timeline-beds__mark[data-outcome] + name)` `+ div.hw-timeline-beds__scroll[tabindex=0][role=region][aria-label] > div.hw-timeline-beds__chart > div.hw-timeline-beds__row* (span.hw-timeline-beds__bed + div.hw-timeline-beds__track > span.hw-timeline-beds__stay[role=img][aria-label][data-outcome][style="--hw-timeline-beds-left: 12%; --hw-timeline-beds-width: 30%"] > span.hw-timeline-beds__name + span.hw-timeline-beds__mark[data-outcome][aria-hidden])` `+ div.hw-timeline-beds__axis[aria-hidden] > span.hw-timeline-beds__tick[style="--hw-timeline-beds-left: …"]* + span.hw-timeline-beds__now`. The window ends now (the track's right edge, ruled in ink). A stay with no `data-outcome` is on the ward: a solid ink bar (ink is the highlight; never red). An ended stay (`data-outcome=discharged\|transferred\|higher_care\|died`) is soft grey, closed by its mark: D in a box, T in a dashed box, H in a filled box, a filled disc for died. The kit draws the letters, so marks are always `aria-hidden`; the stay's label and the legend (all five) carry the outcome. The chart keeps 720px (640px below 576px) and scrolls sideways in its region. |
| `hw-steps--accent` (`steps.css`) | The current step in ink instead of brand red, for products where red is clinical (as `hw-segmented--accent`). |

New tokens (component tier): `--hw-timeline-beds-bed-width`, `-row-height`, `-bar-height`, `-min-width`, `-open-bg`,
`-open-fg`, `-ended-bg`, `-ended-fg`, `-rule`, `-now`, `-mark-size`, and per mark `-mark-bg`, `-mark-fg`, `-mark-border`,
`-mark-style`, `-mark-radius`; per stay `-left` and `-width` (set by the app). No new semantic tokens.

### Phase 4 (0.10.0-biosentry.4): Research

`bars` gains two options; nothing else changes. `test/gallery.html` (section "Research (0.10 Phase 4)", inside
`data-hw-space="research"`) draws both.

| Component | Contract |
|---|---|
| `bars` (`js/graph/bars.js`) | `diverging: true` puts a labelled zero line in the middle (`zeroLabel`, bold; optional `sides: [loss, gain]` under it): losses run left of it, gains right, on a symmetric scale; the graph carries `data-diverging`, each track `data-sign=positive\|negative`. Per item, `interval: [lo, hi]` draws `span.hw-graph__interval[aria-hidden]`, an ink whisker with end caps (haloed in the surface), and the value moves past its far end; `intervalLabel` names it in the readout ("98.33% interval +0.008 to +0.029"). Both optional: an old config draws exactly as before. Below 576px a diverging graph puts each name above its bar and keeps only the scale's ends and zero. API: [docs/graphs.md](docs/graphs.md#bars-diverging-and-intervals). |

New tokens: none. Component custom properties set by the script: `--hw-graph-start` (a bar's or whisker's left edge),
`--hw-graph-label` (where the value sits), `--hw-graph-bar-radius`.

## Rules (enforced by `npm run lint`)

1. **Cascade layers** `hw.tokens → hw.base → hw.components → hw.utilities`. Later layers win regardless of
   specificity, so no specificity wars and no `!important` (two documented exceptions: `[hidden]` and the
   show/hide utilities; reduced-motion lives outside layers).
2. **BEM with prefix:** `.hw-block`, `.hw-block__element`, `.hw-block--modifier`. Lint rejects anything else.
3. **No raw colors in components, base or utilities.** Hex, `rgb()`, named colors fail lint. Add a semantic
   token instead; that is what makes dark mode and families free.
4. **State comes from the platform, not classes:** `:checked`, `:disabled`, `[aria-expanded]`,
   `[aria-selected]`, `[aria-current]`, `[aria-invalid]`, `[open]`, `[hidden]`, `[data-status]`.
5. **Modifiers only reassign component tokens** (`.hw-button--primary { --hw-button-bg: … }`), never
   restyle properties. One place to read what a variant changes.
6. **No `transition: all`.** List properties (lint-enforced).
7. **Native first:** `<dialog>` (focus trap, Esc, top layer), `<details>` (accordion), `<progress>`,
   `<input type=range>`, real radios/checkboxes behind custom visuals. JS only where HTML has no element.
8. **Responsive rules live in the component file**, mobile-first, at the shared breakpoints
   576 / 768 / 992 / 1200 / 1440.
9. **JS:** ES modules, no dependencies, event delegation on `document`, `init()` returns a cleanup
   function, components announce changes with `CustomEvent`s (`hw:change`, `hw:select`, `hw:tabchange`),
   `textContent` only (`innerHTML` is lint-banned).

## Commands

```bash
npm install          # dev tools + Font Awesome Free (the only runtime peer dependency; the font is bundled)
npm run lint         # CSS architecture rules + JS lint
npm test             # Playwright: 173 checks (keyboard, form states, link names, motion, graphs, reduced motion, axe in 5 modes)
```

## Adding a component

1. `src/components/<name>.css` inside `@layer hw.components { … }`. Start with its `--hw-<name>-*`
   tokens pointing at semantic tokens, then base rules, then modifiers, then `@media` rules.
2. Need a new color or size? Add it to `tokens/semantic.css` (and its dark value in `themes.css`).
3. Add the `@import` to `src/index.css` (alphabetical).
4. Behaviour needed? `src/js/<name>.js` exporting `init(scope) → cleanup`, register in `js/index.js`.
5. Add it to `test/gallery.html` with real, accessible markup; add keyboard checks to `test/run.py`.
6. `npm run lint && npm test` must pass. Document the markup contract in the skill.
