// Keeps the slider fill (--hw-slider-value) and its <output> in sync.
// Markup: <div class="hw-slider-field"><input type="range" class="hw-slider" id="vol" …><output for="vol" class="hw-slider-field__value"></output></div>
import { on } from './core/dom.js';

function sync(input) {
  const min = Number(input.min || 0);
  const max = Number(input.max || 100);
  const pct = ((Number(input.value) - min) / (max - min)) * 100;
  input.style.setProperty('--hw-slider-value', String(pct));
  const output = input.parentElement?.querySelector(`output[for="${input.id}"]`);
  if (output) output.value = input.value;
}

export function init(scope = document) {
  scope.querySelectorAll('.hw-slider').forEach(sync);
  return on(document, 'input', (e) => {
    if (e.target.matches?.('.hw-slider')) sync(e.target);
  });
}
