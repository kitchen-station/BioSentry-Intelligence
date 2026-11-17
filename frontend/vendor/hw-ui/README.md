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
npm test             # Playwright: 102 checks (keyboard, form states, link names, motion, graphs, reduced motion, axe in 5 modes)
```

## Adding a component

1. `src/components/<name>.css` inside `@layer hw.components { … }`. Start with its `--hw-<name>-*`
   tokens pointing at semantic tokens, then base rules, then modifiers, then `@media` rules.
2. Need a new color or size? Add it to `tokens/semantic.css` (and its dark value in `themes.css`).
3. Add the `@import` to `src/index.css` (alphabetical).
4. Behaviour needed? `src/js/<name>.js` exporting `init(scope) → cleanup`, register in `js/index.js`.
5. Add it to `test/gallery.html` with real, accessible markup; add keyboard checks to `test/run.py`.
6. `npm run lint && npm test` must pass. Document the markup contract in the skill.
