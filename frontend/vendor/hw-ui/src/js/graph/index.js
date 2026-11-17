// Graph components. Each takes (element, config) and returns { destroy() }.
// Declarative use: <div data-hw-graph="trend"><script type="application/json">{ …config… }</script></div>
// (function options such as label or format need the JS call).
import { trend } from './trend.js';
import { bars } from './bars.js';
import { columns } from './columns.js';
import { steps } from './steps.js';
import { range } from './range.js';
import { share } from './share.js';
import { stat } from './stat.js';

export { trend, bars, columns, steps, range, share, stat };
const TYPES = { trend, bars, columns, steps, range, share, stat };

export function init(scope = document) {
  const made = [];
  scope.querySelectorAll('[data-hw-graph]').forEach((node) => {
    const json = node.querySelector(':scope > script[type="application/json"]');
    const make = TYPES[node.dataset.hwGraph];
    if (json && make) made.push(make(node, JSON.parse(json.textContent)));
  });
  return () => made.forEach((g) => g.destroy());
}
