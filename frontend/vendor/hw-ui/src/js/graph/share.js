// Share: parts of a whole as one bar, instead of a pie. Use for up to five parts, largest first.
//
//   share(el, {
//     title: 'Most alerts start with a vital sign', subtitle: 'What raised each alert on Ward W4 this week',
//     parts: [{ name: 'Vital signs', value: 29 }, { name: 'Lab results', value: 13 }, { name: 'Nurse concern', value: 5 }],
//     highlight: 0, noun: 'alerts',
//     label: (part) => `of alerts came from ${part.name.toLowerCase()}`,   // optional
//   })
import { frame, el, formatter, selectable } from './frame.js';

export function share(root, config) {
  const { parts } = config;
  const fmt = formatter(config);
  const total = parts.reduce((a, p) => a + p.value, 0) || 1;
  const pct = (p) => `${Math.round((p.value / total) * 100)}%`;
  const f = frame(root, 'share', config);

  const group = el('div', 'hw-graph__parts', f.plot);
  group.setAttribute('role', 'group');
  group.setAttribute('aria-labelledby', f.titleId);
  const buttons = parts.map((p) => {
    const part = el('button', 'hw-graph__part', group);
    part.type = 'button';
    part.style.setProperty('--hw-graph-share', p.value);
    el('span', 'hw-graph__part-bar', part);
    el('span', 'hw-graph__part-value', part, pct(p));
    part.append(' ');
    el('span', 'hw-graph__part-name', part, p.name);
    return part;
  });
  selectable(group, buttons, {
    selected: config.highlight ?? 0,
    onSelect: (i) => f.say({
      value: pct(parts[i]),
      label: config.label ? config.label(parts[i]) : parts[i].name,
      detail: `${fmt(parts[i].value)} of ${fmt(total)}${config.noun ? ` ${config.noun}` : ''}`,
    }),
  });
  f.hint(config.hint ?? 'One bar is the whole. Point at a part, tap it, or use the arrow keys');
  return { destroy() { root.replaceChildren(); } };
}
