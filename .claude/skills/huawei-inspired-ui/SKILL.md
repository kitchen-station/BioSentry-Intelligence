---
name: "huawei-inspired-ui"
description: "Build our product UI with hw-ui, our Huawei-inspired kit (tokens, components, motion, icons, graph components, HarmonyOS Sans). Use for any screen, component, graph, theme or layout work in that style."
---

# hw-ui: Huawei-inspired UI kit

hw-ui is our own UI kit in the visual language of Huawei's websites (huawei.com, Huawei Cloud,
Consumer, Enterprise, Talent, ICT Competition), extracted from their live CSS in Nov 2026 and improved
for accessibility. It is a package of real files: **tokens → one CSS file per component → small JS
modules**, with lint rules and tests that keep it that way.

## 0. Before writing any UI

1. **Find the package.** Look for `hw-ui/src/index.css` (or a `hw-ui` dependency) in the project. Use it.
   In BioSentry it is vendored at `frontend/vendor/hw-ui` (version `0.10.0-biosentry.N`); its README's
   "0.10.0 (BioSentry Phase 1)" and "Phase 2/3/4" parts hold the ward, patient, census and research
   contracts that this file summarises in section 5a.
   Never re-implement a component inline or paste kit CSS into app files.
2. **If it isn't there**, ask the user for the `hw-ui.zip` package (or the repo it was committed to) and
   add it to the project. Don't regenerate the kit from memory: that drifts from the tested source.
3. **Compose, don't override.** Screens are built from kit classes and layout utilities. If a screen
   needs something the kit lacks, add a component to the kit (section 8), not a one-off style.

## 1. Boundaries

- **Our product keeps its own identity.** Never use Huawei's logo, the HUAWEI wordmark, product names as
  branding, slogans ("I. C. The Future"), campaign art or Huawei photos. Our product mark goes in every
  logo slot. Nothing may look like an official Huawei page or flow (sign-in, support, store).
- Naming technologies in text is fine ("Built on Huawei Cloud", "Trained with MindSpore"). Official
  "Powered by Huawei Cloud" marks only via Huawei's partner program and its guidelines.
- Fonts: HuaweiSans is proprietary; never use it. The kit bundles HarmonyOS Sans unmodified in
  `fonts/harmonyos-sans` with its licence. That licence asks the product to say prominently that it uses
  the fonts ("Set in HarmonyOS Sans" in a footer or About screen) and forbids modified copies, so never
  subset, convert or rename the files.

## 2. Architecture rules (lint-enforced: `npm run lint`)

| Rule | Why |
|---|---|
| Cascade layers `hw.tokens → hw.base → hw.components → hw.utilities` | Later layers win regardless of specificity: no specificity wars, no `!important`. |
| BEM with prefix: `.hw-block`, `.hw-block__element`, `.hw-block--modifier` | Predictable names; lint rejects others. |
| Components read **semantic tokens only**; hex, `rgb()`, named colors fail lint in components/base/utilities | Themes, dark mode and families then cost nothing. |
| Three token tiers: primitives (raw) → semantic (meaning) → component (`--hw-<name>-*`) | Change a value once; variants are readable. |
| Modifiers only reassign component tokens (`.hw-button--primary { --hw-button-bg: … }`) | One place shows what a variant changes. |
| State from the platform: `:checked`, `:disabled`, `[aria-expanded]`, `[aria-selected]`, `[aria-current]`, `[aria-invalid]`, `[open]`, `[hidden]`, `[data-status]` | Styling and accessibility can't drift apart. |
| No `transition: all`; list properties | Performance and intent (lint-enforced). |
| Native elements first: `<dialog>`, `<details>`, `<progress>`, `<input type=range>`, real radios/checkboxes | Free focus traps, Esc, semantics. JS only where HTML has no element. |
| Responsive rules live **inside the component file**, mobile-first | A component is complete in one file. |
| JS: ES modules, zero deps, delegation on `document`, `init()` returns cleanup, `CustomEvent`s out, `textContent` only (`innerHTML` lint-banned) | SPA-safe, testable, XSS-safe. |
| State classes are banned (`is-open`, `is-active`): use attributes (`[data-hw-leaving]`, `[data-entering]`) | Lint enforces BEM; attributes stay queryable in tests. |
| Durations and easings only via motion tokens (`--hw-motion-*`, `--hw-ease-*`) | One file retunes the feel of the whole kit. |
| Fields turn red via `:user-invalid` or `aria-invalid="true"`, never plain `:invalid` | `:invalid` marks every empty required field red before anyone has typed. |
| `:has()` state checks name the control (`:has(.hw-input__control:disabled)`), never a bare `:has(:disabled)` | A bare check also matches a disabled `<option>` or a disabled inner button. |
| No text glyphs ("›") in `::before` / `::after` on links and buttons; draw marks with borders and `content: ""` | Generated text is read as part of the accessible name ("Ward ›"). |

```
hw-ui/
  src/index.css               @layer order + @imports; the only CSS file apps link
  src/fonts.css               @font-face for the bundled HarmonyOS Sans (300, 400, 500–600, 700)
  src/tokens/primitives.css   tier 1 raw values (never used by components)
  src/tokens/semantic.css     tier 2 meaning; the only tokens components read
  src/tokens/themes.css       dark · cloud family · compact density · touch: tier-2 overrides only
  src/base/                   reset, focus ring, typography, keyframes, reduced motion
  src/components/<name>.css   one file per component (38, incl. icon.css, graph.css, stat.css, native-select.css)
  src/utilities/layout.css    container, stack, cluster, grid, app shell, show/hide
  src/utilities/motion.css    scroll reveal + entrance helpers
  src/js/<behaviour>.js       select, menu, tabs, dialog, tooltip, slider, reveal, toast
  src/js/graph/               trend, bars, columns, steps, range, share, stat (+ frame.js shared by all)
  src/js/core/                dom.js (helpers), motion.js (leave/cancelLeave exit animations)
  fonts/harmonyos-sans/       the font files and LICENSE.txt, unmodified
  docs/graphs.md              graph rules, API and colour roles
  test/gallery.html           every component with real markup
  test/run.py                 keyboard, form states, link names, motion, graphs, reduced motion, axe in 5 modes, screenshots (173 checks in BioSentry's copy)
```

## 3. Setup

```html
<html lang="en" data-hw-theme="light">   <!-- optional: data-hw-theme="dark" data-hw-family="cloud" data-hw-density="compact" -->
<link rel="stylesheet" href="/node_modules/@fortawesome/fontawesome-free/css/all.min.css">  <!-- icons (peer dep) -->
<link rel="stylesheet" href="/hw-ui/src/index.css">
<script type="module">
  import { initAll, toast, openDialog, closeDialog, trend, bars, columns, steps, range, share, stat } from '/hw-ui/src/js/index.js';
  const destroy = initAll();   // SPA: call destroy() on teardown
</script>
```

| Variant | How | What changes |
|---|---|---|
| Corporate (default) | none | Red primary, 6px controls, 16px cards |
| Cloud console | `data-hw-family="cloud"` | Near-black primary, pill buttons. Use for dashboards and data apps. |
| Compact | `data-hw-density="compact"` | 32px controls |
| Dark | `data-hw-theme="dark"` | Contrast-checked dark palette; red text becomes #ee696f / #f59297 |
| Touch | automatic on `pointer: coarse` | 44px controls (sm 36, lg 52) |

For BioSentry: `data-hw-family="cloud"` + the app shell (section 6).

## 4. Tokens

Use semantic tokens in app code too: `var(--hw-color-brand)`, never `#c7000b`.

| Group | Tokens |
|---|---|
| Surfaces | `--hw-color-canvas` `-surface` `-surface-subtle` `-surface-inverse` `-overlay` `-fill-hover` `-fill-disabled` |
| Text | `--hw-color-text` (#191919) `-text-muted` (#595959, 7:1) `-text-subtle` (#767676, 4.5:1) `-text-inverse` `-text-on-brand` |
| Border | `--hw-color-border` (#c9c9c9) `-border-strong` (#191919) `-border-subtle` (#dfdfdf) |
| Brand / emphasis | `--hw-color-brand` (#c7000b) `-brand-hover` (#e02128) `-brand-active` (#850f12) `-brand-subtle` (#fee7e8) `-brand-text` |
| Accent / selection | `--hw-color-accent` (#191919) `-accent-hover` (#595959) `-on-accent` |
| Primary button | `--hw-color-primary` `-primary-hover` `-primary-active` `--hw-color-on-primary` (= brand; = accent in Cloud family) |
| Links / focus | `--hw-color-link` (#0a59f7) `-link-hover` `-focus`; `--hw-focus-ring` |
| Status | `--hw-color-{info,success,warning,danger}-{fg,bg,border}`, `--hw-color-danger`, `--hw-color-success` |
| Type | `--hw-font-sans` (HarmonyOS Sans) `--hw-font-mono` `--hw-text-body` (14) `--hw-text-caption` (12) `--hw-line-height` (1.5); weights `--hw-weight-light` 300 · `-regular` 400 · `-medium` 500 · `-semibold` 600 · `-bold` 700 |
| Graphs | `--hw-graph-highlight` (brand red) · `--hw-graph-context` `-context-strong` `-context-soft` (greys) · `--hw-graph-grid` `-baseline` `-band` `-surface` · `--hw-graph-urgent` `-emergency` (+ `-text`) · `--hw-graph-compare-1…4` |
| Size / shape | `--hw-control-height` (40) `-sm` (32) `-lg` (48); `--hw-control-radius` (6) `--hw-button-radius` `--hw-card-radius` (16) `--hw-popup-radius` (8) |
| Elevation | `--hw-elevation-popup` `-raised` `-overlay` `-dialog` `-knob` |
| Layers | `--hw-z-sticky` 50 · `-dropdown` 100 · `-popup` 1000 · `-modal` 1100 · `-toast` 1200 |
| Primitives (tokens files only) | `--hw-red-05…90`, `--hw-gray-0…100`, blue/mint/orange/yellow ramps, `--hw-space-0…9` (0,2,4,8,12,16,24,32,48,64), `--hw-radius-*`, `--hw-font-size-*`, `--hw-shadow-1…5`, `--hw-duration-1…5` (150–600ms), `--hw-ease-*` |

Selection controls use **accent** (near-black); **brand red** marks emphasis (primary button, active tab
underline, rate, segmented choice, badges, current step). Keep that split.

## 5. Components: markup contracts

Use exactly these structures; the CSS and JS depend on them. Sizes: `--sm` / `--lg` modifiers.

**Buttons:** `<button class="hw-button hw-button--primary">` · variants `--primary --accent --secondary --ghost --danger --pill --icon --block` · `<a class="hw-link-cta">Learn more</a>` (red text + a drawn chevron that nudges on hover) · `<div class="hw-button-group" role="group">`.

**Field + input:**
```html
<div class="hw-field">
  <label class="hw-field__label" for="x">Label<span class="hw-field__required" aria-hidden="true">*</span></label>
  <div class="hw-input"><span class="hw-input__icon" aria-hidden="true"><i class="hw-icon fa-solid fa-magnifying-glass" aria-hidden="true"></i></span>
    <input class="hw-input__control" id="x" placeholder=" " aria-describedby="x-help" aria-invalid="false">
    <button class="hw-input__clear" type="button" aria-label="Clear"><i class="hw-icon fa-solid fa-xmark" aria-hidden="true"></i></button></div>
  <span class="hw-field__help" id="x-help">Help</span>   <!-- or .hw-field__error when aria-invalid="true" -->
</div>
```
**Native select** (no JS, no wrapper): `<select class="hw-native-select" id="shift" name="shift" required>` placed in a `.hw-field` right after its `<label>`. Modifiers `--sm`, `--lg`, `--block` (full width outside a field). An empty first option (`<option value="">Choose a shift</option>`) reads as the placeholder. Never wrap a `<select>` in `.hw-input`. Use it for plain forms, long lists and phones; use the custom Select below when options need tags or search.

Field states: a field is red only after the person has used it (`:user-invalid`) or when the app sets `aria-invalid="true"`; empty required fields are never red on load. The grey disabled look follows the control's own `disabled`, so a disabled `<option>` does not grey the field.

Textarea: `.hw-textarea > textarea.hw-textarea__control + .hw-textarea__count[data-over]`. Number: `.hw-number[role=group] > button.hw-number__step + input.hw-number__value[type=number] + button.hw-number__step`.

**Select** (js/select.js; emits `hw:change {value,label}`):
```html
<div class="hw-select" data-hw-select>
  <input type="hidden" name="ward" value="w4">
  <button type="button" class="hw-select__trigger" aria-haspopup="listbox" aria-expanded="false" aria-labelledby="lbl val"><span id="val">Ward W4</span></button>
  <ul class="hw-listbox" role="listbox" aria-labelledby="lbl" hidden>
    <li class="hw-listbox__option" role="option" data-value="w4" aria-selected="true">Ward W4</li>
  </ul>
</div>
```
Keyboard: ↓/↑ open and move, Home/End, type-ahead, Enter/Space choose, Esc close, Tab leaves. Multi-select trigger must be a `div role=combobox tabindex=0` holding `.hw-chip`s (`.hw-chip__label` + `button.hw-chip__remove`), never a `<button>` (buttons can't nest).

**Menu** (js/menu.js; emits `hw:select {action}`): `.hw-menu-anchor > button[aria-haspopup=menu][aria-expanded][data-hw-menu-trigger] + .hw-menu[role=menu][hidden] > button.hw-menu__item[role=menuitem][data-action]` · `.hw-menu__separator[role=separator]` · `.hw-menu__item--danger` · `.hw-menu[data-align=end]`.

**Choice controls** (real inputs, custom visuals):
- Checkbox: `label.hw-checkbox > input.hw-checkbox__input[type=checkbox] + span.hw-checkbox__box + text` (set `.indeterminate` from JS for "some").
- Radio: `label.hw-radio > input.hw-radio__input[type=radio] + span.hw-radio__dot + text`, grouped in a `fieldset` with a `legend`.
- Switch: `label.hw-switch(--brand|--sm) > input.hw-switch__input[type=checkbox][role=switch] + span.hw-switch__track + text`. Instant-apply settings only.
- Segmented: `fieldset.hw-segmented > label > input.hw-segmented__input[type=radio] + span.hw-segmented__option` (2–6 options; equal-width row on phones).
- Slider (js/slider.js): `.hw-slider-field > input.hw-slider(--brand)[type=range][id] + output.hw-slider-field__value[for]`; optional `.hw-slider-field__ticks`.
- Rate: `fieldset.hw-rate > label > input.hw-rate__input[type=radio] + span.hw-rate__item` (numbered squares).
- Upload: `label.hw-upload > input[type=file].hw-visually-hidden + .hw-upload__title + .hw-upload__browse`; set `[data-dragover]` while dragging; file rows `.hw-file`.

**Feedback:**
- Alert: `.hw-alert(--success|--warning|--danger) > .hw-alert__icon[aria-hidden] + .hw-alert__body > .hw-alert__title`; `role="alert"` only for urgent ones.
- Banner: `.hw-banner` (page-wide notice) with `.hw-banner__close`.
- Toast: `toast({ message, tone: 'success'|'danger'|'warning'|'info', icon?, action: { label, onClick }, timeout })`. One `role=status` region; bottom on phones; pauses on hover/focus.
- Progress: `<progress class="hw-progress(--success|--lg)" max="100" value="40" aria-labelledby>`; `.hw-ring[style="--hw-ring-value:65"][role=img][aria-label]`; `.hw-spinner[role=status][aria-label]`; `.hw-loading-bar[aria-hidden]`; `.hw-skeleton(--title)[aria-hidden]`.
- Tooltip: any element with `data-hw-tooltip="text"` (hover + focus, 300ms, Esc). Popover (static rich panel): `.hw-popover > .hw-popover__title`.

**Overlays** (all native `<dialog>`; js/dialog.js): open with `data-hw-open="id"` or `openDialog(id)`, close with `data-hw-close`, Esc, backdrop or `await closeDialog(el)` (animates out first).
```html
<dialog class="hw-dialog hw-dialog--sm" id="discharge" aria-labelledby="t">
  <div class="hw-dialog__header"><h2 class="hw-dialog__title" id="t">Discharge patient?</h2><button class="hw-close" data-hw-close aria-label="Close"><i class="hw-icon fa-solid fa-xmark" aria-hidden="true"></i></button></div>
  <div class="hw-dialog__body">…</div>
  <div class="hw-dialog__footer"><button class="hw-button" data-hw-close>Cancel</button><button class="hw-button hw-button--primary" data-hw-close value="confirm">Discharge</button></div>
</dialog>
```
Variants: `--sm` 400 · default 550 · `--lg` 700 · `--xl` 900 · `--drawer` (right side panel) · `--nav` (left navigation drawer) · `--sheet` (bottom sheet with grabber). On phones (<576px), plain dialogs go full-screen with stacked full-width buttons. Name destructive actions ("Discharge"), never "OK".

**Navigation:**
- Tabs (js/tabs.js; `hw:tabchange`): `.hw-tabs(--pill)[data-hw-tabs] > .hw-tabs__list[role=tablist][aria-label] > button.hw-tabs__tab[role=tab][aria-selected][aria-controls]` + `.hw-tabs__panel[role=tabpanel][aria-labelledby][tabindex=0][hidden]`. ←/→/Home/End, roving tabindex, scrolls horizontally on phones.
- Breadcrumb: `nav[aria-label=Breadcrumb] > ol.hw-breadcrumb > li > a | span[aria-current=page]`.
- Pagination: `nav[aria-label=Pagination] > ul.hw-pagination > li > button.hw-pagination__item[aria-current=page]` + `.hw-pagination__ellipsis`. Phones keep prev / current / next.
- Steps (the progress stepper, not the `steps` graph): `ol.hw-steps > li.hw-steps__step[data-status=done|current|error]` (+ `aria-current=step`). Vertical on phones.
- Side nav: `nav.hw-sidenav > p.hw-sidenav__group + a.hw-sidenav__link[aria-current=page] > icon + span.hw-sidenav__label`. Collapses to a 64px icon rail below 992px.
- Desktop header: `.hw-header > .hw-header__utility + .hw-header__bar > a.hw-header__mark(OUR mark) + nav > ul.hw-header__nav > li > a.hw-header__link[aria-current]`. The utility row scrolls away; the frosted bar sticks.

**Data display:**
- Table: `.hw-table-wrap > table.hw-table(--stack)` with `caption`, `th[scope=col]`, `.hw-table__num` for numbers, `tr[aria-selected=true]`. With `--stack`, every `td` needs `data-label` and rows become cards below 768px.
- Tag: `.hw-tag(--info|--success|--warning|--danger|--round|--lg)`. Badge: `.hw-badge > [anchor] + .hw-badge__dot | .hw-badge__count` (visually-hidden "new" text for screen readers).
- Avatar: `.hw-avatar(--sm|--lg|--self)[role=img][aria-label]`, stack in `.hw-avatar-stack`.
- Accordion: `.hw-accordion > details.hw-accordion__item > summary.hw-accordion__summary + .hw-accordion__body`.
- Stat: build with `stat(el, config)` (section 7b). Markup it makes: `.hw-stat > .hw-stat__label + .hw-stat__main (.hw-stat__value + svg.hw-stat__spark) + .hw-stat__change (arrow icon + words) + .hw-stat__note`.
- Timeline: `ol.hw-timeline > li.hw-timeline__item[data-tone=alert] > time.hw-timeline__time`.
- Empty: `.hw-empty > .hw-empty__art[aria-hidden] + p.hw-empty__title + text + optional action`.
- Cards: `.hw-card(--interactive|--outlined) > .hw-card__media? + .hw-card__title`; `a.hw-tile` (name + red chevron).

**Typography:** `.hw-heading--1…4`, `.hw-section-title(--center)` (60×2 red bar, Huawei's signature), `.hw-eyebrow`, `.hw-caption`, `.hw-text-muted`, `.hw-num` (tabular figures).

## 5a. BioSentry additions (0.10.0-biosentry)

Added to the vendored kit for the BioSentry dashboard, phase by phase. The full markup contract of each sits at
the top of its CSS file and in the kit README; working markup is in `test/gallery.html`.

| Phase | Component | In short |
|---|---|---|
| 1 Ward | `hw-score` | NEWS2 tile, `div.hw-score[data-band=high\|medium\|low][role=img][aria-label]`; 60px, `--sm` 40px; the band word always inside or beside it |
| 1 | `hw-filter` | chips with counts, `button.hw-filter__item[aria-pressed] > span.hw-filter__count[data-tone]`; a count of 0 takes no tone |
| 1 | `hw-bell` + `hw-alert-tray` | bell with count, tray `[role=dialog]` of alerts with Open and Acknowledge (`hw-button--accent`) |
| 1 | `hw-search` | patient search combobox with a listbox, `/` focuses it (the app wires the key) |
| 1 | `hw-ranklist` | the ward list, `ol.hw-ranklist[data-density] > li.hw-ranklist__row[data-alert]`; cards below 1200px |
| 1 | `hw-notice--dashed`, `hw-tag--dashed` | dashed means incomplete (NOT RANKED) and nothing else |
| 1 | `hw-banner--inverse` | the black offline banner, `role=alert` |
| 1 | `hw-segmented--accent` | selected option in ink, where red is clinical |
| 2 Patient | `hw-pager` | Previous / position / Next in rank order; K and J hints `aria-hidden` |
| 2 | `hw-decision` | the decision strip: NEWS2, Next step, Why (with the prediction-models note), Signal |
| 2 | `hw-actionbar` | phone bottom action bar; a screen shows it or `hw-bottomnav`, never both |
| 2 | `trend` options | `range`, `limits`, `band` + `bandLabel`, `highlightWhere` (see 7b) |
| 2 | highlight by space | `data-hw-space="clinical"` (ink) or `"research"` (info blue) on an ancestor sets `--hw-graph-highlight` |
| 3 Census | `hw-timeline-beds` | the 14-day bed timeline: stays placed by percentage, ended stays closed by their outcome mark, all five outcomes in the legend |
| 3 | `hw-steps--accent` | the current step in ink instead of brand red |
| 4 Research | `bars` options | `diverging`, `zeroLabel`, `sides`, per-item `interval`, `intervalLabel` (see 7b) |

In this product red is clinical (NEWS2 7+, a value adding points): app actions use `hw-button--accent`, selection
uses the `--accent` modifiers, graphs on clinical analysis pages sit in `data-hw-space="clinical"` and the research
pages in `data-hw-space="research"`. App icons are `span.hw-icon`, never `<i>` (pages are tested for no b/i/u/s).

## 6. Responsive & mobile

Mobile-first. Breakpoints are literal in `@media` (custom properties can't be used there):
**576 · 768 · 992 · 1200 · 1440**. Each component's responsive rules sit at the bottom of its own file.

Layout utilities: `.hw-container`, `.hw-section`, `.hw-stack` (`--hw-stack-gap`), `.hw-cluster`,
`.hw-grid` (auto-fit with `--hw-grid-min`, so phones get one column automatically), `.hw-hide-mobile`,
`.hw-only-mobile`, `.hw-only-compact`.

**App shell** (desktop: header or side nav; phone: app bar + bottom tabs + nav drawer):
```html
<body class="hw-has-bottomnav">
  <header class="hw-appbar hw-only-mobile">
    <button class="hw-appbar__action" aria-label="Open menu" data-hw-open="nav"><i class="hw-icon fa-solid fa-bars" aria-hidden="true"></i></button>
    <h1 class="hw-appbar__title">Ward W4</h1>
    <button class="hw-appbar__action" aria-label="Search"><i class="hw-icon fa-solid fa-magnifying-glass" aria-hidden="true"></i></button>
  </header>
  <div class="hw-shell">
    <aside class="hw-shell__side"><nav class="hw-sidenav" aria-label="Main">…</nav></aside>
    <main class="hw-shell__main hw-container">…</main>
  </div>
  <nav class="hw-bottomnav hw-only-mobile" aria-label="Primary">
    <a class="hw-bottomnav__item" href="/ward" aria-current="page"><span class="hw-bottomnav__icon" aria-hidden="true"><i class="hw-icon fa-solid fa-list-ol" aria-hidden="true"></i></span>Ward</a>
    <!-- 3–5 items; badge: <span class="hw-badge"><span class="hw-bottomnav__icon" aria-hidden="true"><i class="hw-icon fa-solid fa-bell" aria-hidden="true"></i></span><span class="hw-badge__count">3<span class="hw-visually-hidden"> new</span></span></span> -->
  </nav>
  <dialog class="hw-dialog hw-dialog--nav" id="nav" aria-label="Main menu"><nav class="hw-sidenav">…</nav></dialog>
</body>
```
Phone rules: touch targets ≥44px (automatic via tokens), 16px input text (no iOS zoom), safe-area
insets handled by app bar, bottom nav and sheet, no horizontal scroll (tested), filters in a bottom
sheet, record details in a full-screen dialog, tables use `--stack`.

## 7. Motion

Tokens (components never use raw ms): `--hw-motion-micro` 150ms (press, popups), `--hw-motion-exit`
200ms, `--hw-motion-enter` 300ms (dialogs, sheets, tab indicator, accordion), `--hw-motion-reveal` 600ms;
`--hw-ease-enter` (decelerate), `--hw-ease-exit` (accelerate), `--hw-ease-move` (A→B), `--hw-ease-entrance`
(reveal). `--hw-press-scale` .97, `--hw-reveal-distance` 24px, `--hw-reveal-stagger` 60ms.
**Exits are shorter than entrances.**

| Element | Enter | Exit |
|---|---|---|
| Menu, select listbox | `hw-pop-in` | `hw-pop-out` |
| Dialog + backdrop | `hw-dialog-in` + `hw-fade-in` | `hw-dialog-out` + `hw-fade-out` |
| Drawer / nav drawer / bottom sheet | `hw-drawer-in` / `hw-nav-in` / `hw-sheet-in` | the matching `-out`; sheets also close on a downward swipe of the header |
| Toast | `hw-toast-in` from its edge (`--hw-toast-offset`: top desktop, bottom phone) | `hw-toast-out` |
| Tabs | one sliding indicator (`[data-hw-indicator]::after`), underline or pill | — |
| Accordion | `::details-content` height + fade (progressive; snaps in older browsers) | same, reversed |
| Buttons, tiles, interactive cards | press scale .97 on `:active` | — |
| Scroll reveal | `data-hw-reveal` / `data-hw-reveal="stagger"` → `hw-fade-in-up`, staggered per batch | — |
| Content added later | `.hw-anim-fade-in`, `.hw-anim-fade-in-up`, `.hw-anim-pop-in` | — |

Exit contract (`js/core/motion.js`): `leave(el)` sets `[data-hw-leaving]`, waits for that element's
finite animations (max 1s), resolves `true`; `cancelLeave(el)` makes it resolve `false`. Callers flip
state at once (`aria-expanded`, focus, events) and only set `hidden` / call `dialog.close()` when
`leave()` resolves true. `[data-hw-leaving]` ignores pointer events. New overlay = give it a
`[data-hw-leaving]` animation in its CSS and use `leave()` in its JS. Esc on dialogs is intercepted via
the `cancel` event so it animates too.

Reduced motion: `prefers-reduced-motion: reduce` collapses every animation/transition (base.css),
`leave()` resolves instantly, and reveal hides nothing. Never make content depend on an animation ending.

## 7a. Icons (Font Awesome Free 7)

Peer dependency `@fortawesome/fontawesome-free` (icons CC BY 4.0, fonts OFL, code MIT; keep its licence
comment). Markup: `<i class="hw-icon fa-solid fa-bell" aria-hidden="true"></i>`. `.hw-icon` gives one box
(1.25em wide, centred) so icons align in buttons, inputs, nav and alerts; sizes `--sm` 12, `--lg` 20,
`--xl` 32; colours `--brand`, `--muted`. `.hw-icon-disc` = icon in a tinted circle.

- Icons are always `aria-hidden="true"`; text or `aria-label` carries meaning. Icon-only buttons:
  `.hw-button--icon` + `aria-label`.
- Use solid for actions and status, regular (`fa-regular`) for empty states and profile.
- Mapping used in the kit: menu `bars`, search `magnifying-glass`, close/remove `xmark`, stepper
  `minus`/`plus`, alerts `circle-info` / `circle-check` / `triangle-exclamation` / `circle-xmark`,
  pagination `chevron-left/right`, dropdown `chevron-down`, ranked list `list-ol`, patients
  `hospital-user`, lab `flask`, alerts `bell`, profile `fa-regular user`, acknowledge `bed-pulse`,
  vitals `heart-pulse`, export `download`, settings `gear`, refresh `rotate`, deltas `arrow-up/down`.
- `toast({ tone })` picks the glyph (success/danger/warning/info); `toast({ icon: 'fa-solid fa-…' })` overrides.
- CSS-drawn marks (select, native select, tile, text-link and accordion chevrons, breadcrumb separators,
  step ✓) are drawn with borders or gradients, never typed as "›": text in generated content on a link
  or button becomes part of its accessible name. They are also font-free, so a failed font
  load never breaks controls.
- Never use Font Awesome's brand icons to imitate Huawei branding.

## 7b. Graphs

Seven graph components in `src/js/graph/`. Read `docs/graphs.md` in the package before building one.
Each is `fn(element, config)` → `{ destroy() }`, or declarative:
`<div data-hw-graph="trend"><script type="application/json">{…}</script></div>` (picked up by `initAll()`).
A graph has no box of its own: put it on a `.hw-card`, a dialog or the page.

| The data | Use | Config beyond `title`, `subtitle`, `unit`, `format`, `hint` |
|---|---|---|
| Lines over time (≤4 series) | `trend` | `x`, `xLabel`, `series: [{ name, values, dashed? }]`, `highlight` (name or index); clinical: `range: [lo, hi]`, `limits: [{ at, label?, tone? }]`, `band: [lo, hi]` + `bandLabel`, `highlightWhere(v)` |
| A few things compared | `bars` | `items: [{ name, value, interval? }]`, `highlight`, `baseline` (index), `max`, `label(item)`; `diverging: true` + `zeroLabel`, `sides: [loss, gain]` for changes against a reference; `intervalLabel` |
| One measure over time | `columns` | `x`, `values`, `highlight`, `average`, `averageLabel`, `noun: [one, many]` |
| A score taken at set times | `steps` | `x`, `times`, `values` (null = no reading), `max`, `limits: [{ at, label, tone: 'urgent'\|'emergency' }]` |
| A value against its usual range | `range` | `items: [{ label, value, unit, usual: [lo, hi], scale: [min, max] }]` |
| Parts of a whole (≤5, largest first) | `share` | `parts: [{ name, value }]`, `highlight`, `noun`, `label(part)` |
| One key number | `stat` | `label`, `value`, `values` (sparkline), `change: { direction: 'up'\|'down', text }`, `note` |

The five rules (built in; don't work around them):
1. **A sentence, not a tooltip.** The readout above the plot (big number + plain sentence) changes as the
   reader points, taps or uses the arrow keys. Never add a floating tooltip to a graph.
2. **One red thing.** The mark to look at uses `--hw-graph-highlight`; everything else is grey. Don't
   colour series by category. If every series truly matters equally, use `--hw-graph-compare-1…4`, in that
   order, max four, always labelled (the orange is under 3:1 on white).
3. **Labels on the marks.** Names and values sit at line ends and bar ends. Trend's key buttons are both
   legend and control. Text never wears a mark colour, except a limit's own name.
4. **The title states the finding** ("Both models catch more deteriorations than NEWS2 alone"); the
   subtitle says what was measured, and says "Sample data" when it is.
5. **Honest shapes.** Bars start at zero. A score taken at set times is flat steps, never a slope. A
   missing reading is a visible gap. No pies, donuts, dual axes or stacked areas; two scales are two graphs.

Accessibility built in: `trend` plot is one tab stop (←/→/Home/End) with a polite live readout and a
"Show as a table" switch; `bars`, `columns`, `share` are button groups with one tab stop and arrow keys
(`aria-pressed`); `steps` lists every reading in its label and under the plot; `range` says each gap in
words. Limits (`urgent`, `emergency`) are for thresholds and states only, always with a word beside them.

Bars, diverging: the zero line sits in the middle of a symmetric scale (equal changes, equal lengths), losses left and
gains right, the zero mark bold and named (`zeroLabel: '0 · NEWS2 alone'`). An item's `interval` is an ink whisker with
end caps across its bar, haloed in the surface; the value moves past its far end and the readout says it
("98.33% interval +0.008 to +0.029"). Without these options a bars graph draws exactly as before.

Sizing: graphs fill their container and redraw on resize; below 480px `trend` drops end labels (the keys
remain) and `steps` moves limit names inside the plot; below 576px `range` rows stack and a
diverging `bars` puts each name above its bar, keeping only the scale's ends and zero.

## 8. Changing the kit

Adding a component:
1. `src/components/<name>.css` in `@layer hw.components { }`: component tokens first (pointing at
   semantic tokens), then base, modifiers, states (ARIA/pseudo-classes), then `@media` rules.
2. New color or size → add a semantic token in `tokens/semantic.css` and its dark value in `themes.css`.
3. `@import` it in `src/index.css` (alphabetical).
4. Behaviour → `src/js/<name>.js` exporting `init(scope) → cleanup`; register in `js/index.js`.
   Follow the WAI-ARIA Authoring Practices pattern for that widget.
5. Add real markup to `test/gallery.html` and keyboard checks to `test/run.py`.
6. `npm run lint && npm test` must pass (axe in light, dark, cloud, phone, phone-dark; 0 violations).
   Tests that close things must wait for the exit (`wait_for_function`), not assume instant removal.
7. Add its markup contract to section 5 of this skill.

Changing a value → change the token, never the component. Removing or renaming a class → it's a breaking
change: bump the package's major version and note it in the README.

## 9. Improvements over Huawei's originals (already in the kit; keep them)

- Contrast: Huawei's #999 (2.85:1), #aeaeae (2.22:1) and #777 (4.48:1) text fail WCAG AA; the kit uses
  #595959 (7:1) and #767676 (4.5:1). Ant blue #1890ff (3.24:1) replaced by #0a59f7 (5.55:1).
- Disabled buttons are grey, not pink (#f59297 + white is 2.22:1).
- Visible focus ring on every control (Huawei removes it on inputs).
- No `transition: all`; one radius per role; 5 breakpoints instead of ~40; hover darkens instead of
  fading to 70%; reduced-motion support; a contrast-checked dark theme (Huawei ships none).
- Textareas resize; modal buttons right-aligned (stacked on phones); destructive actions named.

## 10. Review checklist

- [ ] Uses kit classes and tokens; no raw colors, no inline styles except token values (`--hw-ring-value`, `--hw-grid-min`).
- [ ] Our product mark only; no Huawei logo, wordmark, slogan or art.
- [ ] Every input has a `<label>`; errors use `aria-invalid` + `aria-describedby`; a native `<select>` uses `.hw-native-select`, not an `.hw-input` wrapper.
- [ ] Icon-only buttons have `aria-label`; decorative icons `aria-hidden="true"`.
- [ ] Overlays are `<dialog>`; menus/selects close on Esc and return focus.
- [ ] Phone check at 390px: no horizontal scroll, bottom nav ≤5 items, stacked tables, 44px targets.
- [ ] Icons from Font Awesome Free inside `.hw-icon`, `aria-hidden`; no glyph characters as icons.
- [ ] Motion uses tokens; new overlays have an exit via `leave()`; nothing depends on an animation ending.
- [ ] Graphs: the right type for the data, a title that states the finding, one highlight, no tooltips, no pies or dual axes, sample data labelled as such.
- [ ] The product says somewhere prominent that it is set in HarmonyOS Sans, and the font files are untouched.
- [ ] `npm run lint && npm test` pass.
