// Toasts. Usage: toast('Saved'); toast({ message: 'Acknowledged bed 4', tone: 'success', action: { label: 'Undo', onClick } });
// One live region for the whole page so screen readers announce each message once.
import { leave } from './core/motion.js';

let region;

function getRegion() {
  if (!region || !region.isConnected) {
    region = document.createElement('div');
    region.className = 'hw-toast-region';
    region.setAttribute('role', 'status');
    region.setAttribute('aria-live', 'polite');
    document.body.append(region);
  }
  return region;
}

// Font Awesome Free classes. Pass { icon: 'fa-solid fa-bed-pulse' } to override per toast.
const ICONS = { success: 'fa-solid fa-circle-check', danger: 'fa-solid fa-circle-xmark', warning: 'fa-solid fa-triangle-exclamation', info: 'fa-solid fa-circle-info' };

export function toast(input) {
  const { message, tone, icon: iconClass, action, timeout = 4000 } = typeof input === 'string' ? { message: input } : input;
  const el = document.createElement('div');
  el.className = 'hw-toast';

  const glyph = iconClass ?? ICONS[tone];
  if (glyph) {
    const icon = document.createElement('i');
    icon.className = `hw-icon hw-toast__icon ${tone ? `hw-toast__icon--${tone}` : ''} ${glyph}`;
    icon.setAttribute('aria-hidden', 'true');
    el.append(icon);
  }
  const text = document.createElement('span');
  text.textContent = message; // textContent, never innerHTML: messages may contain user data
  el.append(text);

  let timer;
  let gone = false;
  const dismiss = () => {
    if (gone) return;
    gone = true;
    clearTimeout(timer);
    leave(el).then(() => el.remove());
  };
  const start = () => { if (timeout > 0) timer = setTimeout(dismiss, timeout); };

  if (action) {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'hw-toast__action';
    btn.textContent = action.label;
    btn.addEventListener('click', () => { action.onClick?.(); dismiss(); });
    el.append(btn);
  }
  // Pause while the user is reading or about to act
  el.addEventListener('pointerenter', () => clearTimeout(timer));
  el.addEventListener('pointerleave', start);
  el.addEventListener('focusin', () => clearTimeout(timer));

  getRegion().append(el);
  start();
  return dismiss;
}
