// Kleine Helfer: Formatierung, DOM-Bau, Escaping.

export function bytes(n) {
  if (!n) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB'];
  let i = 0, v = Number(n);
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
  return (i === 0 ? v.toFixed(0) : v.toFixed(v < 10 ? 1 : 0)) + ' ' + units[i];
}

export function duration(seconds) {
  if (seconds == null) return '—';
  const s = Math.round(seconds);
  if (s < 60) return s + ' s';
  if (s < 3600) return Math.floor(s / 60) + ' min ' + (s % 60) + ' s';
  return Math.floor(s / 3600) + ' h ' + Math.floor((s % 3600) / 60) + ' min';
}

export function date(iso, withSeconds = false) {
  if (!iso) return '—';
  const d = new Date(iso);
  if (isNaN(d)) return '—';
  return d.toLocaleString('de-DE', {
    day: '2-digit', month: '2-digit', year: 'numeric',
    hour: '2-digit', minute: '2-digit',
    ...(withSeconds ? { second: '2-digit' } : {}),
  });
}

export function timeOnly(iso) {
  const d = new Date(iso);
  return isNaN(d) ? '—' : d.toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit' });
}

export function ago(iso) {
  if (!iso) return 'nie';
  const diff = (Date.now() - new Date(iso).getTime()) / 1000;
  if (isNaN(diff)) return '—';
  if (diff < 60) return 'gerade eben';
  if (diff < 3600) return `vor ${Math.floor(diff / 60)} min`;
  if (diff < 86400) return `vor ${Math.floor(diff / 3600)} h`;
  const days = Math.floor(diff / 86400);
  if (days < 30) return `vor ${days} ${days === 1 ? 'Tag' : 'Tagen'}`;
  return date(iso).split(',')[0];
}

export function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));
}

/** Tagged template, das interpolierte Werte automatisch escaped. */
export function html(strings, ...values) {
  return strings.reduce((out, str, i) => {
    let v = values[i - 1];
    if (Array.isArray(v)) v = v.join('');
    else if (v === null || v === undefined || v === false) v = '';
    else if (typeof v === 'object' && '__raw' in v) v = v.__raw ?? '';
    else v = esc(v);
    return out + v + str;
  });
}

export const raw = (s) => ({ __raw: s });

export function el(selector, root = document) { return root.querySelector(selector); }
export function els(selector, root = document) { return [...root.querySelectorAll(selector)]; }

/** Delegierter Klick-Handler: on(root, '[data-act="x"]', (elem, event) => …) */
export function on(root, selector, handler, type = 'click') {
  root.addEventListener(type, (event) => {
    const target = event.target.closest(selector);
    if (target && root.contains(target)) handler(target, event);
  });
}

export function statusClass(status) {
  return { completed: 'ok', running: 'info', partial: 'warn',
           failed: 'err', cancelled: 'warn', error: 'err', warn: 'warn' }[status] || '';
}

export function statusLabel(status) {
  return { completed: 'Erfolgreich', running: 'Läuft', partial: 'Teilweise',
           failed: 'Fehlgeschlagen', cancelled: 'Abgebrochen' }[status] || status;
}

export function stateLabel(state) {
  return { running: 'Läuft', exited: 'Gestoppt', created: 'Erstellt',
           paused: 'Pausiert', restarting: 'Neustart', dead: 'Tot' }[state] || state;
}

export function cronText(expression) {
  const presets = {
    '0 3 * * *': 'Täglich um 03:00 Uhr',
    '0 2 * * *': 'Täglich um 02:00 Uhr',
    '0 4 * * 0': 'Sonntags um 04:00 Uhr',
    '0 */6 * * *': 'Alle 6 Stunden',
    '0 0 1 * *': 'Am 1. jedes Monats',
  };
  if (presets[expression]) return presets[expression];
  const parts = String(expression).trim().split(/\s+/);
  if (parts.length !== 5) return expression;
  const [min, hour, dom, , dow] = parts;
  if (dom === '*' && dow === '*' && /^\d+$/.test(hour) && /^\d+$/.test(min)) {
    return `Täglich um ${hour.padStart(2, '0')}:${min.padStart(2, '0')} Uhr`;
  }
  const days = { 0: 'Sonntag', 1: 'Montag', 2: 'Dienstag', 3: 'Mittwoch',
                 4: 'Donnerstag', 5: 'Freitag', 6: 'Samstag' };
  if (dom === '*' && days[dow] && /^\d+$/.test(hour)) {
    return `${days[dow]}s um ${hour.padStart(2, '0')}:${String(min).padStart(2, '0')} Uhr`;
  }
  return expression;
}

export function shortImage(image) {
  if (!image) return '—';
  return image.replace(/^(ghcr\.io|docker\.io|lscr\.io)\//, '');
}
