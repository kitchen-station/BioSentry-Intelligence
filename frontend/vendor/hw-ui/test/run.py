"""hw-ui test runner: keyboard behaviour, axe accessibility scan, and screenshots.
Usage: python3 -I test/run.py   (needs Playwright + Chrome; serves the repo over HTTP for ES modules)."""
import http.server, threading, functools, pathlib, sys, json, os, re
from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / 'test' / 'out'
OUT.mkdir(exist_ok=True)
handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(ROOT))
http.server.SimpleHTTPRequestHandler.log_message = lambda *a: None
server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
BASE = f'http://127.0.0.1:{server.server_address[1]}/test/gallery.html'
AXE = (ROOT / 'node_modules/axe-core/axe.min.js').read_text()

results, failures = [], []
def check(name, cond, detail=''):
    results.append((name, bool(cond)))
    if not cond: failures.append(f'{name} {detail}')

def axe(page, label):
    page.add_script_tag(content=AXE)
    r = page.evaluate("async () => (await axe.run(document, {resultTypes:['violations']})).violations.map(v => ({id:v.id, impact:v.impact, n:v.nodes.length, t:v.nodes.slice(0,3).map(n=>n.target.join(' '))}))")
    check(f'axe {label}: no violations', not r, json.dumps(r))

with sync_playwright() as p:
    # Linux CI has Chrome at /usr/bin/google-chrome; elsewhere use the installed Google Chrome.
    browser = p.chromium.launch(**({'executable_path': '/usr/bin/google-chrome'} if os.path.exists('/usr/bin/google-chrome') else {'channel': 'chrome'}))
    errors = []
    def page_for(**kw):
        kw.setdefault('reduced_motion', 'no-preference')
        pg = browser.new_page(**kw)
        pg.on('pageerror', lambda e: errors.append(str(e)))
        pg.on('console', lambda m: errors.append(m.text) if m.type == 'error' else None)
        return pg

    # ---- Desktop behaviour ----
    pg = page_for(viewport={'width': 1280, 'height': 900})
    pg.goto(BASE); pg.wait_for_function('window.hw')

    trig = pg.locator('#ward-select .hw-select__trigger')
    trig.focus(); pg.keyboard.press('ArrowDown')
    check('select: ArrowDown opens', trig.get_attribute('aria-expanded') == 'true')
    pg.keyboard.press('ArrowDown'); pg.keyboard.press('Enter')
    check('select: Enter picks next option', pg.locator('#ward-select input[type=hidden]').input_value() == 'w5')
    check('select: closes and refocuses trigger', trig.get_attribute('aria-expanded') == 'false' and pg.evaluate('document.activeElement.classList.contains("hw-select__trigger")'))
    pg.keyboard.press('ArrowDown'); pg.keyboard.press('w')
    check('select: type-ahead moves to the next match', 'S2' in pg.locator('#ward-select [data-active]').inner_text())
    pg.keyboard.press('Escape')
    check('select: Escape closes', trig.get_attribute('aria-expanded') == 'false')
    trig.click(); pg.locator('main h1').click()
    check('select: outside click closes', trig.get_attribute('aria-expanded') == 'false')

    mt = pg.locator('[data-hw-menu-trigger]')
    mt.focus(); pg.keyboard.press('ArrowDown')
    check('menu: ArrowDown opens and focuses first item', pg.evaluate('document.activeElement.dataset.action') == 'ack')
    pg.keyboard.press('End')
    check('menu: End focuses last item', pg.evaluate('document.activeElement.dataset.action') == 'discharge')
    pg.keyboard.press('Escape')
    check('menu: Escape closes and returns focus', mt.get_attribute('aria-expanded') == 'false' and pg.evaluate('document.activeElement.hasAttribute("data-hw-menu-trigger")'))
    check('motion: menu plays an exit before hiding', pg.evaluate('(() => { const m = document.querySelector(".hw-menu"); return !m.hidden && m.hasAttribute("data-hw-leaving"); })()'))
    pg.wait_for_function('document.querySelector(".hw-menu").hidden')
    check('motion: menu hidden after its exit', True)
    pg.keyboard.press('ArrowDown'); pg.keyboard.press('Escape'); pg.keyboard.press('ArrowDown'); pg.wait_for_timeout(400)
    check('motion: reopening mid-exit keeps the menu open', pg.evaluate('(() => { const m = document.querySelector(".hw-menu"); return !m.hidden && !m.hasAttribute("data-hw-leaving"); })()'))
    pg.keyboard.press('Escape'); pg.wait_for_timeout(300)

    pg.locator('#t1').focus(); pg.keyboard.press('ArrowRight')
    check('tabs: ArrowRight selects next', pg.locator('#t2').get_attribute('aria-selected') == 'true')
    check('tabs: panel follows', pg.locator('#p2').is_visible() and not pg.locator('#p1').is_visible())
    check('tabs: roving tabindex', pg.locator('#t1').get_attribute('tabindex') == '-1' and pg.locator('#t2').get_attribute('tabindex') == '0')
    ind = pg.evaluate('(() => { const l = document.querySelector("#t2").parentElement, t = document.querySelector("#t2"); return [l.style.getPropertyValue("--hw-tab-indicator-x"), t.offsetLeft + "px", l.style.getPropertyValue("--hw-tab-indicator-w"), t.offsetWidth + "px", getComputedStyle(l, "::after").transitionDuration]; })()')
    check('motion: tab indicator slides under the selected tab', ind[0] == ind[1] and ind[2] == ind[3] and ind[4] != '0s', str(ind))

    opener = pg.locator('[data-hw-open="discharge"]')
    opener.click()
    check('dialog: opens', pg.evaluate('document.getElementById("discharge").open'))
    pg.wait_for_timeout(350)
    pg.keyboard.press('Escape')
    check('motion: dialog animates out before closing', pg.evaluate('(() => { const d = document.getElementById("discharge"); return d.open && d.hasAttribute("data-hw-leaving"); })()'))
    pg.wait_for_function('!document.getElementById("discharge").open', timeout=2000)
    check('dialog: Escape closes', not pg.evaluate('document.getElementById("discharge").open'))
    check('dialog: focus returns to opener', pg.evaluate('document.activeElement.dataset.hwOpen') == 'discharge')
    opener.click(); pg.wait_for_timeout(350); pg.mouse.click(5, 5)
    pg.wait_for_function('!document.getElementById("discharge").open', timeout=2000)
    check('dialog: backdrop click closes', not pg.evaluate('document.getElementById("discharge").open'))

    pg.locator('[data-hw-tooltip]').focus(); pg.wait_for_timeout(450)
    check('tooltip: shows on keyboard focus', pg.locator('.hw-tooltip[role=tooltip]').count() == 1)
    check('tooltip: linked via aria-describedby', pg.locator('[data-hw-tooltip]').get_attribute('aria-describedby') is not None)
    pg.keyboard.press('Escape')
    check('tooltip: Escape hides', pg.locator('.hw-tooltip').count() == 0)

    check('slider: output synced on load', pg.locator('output[for=vol]').inner_text() == '4')
    pg.locator('#vol').focus(); pg.keyboard.press('ArrowRight')
    check('slider: fill var follows value', pg.evaluate('getComputedStyle(document.getElementById("vol")).getPropertyValue("--hw-slider-value").trim()') == '50')

    pg.locator('#toast-btn').click()
    check('toast: appears in live region', pg.locator('.hw-toast-region[role=status] .hw-toast').count() == 1)
    check('icons: toast uses a Font Awesome glyph', pg.evaluate('getComputedStyle(document.querySelector(".hw-toast .fa-circle-check"), "::before").fontFamily').find('Font Awesome') >= 0)
    pg.locator('.hw-toast__action').click()
    check('motion: toast exits then is removed', pg.locator('.hw-toast[data-hw-leaving]').count() == 1)
    pg.wait_for_function('!document.querySelector(".hw-toast")', timeout=2000)

    acc = pg.locator('.hw-accordion__item').nth(1)
    acc.locator('summary').click(); pg.wait_for_timeout(60)
    mid = acc.evaluate('e => e.getBoundingClientRect().height'); pg.wait_for_timeout(500)
    end = acc.evaluate('e => e.getBoundingClientRect().height')
    check('motion: accordion expands smoothly', mid < end - 4, f'{mid} -> {end}')

    btn = pg.locator('.hw-button--secondary').first
    btn.hover(); pg.mouse.down(); pg.wait_for_timeout(200)
    check('motion: button presses in', btn.evaluate('e => getComputedStyle(e).transform') != 'none')
    pg.mouse.up()

    icon_font = pg.evaluate('getComputedStyle(document.querySelector(".hw-alert .fa-circle-info"), "::before").fontFamily')
    check('icons: Font Awesome loaded for markup icons', 'Font Awesome' in icon_font, icon_font)

    # ---- Form states and names ----
    css = lambda sel, prop: pg.evaluate('([s, p]) => getComputedStyle(document.querySelector(s))[p]', [sel, prop])
    BORDER, DANGER, SURFACE = 'rgb(201, 201, 201)', 'rgb(224, 33, 40)', 'rgb(255, 255, 255)'
    check('native select: styled as a kit control', [css('#shift', p) for p in ('height', 'borderTopLeftRadius', 'appearance', 'borderTopColor')] == ['40px', '6px', 'none', BORDER], str([css('#shift', p) for p in ('height', 'borderTopLeftRadius', 'appearance', 'borderTopColor')]))
    check('native select: the empty choice reads as a placeholder', css('#shift', 'color') == 'rgb(118, 118, 118)', css('#shift', 'color'))
    check('native select: a disabled option does not grey the control', css('#shift', 'backgroundColor') == SURFACE and css('#shift', 'cursor') == 'pointer', css('#shift', 'backgroundColor'))
    check('native select: a disabled control is greyed', css('#unit-locked', 'backgroundColor') != SURFACE and css('#unit-locked', 'cursor') == 'not-allowed')
    wrapped = pg.evaluate("""(() => { const w = document.createElement('div'); w.className = 'hw-input'; w.id = 'wrapped';
      const s = document.createElement('select'); s.className = 'hw-input__control'; s.setAttribute('aria-label', 'Wrapped');
      for (const [t, off] of [['Day', false], ['Night', true]]) { const o = document.createElement('option'); o.textContent = t; o.disabled = off; s.append(o); }
      w.append(s); document.querySelector('#record').closest('.hw-field').after(w);
      const c = getComputedStyle(w), out = [c.backgroundColor, c.cursor]; w.remove(); return out; })()""")
    check('input: a disabled option inside the wrapper does not grey it', wrapped == [SURFACE, 'auto'], str(wrapped))
    onload = [pg.evaluate('getComputedStyle(document.querySelector("#record").parentElement).borderTopColor'), css('#shift', 'borderTopColor')]
    check('input: empty required fields are not red on load', onload == [BORDER, BORDER], str(onload))
    pg.locator('#record').click(); pg.keyboard.type('7'); pg.keyboard.press('Backspace'); pg.locator('#h-form').click(); pg.wait_for_timeout(350)
    left = pg.evaluate('getComputedStyle(document.querySelector("#record").parentElement).borderTopColor')
    check('input: a required field left empty turns red', left == DANGER, left)
    pg.locator('#shift').focus(); pg.keyboard.press('ArrowDown'); pg.keyboard.press('ArrowUp'); pg.keyboard.press('Tab'); pg.wait_for_timeout(350)  # real keys: a scripted change is not "user" invalid
    check('native select: left on the empty choice turns red', css('#shift', 'borderTopColor') == DANGER, css('#shift', 'borderTopColor'))
    pg.locator('#shift').select_option('evening'); pg.locator('#h-form').click(); pg.wait_for_timeout(350)
    check('native select: a valid choice clears the red', css('#shift', 'borderTopColor') == BORDER and css('#shift', 'color') == 'rgb(25, 25, 25)', css('#shift', 'borderTopColor'))
    names = [pg.locator(s).first.aria_snapshot() for s in ('.hw-tile', '.hw-link-cta')]
    check('names: the tile chevron is not in the link name', 'link "Ward"' in names[0] and '›' not in names[0], names[0])
    check('names: the text-link chevron is not in the link name', 'link "Learn more"' in names[1] and '›' not in names[1], names[1])
    crumbs = pg.locator('.hw-breadcrumb').aria_snapshot()
    check('names: breadcrumb separators are not read out', '›' not in crumbs, crumbs)
    marks = [pg.evaluate('(s) => { const r = document.querySelector(s), c = getComputedStyle(r, s.includes("breadcrumb") ? "::before" : "::after"); return [parseFloat(c.width) > 3, c.borderTopWidth !== "0px"]; }', s) for s in ('.hw-tile', '.hw-link-cta', '.hw-breadcrumb li + li')]
    check('names: the chevrons are still drawn', all(a and b for a, b in marks), str(marks))

    # ---- Graphs ----
    below = pg.evaluate('[...document.querySelectorAll("[data-hw-reveal] > *")].map(e => e.dataset.hwRevealState || "none")')
    check('reveal: below-the-fold items wait hidden', 'out' in below, str(below))
    pg.locator('#g-steps').scroll_into_view_if_needed(); pg.wait_for_timeout(900)
    check('reveal: items animate in when scrolled to', pg.evaluate('document.querySelector("#g-steps").closest(".hw-card").dataset.hwRevealState') == 'in')
    pg.evaluate('document.fonts.ready')
    check('type: HarmonyOS Sans is bundled and loaded', pg.evaluate('document.fonts.check("300 44px \\"HarmonyOS Sans\\"") && document.fonts.check("700 14px \\"HarmonyOS Sans\\"")'))
    pg.locator('#g-trend').scroll_into_view_if_needed(); pg.wait_for_timeout(200)
    tr = '#g-trend '
    rd = lambda sel: [pg.locator(sel + ' .hw-graph__value').inner_text(), pg.locator(sel + ' .hw-graph__label').inner_text(), pg.locator(sel + ' .hw-graph__detail').inner_text()]
    check('trend: readout starts on the highlighted line, latest point', rd('#g-trend') == ['0.13', 'Checked student, Week 6', 'NEWS2 alone 0.41, Student model 0.41'], str(rd('#g-trend')))
    cols = pg.evaluate('[...document.querySelectorAll("#g-trend .hw-graph__line")].map(l => getComputedStyle(l).stroke)')
    check('trend: one red line, the rest grey', cols.count('rgb(199, 0, 11)') == 1 and cols.count('rgb(147, 147, 147)') == 2, str(cols))
    check('trend: names and last values sit at the line ends', pg.locator(tr + '.hw-graph__end').count() == 3 and pg.locator(tr + '.hw-graph__end[data-on]').text_content() == 'Checked student0.13')
    ys = pg.evaluate('[...document.querySelectorAll("#g-trend .hw-graph__end")].map(t => +t.getAttribute("y")).sort((a, b) => a - b)')
    check('trend: end labels never overlap', all(b - a >= 15.9 for a, b in zip(ys, ys[1:])), str(ys))
    plot = pg.locator(tr + '.hw-graph__plot')
    plot.focus(); pg.keyboard.press('Home')
    check('trend: arrow keys read each point', rd('#g-trend')[:2] == ['0.2', 'Checked student, Week 1'], str(rd('#g-trend')))
    check('trend: keyboard focus stays on the plot while reading', pg.evaluate('document.activeElement.classList.contains("hw-graph__plot")'))
    pg.keyboard.press('ArrowRight'); pg.keyboard.press('ArrowRight')
    check('trend: the guide follows the reading', rd('#g-trend')[1] == 'Checked student, Week 3' and pg.evaluate('(() => { const g = document.querySelector("#g-trend .hw-graph__guide"), d = document.querySelector("#g-trend .hw-graph__dot[data-on]:last-of-type"); return g.getAttribute("x1") === d.getAttribute("cx"); })()'))
    box = plot.bounding_box(); pg.mouse.move(box['x'] + 40, box['y'] + 80)
    check('trend: pointing reads the nearest point', rd('#g-trend')[1] == 'Checked student, Week 1', str(rd('#g-trend')))
    pg.mouse.move(5, 5)
    check('trend: leaving returns to the latest point', rd('#g-trend')[1] == 'Checked student, Week 6')
    pg.locator(tr + '.hw-graph__key').first.click()
    check('trend: a key button changes the highlighted line', rd('#g-trend')[:2] == ['0.41', 'NEWS2 alone, Week 6'] and pg.locator(tr + '.hw-graph__key[aria-pressed=true]').inner_text() == 'NEWS2 alone' and pg.evaluate('getComputedStyle(document.querySelector("#g-trend .hw-graph__line[data-on]")).stroke') == 'rgb(199, 0, 11)')
    pg.locator(tr + '.hw-graph__toggle').click()
    check('trend: shows as a table with every value', pg.locator(tr + 'tbody tr').count() == 6 and not plot.is_visible() and pg.locator(tr + 'tbody tr').first.inner_text().split() == ['Week', '1', '0.42', '0.44', '0.2'])
    pg.locator(tr + '.hw-graph__toggle').click()
    check('trend: and back to the graph', plot.is_visible() and pg.locator(tr + '.hw-graph__toggle').inner_text() == 'Show as a table')

    check('bars: readout compares with the baseline', rd('#g-bars') == ['57%', 'flagged early by Checked student', '21 points more than NEWS2 alone'], str(rd('#g-bars')))
    bw = pg.evaluate('[...document.querySelectorAll("#g-bars .hw-graph__bar")].map(b => [b.getBoundingClientRect().height, getComputedStyle(b).backgroundColor])')
    check('bars: 22px bars, one highlighted', all(abs(h - 22) < 0.5 for h, _ in bw) and [c for _, c in bw].count('rgb(199, 0, 11)') == 1, str(bw))
    pg.locator('#g-bars .hw-graph__row[aria-pressed=true]').focus(); pg.keyboard.press('ArrowUp')
    check('bars: arrow keys move the highlight', rd('#g-bars')[:2] == ['56%', 'flagged early by Student model'] and pg.evaluate('document.activeElement.textContent') == 'Student model 56%')
    pg.keyboard.press('Home')
    check('bars: the baseline says so', rd('#g-bars')[2] == 'The baseline the others are measured against')
    check('bars: one tab stop for the group', pg.locator('#g-bars .hw-graph__row[tabindex="0"]').count() == 1)

    check('columns: latest column highlighted, read against the average', rd('#g-columns') == ['2', 'alerts on 16 Nov', '4.1 below the 14-day average'], str(rd('#g-columns')))
    cw = pg.evaluate('Math.max(...[...document.querySelectorAll("#g-columns .hw-graph__column-bar")].map(b => b.getBoundingClientRect().width))')
    check('columns: never wider than 24px', cw <= 24.01, str(cw))
    pg.locator('#g-columns .hw-graph__column').nth(3).hover()
    check('columns: pointing at a column reads it', rd('#g-columns') == ['11', 'alerts on 6 Nov', '4.9 above the 14-day average'], str(rd('#g-columns')))
    check('columns: average line labelled', pg.locator('#g-columns .hw-graph__level--average').inner_text() == '14-day average 6.1')

    check('steps: sentence says the change, the crossing and the gap', rd('#g-steps') == ['7', 'at 14:00, emergency', 'Up 5 since 03:00. Reached 5 at 11:00. No reading at 08:00.'], str(rd('#g-steps')))
    sg = pg.evaluate('(() => { const s = [...document.querySelectorAll("#g-steps .hw-graph__step")]; return [s.length, s.every(l => l.getAttribute("y1") === l.getAttribute("y2")), document.querySelectorAll("#g-steps .hw-graph__hole").length, s.filter(l => l.dataset.tone === "urgent").length, s.filter(l => l.dataset.tone === "emergency").length]; })()')
    check('steps: flat for each reading, a hole for the missing one, toned past each limit', sg == [11, True, 1, 3, 1], str(sg))
    check('steps: no riser across a missing reading', pg.locator('#g-steps .hw-graph__riser').count() == 4)
    check('steps: every value printed under the plot', pg.evaluate('[...document.querySelectorAll("#g-steps .hw-graph__cell:not(.hw-graph__cell--time)")].map(c => c.textContent).join(" ")') == '2 2 2 3 3 – 4 4 5 5 6 7')

    notes = pg.evaluate('[...document.querySelectorAll("#g-range .hw-graph__item")].map(r => [r.hasAttribute("data-off"), r.querySelector(".hw-graph__item-note").textContent])')
    check('range: readings outside their usual range say how far', notes == [[True, '4 above usual 14 to 18'], [True, '2 below usual 95 to 98'], [True, '24 above usual 72 to 88'], [True, '6 below usual 110 to 130'], [False, 'usual 36.4 to 37.2']], str(notes))
    inside = pg.evaluate('(() => { const r = [...document.querySelectorAll("#g-range .hw-graph__item")].at(-1), b = r.querySelector(".hw-graph__usual").getBoundingClientRect(), n = r.querySelector(".hw-graph__now").getBoundingClientRect(); const c = n.left + n.width / 2; return c >= b.left && c <= b.right; })()')
    check('range: a normal reading sits inside its band', inside)

    check('share: readout names the part and its count', rd('#g-share') == ['62%', 'of alerts came from vital signs', '29 of 47 alerts'], str(rd('#g-share')))
    sw = pg.evaluate('(() => { const p = [...document.querySelectorAll("#g-share .hw-graph__part-bar")].map(b => b.getBoundingClientRect().width); const t = p.reduce((a, b) => a + b, 0); return p.map(w => Math.round(w / t * 100)); })()')
    check('share: widths match the shares', sw == [62, 28, 11] or sw == [62, 28, 10] or sw == [62, 27, 11], str(sw))
    pg.locator('#g-share .hw-graph__part').nth(1).click()
    check('share: tapping a part highlights it', rd('#g-share')[0] == '28%' and pg.locator('#g-share .hw-graph__part[aria-pressed=true]').count() == 1)

    check('stat: number, sparkline, and a change with its arrow', pg.locator('#g-stat-1 .hw-stat__value').inner_text() == '0.13' and pg.locator('#g-stat-1 .hw-stat__line').count() == 1 and pg.locator('#g-stat-1 .hw-stat__change .fa-arrow-down').count() == 1)
    check('graphs: every graph is a labelled figure', pg.evaluate('[...document.querySelectorAll(".hw-graph")].every(g => g.getAttribute("role") === "figure" && document.getElementById(g.getAttribute("aria-labelledby")))') and pg.locator('.hw-graph').count() == 7)

    # ---- Ward components (0.10): score, filter, bell and tray, search, ranklist, notice, banner ----
    EMERGENCY, URGENT, URGENT_TEXT, WHITE, INK, GREY = 'rgb(199, 0, 11)', 'rgb(244, 132, 12)', 'rgb(149, 67, 4)', 'rgb(255, 255, 255)', 'rgb(25, 25, 25)', 'rgb(223, 223, 223)'
    check('score: high is an emergency fill with white text', [css('#score-high .hw-score__value', p) for p in ('backgroundColor', 'color')] == [EMERGENCY, WHITE])
    check('score: medium is white with a 2px urgent border and urgent text', [css('#score-medium .hw-score__value', p) for p in ('backgroundColor', 'borderTopColor', 'borderTopWidth', 'color')] == [WHITE, URGENT, '2px', URGENT_TEXT])
    check('score: low is a grey fill with ink text', [css('#score-low .hw-score__value', p) for p in ('backgroundColor', 'color')] == [GREY, INK])
    sz = pg.evaluate('["#score-high", "#score-sm"].map(s => { const v = document.querySelector(s + " .hw-score__value").getBoundingClientRect(), b = document.querySelector(s + " .hw-score__band").getBoundingClientRect(); return [v.width, v.height, b.left >= v.right]; })')
    check('score: 60px with the band inside, --sm 40px with the band beside', sz == [[60, 60, False], [40, 40, True]], str(sz))

    cnt = lambda n, p: pg.evaluate('([n, p]) => getComputedStyle(document.querySelectorAll("#filter .hw-filter__count")[n])[p]', [n, p])
    check('filter: the selected chip is ink with white text', [css('#filter [aria-pressed=true]', p) for p in ('backgroundColor', 'color')] == [INK, WHITE])
    check('filter: count tones (emergency fill, urgent border)', cnt(1, 'backgroundColor') == EMERGENCY and cnt(2, 'borderTopColor') == URGENT and cnt(2, 'color') == URGENT_TEXT)
    check('filter: a count of 0 never takes a tone', cnt(5, 'backgroundColor') == GREY, cnt(5, 'backgroundColor'))
    chips = pg.locator('#filter .hw-filter__item')
    chips.nth(1).focus(); pg.keyboard.press('Space'); pg.wait_for_timeout(250)  # colours transition
    check('filter: Space selects one chip and clears the others', [chips.nth(i).get_attribute('aria-pressed') for i in range(6)] == ['false', 'true'] + ['false'] * 4)
    check('filter: a tone stays on the selected chip', css('#filter [aria-pressed=true]', 'backgroundColor') == INK and cnt(1, 'backgroundColor') == EMERGENCY)
    pg.keyboard.press('Shift+Tab'); pg.keyboard.press('Enter')
    check('filter: Enter selects, All resets', chips.nth(0).get_attribute('aria-pressed') == 'true' and chips.nth(1).get_attribute('aria-pressed') == 'false')

    check('bell: tinted while it shows a count, plain without', [css('#bell-live', 'backgroundColor'), css('#bell-live', 'borderTopColor'), css('#bell-calm', 'backgroundColor')] == ['rgb(254, 231, 232)', EMERGENCY, WHITE])
    bell = pg.locator('#bell-live')
    bell.focus(); pg.keyboard.press('Enter'); pg.wait_for_timeout(250)  # let the pop-in finish before measuring
    check('tray: Enter on the bell opens it and focuses the first Acknowledge', bell.get_attribute('aria-expanded') == 'true' and pg.evaluate('(() => { const a = document.activeElement; return !!a.closest("#tray-live") && a.textContent; })()') == 'Acknowledge')
    geo = pg.evaluate('(() => { const b = document.getElementById("bell-live").getBoundingClientRect(), t = document.getElementById("tray-live").getBoundingClientRect(); return [Math.round(t.top - b.bottom), Math.round(t.right - b.right), t.width]; })()')
    check('tray: drops 8px under the bell, right-aligned, 400px wide', geo == [8, 0, 400], str(geo))
    pg.keyboard.press('Escape')
    check('tray: Esc closes and returns focus to the bell', bell.get_attribute('aria-expanded') == 'false' and pg.evaluate('document.activeElement.id') == 'bell-live')
    check('motion: tray plays its exit before hiding', pg.evaluate('(() => { const t = document.getElementById("tray-live"); return !t.hidden && t.hasAttribute("data-hw-leaving") && t.getAnimations().length > 0; })()'))
    pg.wait_for_function('document.getElementById("tray-live").hidden', timeout=2000)
    check('tray: 36px buttons on desktop; offline Acknowledge disabled', css('#tray-offline .hw-button', 'height') == '36px' and pg.locator('#tray-offline .hw-button--accent').is_disabled())

    act = pg.evaluate('(() => { const o = document.querySelector("#search-results [aria-selected=true]"); return [o.id, getComputedStyle(o).boxShadow.includes("inset"), document.querySelector("#search [role=combobox]").getAttribute("aria-activedescendant")]; })()')
    check('search: the active option is marked and is the aria-activedescendant', act[1] and act[0] == act[2], str(act))
    check('search: results drop under the field', pg.evaluate('document.getElementById("search-results").getBoundingClientRect().top - document.querySelector("#search .hw-input").getBoundingClientRect().bottom') == 8)

    rl = pg.evaluate("""(() => { const cs = getComputedStyle, row = (l, n) => document.querySelectorAll(l + " .hw-ranklist__row")[n];
      const a = row("#list-comfortable", 0), lim = row("#list-compact", 2), reyes = row("#list-compact", 0);
      return [cs(a).gridTemplateColumns.split(" ").length, cs(a).boxShadow, cs(a).backgroundColor, a.getBoundingClientRect().height >= 88,
              Math.round(lim.getBoundingClientRect().height), cs(reyes.querySelector(".hw-ranklist__reasons")).display, cs(reyes.querySelector(".hw-ranklist__spark")).display,
              cs(document.querySelector("#list-comfortable .hw-ranklist__head")).display]; })()""")
    check('ranklist: fixed 10-column rows with a header', rl[0] == 10 and rl[7] == 'grid', str(rl))
    check('ranklist: alert row has the 4px emergency bar and the danger tint', 'inset' in rl[1] and '4px 0px 0px 0px' in rl[1] and EMERGENCY in rl[1] and rl[2] == 'rgb(254, 231, 232)', str(rl[1:3]))
    check('ranklist: comfortable rows 88px, compact rows 56px without sparklines or chips', rl[3] and rl[4] == 56 and rl[5] == 'none' and rl[6] == 'none', str(rl[3:7]))
    vt = pg.evaluate('[...document.querySelectorAll("#list-comfortable .hw-ranklist__row:nth-child(2) .hw-ranklist__value")].map(v => [getComputedStyle(v).color, getComputedStyle(v).fontWeight])')
    check('ranklist: a value adding points is red (bold at 2+), the rest ink', vt == [[EMERGENCY, '500'], [INK, '500'], [EMERGENCY, '500'], [EMERGENCY, '700'], [INK, '500']], str(vt))
    check('ranklist: a stale age is red and bold', [css('#list-comfortable [data-stale]', p) for p in ('color', 'fontWeight')] == [EMERGENCY, '700'])

    check('notice: --dashed is a 1px dashed subtle border', [css('#notice', p) for p in ('borderTopStyle', 'borderTopWidth', 'borderTopColor')] == ['dashed', '1px', 'rgb(118, 118, 118)'])
    check('tag: --dashed is dashed', css('#tag-dashed', 'borderTopStyle') == 'dashed')
    check('banner: --inverse is black with white text', [css('#offline-banner', p) for p in ('backgroundColor', 'color')] == [INK, WHITE])

    # ---- Patient (0.10 Phase 2): trend range, limits, band, highlightWhere; highlight by space; pager, decision, action bar ----
    pg.locator('#g-hr').scroll_into_view_if_needed(); pg.wait_for_timeout(200)
    hr = pg.evaluate("""(() => { const g = document.getElementById("g-hr"), q = (s) => [...g.querySelectorAll(s)], n = (e, a) => +e.getAttribute(a), cs = getComputedStyle;
      const band = g.querySelector(".hw-graph__band"), dots = q(".hw-graph__dot");
      return { ticks: q(".hw-graph__tick:not(.hw-graph__tick--x)").map(t => t.textContent), baselines: q(".hw-graph__baseline").length,
        grid: q(".hw-graph__gridline").map(l => n(l, "y1")), limits: q(".hw-graph__limit").map(l => [n(l, "y1"), n(l, "x2"), cs(l).strokeDasharray]),
        labels: q(".hw-graph__limit-label").map(t => [t.textContent, n(t, "x")]), band: [n(band, "y"), n(band, "height"), cs(band).fill],
        bandLabel: q(".hw-graph__band-label").map(t => t.textContent), strokes: q(".hw-graph__line").map(l => [l.hasAttribute("data-on"), cs(l).stroke]),
        lit: g.querySelector(".hw-graph__line[data-on]").getAttribute("d"), endDot: cs(dots[0]).fill, value: cs(g.querySelector(".hw-graph__value")).color }; })()""")
    top, bottom = min(hr['grid']), max(hr['grid'])
    yv = lambda v: bottom - (v - 40) / 100 * (bottom - top)
    check('trend range: the y-axis runs from 40 to 140, not from zero', hr['ticks'] == ['40 bpm', '140 bpm'] and hr['baselines'] == 0 and len(hr['grid']) == 2, str(hr['ticks']))
    check('trend limits: dashed lines where their values fall on the range', sorted(round(y) for y, _, _ in hr['limits']) == sorted(round(yv(v)) for v in (50, 90, 110)) and all(d != 'none' for _, _, d in hr['limits']), str(hr['limits']))
    check('trend limits: each named at its right end', sorted(t for t, _ in hr['labels']) == ['+1 point above 90', '+1 point at 50 or below', '+2 points above 110'] and all(x > max(x2 for _, x2, _ in hr['limits']) for _, x in hr['labels']), str(hr['labels']))
    check('trend band: shaded from 72 to 88 and named', abs(hr['band'][0] - yv(88)) < 0.5 and abs(hr['band'][1] - (yv(72) - yv(88))) < 0.5 and hr['band'][2] == 'rgba(25, 25, 25, 0.18)' and hr['bandLabel'] == ['Own usual 72–88'], str(hr['band']))
    starts = [float(y) for y in re.findall(r'M[\d.]+,([\d.]+)', hr['lit'])]
    check('trend highlightWhere: only the stretches adding points are red, cut at the limit', hr['strokes'] == [[False, 'rgb(147, 147, 147)'], [True, EMERGENCY]] and len(starts) == 2 and all(abs(y - yv(90)) < 0.6 for y in starts), str([hr['strokes'], starts, yv(90)]))
    check('trend highlightWhere: the latest reading adds points, so its dot and number are red', hr['endDot'] == EMERGENCY and hr['value'] == EMERGENCY, str([hr['endDot'], hr['value']]))
    reading = lambda: pg.evaluate('(() => { const g = document.getElementById("g-hr"); return [g.querySelector(".hw-graph__value").textContent, getComputedStyle(g.querySelector(".hw-graph__value")).color, [...g.querySelectorAll(".hw-graph__dot")].at(-1).hasAttribute("data-on")]; })()')
    pg.locator('#g-hr .hw-graph__plot').focus(); pg.keyboard.press('Home')
    first = reading()
    for _ in range(6): pg.keyboard.press('ArrowRight')
    spike = reading()
    check('trend highlightWhere: a reading inside the limits is grey, one outside them red', first == ['78 bpm', INK, False] and spike == ['96 bpm', EMERGENCY, True], str([first, spike]))
    out = pg.evaluate("""(() => { const d = document.createElement("div"); d.style.width = "600px"; document.getElementById("g-hr").after(d);
      const t = window.hw.trend(d, { title: "Out of range", x: ["a", "b"], series: [{ name: "HR", values: [150, 30] }], range: [40, 140] });
      const grid = [...d.querySelectorAll(".hw-graph__gridline")].map(l => +l.getAttribute("y1")), dot = () => +[...d.querySelectorAll(".hw-graph__dot")].at(-1).getAttribute("cy");
      const r = [Math.min(...grid), Math.max(...grid), dot(), d.querySelector(".hw-graph__value").textContent, [...d.querySelectorAll("tbody td")].map(c => c.textContent)];
      d.querySelector(".hw-graph__plot").dispatchEvent(new KeyboardEvent("keydown", { key: "Home" }));
      r.push(dot(), d.querySelector(".hw-graph__value").textContent); t.destroy(); d.remove(); return r; })()""")
    check('trend range: a value outside it sits on the edge; the readout and table keep it', out[2] == out[1] and out[3] == '30' and out[4] == ['150', '30'] and out[5] == out[0] and out[6] == '150', str(out))
    old = pg.evaluate("""(() => { const g = document.getElementById("g-trend"), t = g.querySelector(".hw-graph__title").textContent;
      return [g.querySelectorAll(".hw-graph__band, .hw-graph__limit, .hw-graph__limit-label, .hw-graph__band-label").length, g.querySelector(".hw-graph__tick").textContent,
              g.querySelectorAll(".hw-graph__baseline").length, g.querySelector(".hw-graph__plot").getAttribute("aria-label") === t + ". Use the left and right arrow keys to read each point.",
              g.querySelector(".hw-graph__readout").hasAttribute("data-tone"), g.querySelectorAll(".hw-graph__line[data-on]").length]; })()""")
    check('trend: an old-style trend (no new options) draws as before: zero-based, one lit line, no reference marks', old == [0, '0', 1, True, False, 1], str(old))
    sp = pg.evaluate("""(() => { const c = document.getElementById("g-trend").closest(".hw-card"), l = document.querySelector("#g-trend .hw-graph__line[data-on]"), out = [];
      for (const s of ["clinical", "research"]) { c.dataset.hwSpace = s; out.push(getComputedStyle(l).stroke); }
      delete c.dataset.hwSpace; out.push(getComputedStyle(l).stroke); return out; })()""")
    check('graphs: data-hw-space sets the highlight (clinical ink, research info blue)', sp == [INK, 'rgb(31, 85, 181)', EMERGENCY], str(sp))

    pgr = pg.evaluate('["#pager-first", "#pager-last"].map(s => [...document.querySelectorAll(s + " .hw-button")].map(b => b.disabled))')
    check('pager: Previous is disabled at the first patient, Next at the last', pgr == [[True, False], [False, True]], str(pgr))
    snap = pg.locator('#pager-first').aria_snapshot()
    check('pager: the K and J hints show but are not read out', 'button "Previous" [disabled]' in snap and 'button "Next"' in snap and css('#pager-first .hw-pager__key', 'display') != 'none', snap)

    dc = pg.evaluate("""(() => { const cs = getComputedStyle, d = document.getElementById("decision-high"), cells = [...d.querySelectorAll(".hw-decision__cell")], col = (s) => cs(document.querySelector(s)).color;
      const band = () => ["score", "band", "step"].map(e => col("#decision-high .hw-decision__" + e));
      const r = [cs(d).gridTemplateColumns.split(" ").length, new Set(cells.map(c => Math.round(c.getBoundingClientRect().top))).size, cs(cells[1]).boxShadow, band(),
                 ["score", "band", "step"].map(e => col("#decision-nr .hw-decision__" + e)), [col("#decision-nr [data-stale]"), cs(document.querySelector("#decision-nr [data-stale]")).fontWeight],
                 cs(d.querySelector(".hw-decision__model")).backgroundColor];
      d.dataset.band = "medium"; r.push(band()); d.dataset.band = "high"; return r; })()""")
    check('decision: four cells in one row from 1200px, divided by subtle lines', dc[0] == 4 and dc[1] == 1 and GREY in dc[2], str(dc[:3]))
    check('decision: the band colours score, band word and next step (high red, medium urgent, not ranked ink)', dc[3] == [EMERGENCY] * 3 and dc[7] == [URGENT_TEXT] * 3 and dc[4] == [INK] * 3, str([dc[3], dc[7], dc[4]]))
    check('decision: a stale source is red and bold; the model note is info-tinted', dc[5] == [EMERGENCY, '700'] and dc[6] == 'rgb(238, 243, 254)', str(dc[5:7]))

    ab = pg.evaluate("""(() => { const b = [...document.querySelectorAll("#actionbar .hw-button")].map(e => e.getBoundingClientRect()), c = getComputedStyle(document.getElementById("actionbar"));
      return [Math.abs(b[0].width - 2 * b[1].width) < 3, Math.abs(b[1].width - b[2].width) < 0.5, b.map(r => r.height), c.position, c.paddingBottom, c.borderTopColor]; })()""")
    check('actionbar: the first action takes two shares, 48px buttons, sticky with a top border', ab[0] and ab[1] and ab[2] == [48] * 3 and ab[3:] == ['sticky', '10px', GREY], str(ab))

    pg.evaluate('window.hw.destroy()')
    trig.click()
    check('destroy(): listeners removed', trig.get_attribute('aria-expanded') == 'false')

    # ---- Accessibility + screenshots per mode ----
    for label, q in [('light', ''), ('dark', '?theme=dark'), ('cloud', '?family=cloud&density=compact')]:
        pg = page_for(viewport={'width': 1280, 'height': 900}, reduced_motion='reduce')  # stable captures
        pg.goto(BASE + q); pg.wait_for_function('window.hw'); pg.wait_for_timeout(300)
        if label == 'dark':
            sp = []
            for s in ('clinical', 'research', ''):  # reduced motion leaves a 0.01ms transition on everything: let it end
                pg.evaluate('(s) => { const c = document.getElementById("g-trend").closest(".hw-card"); if (s) c.dataset.hwSpace = s; else delete c.dataset.hwSpace; }', s)
                pg.wait_for_timeout(50); sp.append(pg.evaluate('getComputedStyle(document.querySelector("#g-trend .hw-graph__line[data-on]")).stroke'))
            check('dark: data-hw-space highlights in light ink and light info blue', sp == ['rgb(243, 243, 243)', 'rgb(157, 189, 252)', 'rgb(238, 105, 111)'], str(sp))
        axe(pg, label)
        pg.screenshot(path=str(OUT / f'desktop-{label}.png'), full_page=True)

    # ---- Phone ----
    phone = dict(viewport={'width': 390, 'height': 844}, device_scale_factor=2, is_mobile=True, has_touch=True, reduced_motion='reduce')
    pg = page_for(**phone)
    pg.goto(BASE); pg.wait_for_function('window.hw'); pg.wait_for_timeout(300)
    check('mobile: app bar visible, desktop header hidden', pg.locator('.hw-appbar').is_visible() and not pg.locator('.hw-header').is_visible())
    check('mobile: bottom nav visible', pg.locator('.hw-bottomnav').is_visible())
    h = pg.evaluate('getComputedStyle(document.querySelector(".hw-button:not(.hw-button--sm):not(.hw-button--lg)")).height')
    check('mobile: touch targets ≥44px', h == '44px', h)
    check('mobile: no horizontal overflow', pg.evaluate('document.documentElement.scrollWidth <= innerWidth'), str(pg.evaluate('[document.documentElement.scrollWidth, innerWidth]')))
    ns = pg.evaluate('(() => { const c = getComputedStyle(document.querySelector("#shift")); return [c.height, c.fontSize]; })()')
    check('mobile: native select is a 44px target with 16px text', ns == ['44px', '16px'], str(ns))
    check('mobile: table stacks into cards', pg.evaluate('getComputedStyle(document.querySelector(".hw-table--stack tr")).display') == 'block')
    ph = pg.evaluate("""(() => { const cs = getComputedStyle, row = document.querySelector("#list-comfortable .hw-ranklist__row"), ack = row.querySelector(".hw-ranklist__action .hw-button"), f = document.getElementById("filter");
      return [cs(document.querySelector("#list-comfortable .hw-ranklist__head")).display, cs(row).borderTopLeftRadius, cs(row).borderTopColor,
              Math.round(ack.getBoundingClientRect().width) === Math.round(row.clientWidth - 28), cs(ack).height,
              cs(f).flexWrap, f.scrollWidth > f.clientWidth, document.querySelector("#score-high .hw-score__value").getBoundingClientRect().width]; })()""")
    check('mobile: ranked rows become cards, the alert card edged in red', ph[:3] == ['none', '16px', 'rgb(199, 0, 11)'], str(ph))
    check('mobile: Acknowledge is full width and 44px', ph[3] and ph[4] == '44px', str(ph))
    check('mobile: filter chips stay on one row that scrolls sideways', ph[5] == 'nowrap' and ph[6], str(ph))
    check('mobile: score tile is 54px', ph[7] == 54, str(ph))
    pg.locator('#bell-live').scroll_into_view_if_needed(); pg.locator('#bell-live').tap(); pg.wait_for_timeout(200)
    tg = pg.evaluate('(() => { const t = document.getElementById("tray-live").getBoundingClientRect(); return [t.left, t.top, t.width, getComputedStyle(document.querySelector("#tray-live .hw-button")).height]; })()')
    check('mobile: tray is a full-width panel under the app bar with 44px buttons', tg == [0, 56, 390, '44px'], str(tg))
    pg.locator('#tray-live .hw-close').tap(); pg.wait_for_function('document.getElementById("tray-live").hidden')
    axe(pg, 'phone')
    pg.screenshot(path=str(OUT / 'phone-light.png'), full_page=True)
    pg.locator('.hw-appbar [data-hw-open="nav-drawer"]').tap(); pg.wait_for_timeout(400)
    check('mobile: hamburger opens nav drawer', pg.evaluate('document.getElementById("nav-drawer").open'))
    pg.screenshot(path=str(OUT / 'phone-nav.png'))
    pg.keyboard.press('Escape'); pg.wait_for_function('!document.getElementById("nav-drawer").open')
    pg.locator('[data-hw-open="discharge"]').scroll_into_view_if_needed(); pg.locator('[data-hw-open="discharge"]').tap(); pg.wait_for_timeout(400)
    box = pg.evaluate('(() => { const r = document.getElementById("discharge").getBoundingClientRect(); return [r.width, r.height]; })()')
    check('mobile: dialog goes full screen', box[0] >= 389 and box[1] >= 843, str(box))
    pg.screenshot(path=str(OUT / 'phone-dialog.png'))
    pg.keyboard.press('Escape'); pg.wait_for_function('!document.getElementById("discharge").open')
    pg.locator('[data-hw-open="sheet"]').tap(); pg.wait_for_timeout(400)
    pg.screenshot(path=str(OUT / 'phone-sheet.png'))
    hdr = pg.locator('#sheet .hw-dialog__header').bounding_box()
    sx, sy = hdr['x'] + 40, hdr['y'] + hdr['height'] / 2
    pg.evaluate(f'''(() => {{ const h = document.querySelector("#sheet .hw-dialog__header");
      const ev = (t, y) => h.dispatchEvent(new PointerEvent(t, {{ bubbles: true, pointerId: 7, pointerType: "touch", clientX: {sx}, clientY: y }}));
      ev("pointerdown", {sy}); const sheet = document.getElementById("sheet");
      sheet.dispatchEvent(new PointerEvent("pointermove", {{ pointerId: 7, pointerType: "touch", clientY: {sy} + 140 }}));
      sheet.dispatchEvent(new PointerEvent("pointerup", {{ pointerId: 7, pointerType: "touch", clientY: {sy} + 140 }})); }})()''')
    pg.wait_for_function('!document.getElementById("sheet").open', timeout=2000)
    check('mobile: swipe down closes the bottom sheet', True)
    pg.evaluate('document.querySelector("#g-trend").scrollIntoView()'); pg.wait_for_timeout(300)
    check('mobile: graphs fit the phone width', pg.evaluate('[...document.querySelectorAll(".hw-graph")].every(g => g.getBoundingClientRect().right <= innerWidth && g.scrollWidth <= g.clientWidth + 1)'))
    check('mobile: trend drops end labels and keeps the keys', pg.locator('#g-trend .hw-graph__end').count() == 0 and pg.locator('#g-trend .hw-graph__key').count() == 3)
    nar = pg.evaluate('(() => { const g = document.getElementById("g-hr"); return [g.querySelectorAll(".hw-graph__limit-label--inside").length, g.querySelectorAll(".hw-graph__band-label--inside").length, g.querySelectorAll(".hw-graph__end").length]; })()')
    check('mobile: a narrow trend names its limits and band inside the plot', nar == [3, 1, 0], str(nar))
    mp = pg.evaluate("""(() => { const cs = getComputedStyle, p = document.getElementById("pager-first"), pos = p.querySelector(".hw-pager__position").getBoundingClientRect(),
        b = [...p.querySelectorAll(".hw-button")].map(e => e.getBoundingClientRect()), d = [...document.querySelectorAll("#decision-high .hw-decision__cell")].map(c => c.getBoundingClientRect());
      return [cs(p.querySelector(".hw-pager__key")).display, pos.bottom <= b[0].top, Math.abs(b[0].width - b[1].width) < 0.5, cs(document.getElementById("decision-high")).gridTemplateColumns.split(" ").length,
              d.every(r => Math.round(r.left) === Math.round(d[0].left)), [...document.querySelectorAll("#actionbar .hw-button")].map(e => e.getBoundingClientRect().height)]; })()""")
    check('mobile: pager hides the key hints, position above two equal buttons', mp[0] == 'none' and mp[1] and mp[2], str(mp))
    check('mobile: decision strip stacks into one column', mp[3] == 1 and mp[4], str(mp))
    check('mobile: action bar buttons stay 48px on touch', mp[5] == [48] * 3, str(mp))
    pg.locator('#g-columns .hw-graph__column').nth(5).tap()
    check('mobile: tapping a column reads it', pg.locator('#g-columns .hw-graph__label').inner_text() == 'alerts on 8 Nov')
    pg.locator('#toast-btn').tap(); pg.wait_for_timeout(300)
    tb = pg.evaluate('document.querySelector(".hw-toast").getBoundingClientRect().bottom')
    check('mobile: toast sits at the bottom', tb > 700, str(tb))

    rm = page_for(viewport={'width': 1280, 'height': 900}, reduced_motion='reduce')
    rm.goto(BASE); rm.wait_for_function('window.hw')
    check('reduced motion: nothing is hidden for reveal', rm.evaluate('document.querySelectorAll("[data-hw-reveal-state]").length') == 0)
    rm.locator('[data-hw-open="discharge"]').click(); rm.keyboard.press('Escape')
    check('reduced motion: dialog closes instantly', not rm.evaluate('document.getElementById("discharge").open'))

    pg = page_for(**phone); pg.goto(BASE + '?theme=dark'); pg.wait_for_function('window.hw'); pg.wait_for_timeout(300)
    axe(pg, 'phone dark'); pg.screenshot(path=str(OUT / 'phone-dark.png'), full_page=True)

    check('no JS errors', not errors, '; '.join(errors[:5]))
    browser.close()

server.shutdown()
for name, ok in results: print(('PASS ' if ok else 'FAIL ') + name)
print(f'\n{sum(ok for _, ok in results)}/{len(results)} passed')
for f in failures: print('  ✗', f[:600])
sys.exit(1 if failures else 0)
