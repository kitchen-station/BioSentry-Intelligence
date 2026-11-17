// Entry point. import { initAll, toast } from './js/index.js'; const destroy = initAll();
import * as select from './select.js';
import * as menu from './menu.js';
import * as tabs from './tabs.js';
import * as dialog from './dialog.js';
import * as tooltip from './tooltip.js';
import * as slider from './slider.js';
import * as reveal from './reveal.js';
import * as graph from './graph/index.js';

export { toast } from './toast.js';
export { openDialog, closeDialog } from './dialog.js';
export { leave, cancelLeave, reducedMotion } from './core/motion.js';
export { trend, bars, columns, steps, range, share, stat } from './graph/index.js';

const modules = { select, menu, tabs, dialog, tooltip, slider, reveal, graph };

/** Wire every component. Returns one function that removes all listeners (call it on SPA route teardown). */
export function initAll(scope = document) {
  const cleanups = Object.values(modules).map((m) => m.init(scope));
  return () => cleanups.forEach((fn) => fn());
}
