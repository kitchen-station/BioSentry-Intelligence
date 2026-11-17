// Tabs (WAI-ARIA APG "Tabs, automatic activation").
// Markup:
// <div class="hw-tabs" data-hw-tabs>
//   <div class="hw-tabs__list" role="tablist" aria-label="Views">
//     <button class="hw-tabs__tab" role="tab" aria-selected="true" aria-controls="p-ward" id="t-ward">Ward</button>
//   </div>
//   <div class="hw-tabs__panel" role="tabpanel" id="p-ward" aria-labelledby="t-ward">…</div>
// </div>
// Emits: "hw:tabchange" on .hw-tabs with detail { tab }.
import { on, cleanupAll, rovingIndex } from './core/dom.js';

// Move the list's single sliding indicator under the selected tab.
function placeIndicator(list) {
  const tab = list.querySelector('[aria-selected="true"]');
  if (!tab || !tab.offsetWidth) return;
  list.style.setProperty('--hw-tab-indicator-x', `${tab.offsetLeft}px`);
  list.style.setProperty('--hw-tab-indicator-w', `${tab.offsetWidth}px`);
  list.dataset.hwIndicator = '';
}

function select(tab, { focus = true } = {}) {
  const list = tab.closest('[role="tablist"]');
  list.querySelectorAll('[role="tab"]').forEach((t) => {
    const selected = t === tab;
    t.setAttribute('aria-selected', String(selected));
    t.tabIndex = selected ? 0 : -1;
    const panel = document.getElementById(t.getAttribute('aria-controls'));
    if (panel) panel.hidden = !selected;
  });
  placeIndicator(list);
  if (focus) tab.focus();
  tab.scrollIntoView({ block: 'nearest', inline: 'nearest' });
  tab.closest('[data-hw-tabs]')?.dispatchEvent(new CustomEvent('hw:tabchange', { bubbles: true, detail: { tab } }));
}

export function init(scope = document) {
  const lists = [...scope.querySelectorAll('[data-hw-tabs] [role="tablist"]')];
  lists.forEach((list) => {
    const current = list.querySelector('[aria-selected="true"]') ?? list.querySelector('[role="tab"]');
    if (current) select(current, { focus: false });
  });
  // Re-place on resize, font load, or when a hidden tab set becomes visible.
  const ro = new ResizeObserver((entries) => entries.forEach((e) => placeIndicator(e.target)));
  lists.forEach((list) => ro.observe(list));

  return cleanupAll([
    () => ro.disconnect(),
    on(document, 'click', (e) => {
      const tab = e.target.closest('[data-hw-tabs] [role="tab"]');
      if (tab) select(tab);
    }),
    on(document, 'keydown', (e) => {
      const tab = e.target.closest?.('[data-hw-tabs] [role="tab"]');
      if (!tab || !['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(e.key)) return;
      e.preventDefault();
      const tabs = [...tab.closest('[role="tablist"]').querySelectorAll('[role="tab"]')];
      select(tabs[rovingIndex(e.key, tabs.indexOf(tab), tabs.length)]);
    }),
  ]);
}
