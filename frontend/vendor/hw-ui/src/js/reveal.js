// Scroll reveal. Markup: <section data-hw-reveal>…</section>, or data-hw-reveal="stagger" on a parent
// to reveal its children one after another. Styles: utilities/motion.css.
// Only elements still below the fold are hidden, so nothing visible at load flashes, and with
// reduced motion or no IntersectionObserver this does nothing at all.
import { reducedMotion } from './core/motion.js';

export function init(scope = document) {
  if (reducedMotion() || !('IntersectionObserver' in window)) return () => {};

  const io = new IntersectionObserver((entries) => {
    let index = 0; // stagger only what arrives together
    entries.forEach((entry) => {
      if (!entry.isIntersecting) return;
      entry.target.style.setProperty('--hw-reveal-index', String(index++));
      entry.target.dataset.hwRevealState = 'in';
      io.unobserve(entry.target);
    });
  }, { rootMargin: '0px 0px -8% 0px' });

  scope.querySelectorAll('[data-hw-reveal]').forEach((el) => {
    const targets = el.dataset.hwReveal === 'stagger' ? [...el.children] : [el];
    targets.forEach((t) => {
      if (t.dataset.hwRevealState) return; // already handled by an earlier init()
      if (t.getBoundingClientRect().top < window.innerHeight) return;
      t.dataset.hwRevealState = 'out';
      io.observe(t);
    });
  });
  return () => io.disconnect();
}
